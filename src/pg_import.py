#!/usr/bin/env python3
"""
pg_import.py
────────────────────────────────────────────────────────────────────────────
Importa un archivo DXF a PostgreSQL/PostGIS.

Inserta en:
  dwg.planos     — un registro por archivo (clave: nombre)
  dwg.entidades  — todas las geometrías del DXF
  dwg.recintos   — habitaciones / locales detectados automáticamente

Uso:
  python pg_import.py plano.dxf --nombre "Edificio X Nivel 1"
  python pg_import.py plano.dxf --nombre "X" --capas-texto "TX . 01" "ÁREAS"
  python pg_import.py plano.dxf --nombre "X" --reimportar
  python pg_import.py plano.dxf --nombre "X" --solo-recintos
  python pg_import.py plano.dxf --nombre "X" --crs EPSG:6372
"""

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

try:
    import psycopg2
    from psycopg2.extras import execute_batch
except ImportError:
    print("Dependencia faltante: pip install psycopg2-binary")
    sys.exit(1)

from dxf_to_gis import DxfReader, GeometryConverter
from detectar_recintos import detectar_recintos

DSN_DEFAULT = "host=localhost port=5433 dbname=dwg user=dwg password=dwg_pass"
BATCH_SIZE  = 500


# ── helpers ───────────────────────────────────────────────────────────────────

def _safe(val):
    """Convierte NaN/NaT a None para psycopg2."""
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    return val


# ── crear / resetear plano ────────────────────────────────────────────────────

def upsert_plano(cur, nombre: str, archivo_dxf: str, crs: str, reimportar: bool) -> int:
    """
    Retorna el plano_id.
    Si ya existe y reimportar=True, borra las entidades y recintos (CASCADE).
    Si ya existe y reimportar=False, lanza un error.
    """
    cur.execute("SELECT id FROM dwg.planos WHERE nombre = %s", (nombre,))
    row = cur.fetchone()

    if row:
        plano_id = row[0]
        if not reimportar:
            raise ValueError(
                f"El plano '{nombre}' ya existe (id={plano_id}). "
                "Usa --reimportar para reemplazarlo."
            )
        cur.execute("DELETE FROM dwg.entidades WHERE plano_id = %s", (plano_id,))
        cur.execute("DELETE FROM dwg.recintos   WHERE plano_id = %s", (plano_id,))
        cur.execute(
            "UPDATE dwg.planos SET archivo_dxf=%s, crs=%s, procesado_en=NOW() WHERE id=%s",
            (archivo_dxf, crs, plano_id),
        )
        log.info("Plano '%s' reseteado (id=%d).", nombre, plano_id)
        return plano_id

    cur.execute(
        """INSERT INTO dwg.planos (nombre, archivo_dxf, crs)
           VALUES (%s, %s, %s) RETURNING id""",
        (nombre, archivo_dxf, crs),
    )
    plano_id = cur.fetchone()[0]
    log.info("Plano '%s' creado (id=%d).", nombre, plano_id)
    return plano_id


# ── importar entidades ────────────────────────────────────────────────────────

def importar_entidades(cur, plano_id: int, reader: DxfReader) -> int:
    converter = GeometryConverter()
    rows = []
    skipped = 0

    for entity in reader.entities():
        geom, attrs = converter.convert(entity)
        if geom is None:
            skipped += 1
            continue
        try:
            wkt = geom.wkt
        except Exception:
            skipped += 1
            continue

        rows.append((
            plano_id,
            attrs.get("tipo"),
            attrs.get("layer"),
            attrs.get("handle"),
            attrs.get("color"),
            attrs.get("texto"),
            attrs.get("bloque"),
            attrs.get("radio"),
            wkt,
        ))

    if rows:
        execute_batch(
            cur,
            """INSERT INTO dwg.entidades
               (plano_id, tipo, layer, handle, color, texto, bloque, radio, geom)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, ST_GeomFromText(%s, 0))""",
            rows,
            page_size=BATCH_SIZE,
        )

    log.debug("Entidades omitidas (sin geometría): %d", skipped)
    return len(rows)


# ── importar recintos ─────────────────────────────────────────────────────────

def importar_recintos(
    cur,
    plano_id: int,
    reader: DxfReader,
    capas_texto: list[str] | None = None,
) -> int:
    gdf = detectar_recintos(reader, capas_texto=capas_texto)
    if gdf.empty:
        return 0

    rows = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        rows.append((
            plano_id,
            _safe(row.get("nombre")),
            _safe(row.get("categoria")),
            _safe(row.get("icono")),
            _safe(row.get("area_m2")),
            _safe(row.get("confianza")),
            _safe(row.get("capa_recinto")),
            _safe(row.get("capa_texto")),
            _safe(row.get("metodo")),
            _safe(row.get("x")),
            _safe(row.get("y")),
            geom.wkt,
        ))

    if rows:
        execute_batch(
            cur,
            """INSERT INTO dwg.recintos
               (plano_id, nombre, categoria, icono, area_m2, confianza,
                capa_recinto, capa_texto, metodo, x, y, geom)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       ST_GeomFromText(%s, 0))""",
            rows,
            page_size=BATCH_SIZE,
        )

    return len(rows)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Importa un DXF a PostGIS.")
    parser.add_argument("dxf",          help="Ruta al archivo .dxf")
    parser.add_argument("--nombre",     required=True, help="Nombre del plano / proyecto")
    parser.add_argument("--dsn",        default=DSN_DEFAULT, help="Cadena de conexión PostgreSQL")
    parser.add_argument("--crs",        default="unknown", help="CRS del plano, ej. EPSG:6372")
    parser.add_argument(
        "--capas-texto", nargs="*", metavar="CAPA", dest="capas_texto",
        help='Capas con etiquetas de espacio. Ej: "TX . 01" "ÁREAS"',
    )
    parser.add_argument(
        "--reimportar", action="store_true",
        help="Borra las entidades y recintos existentes del plano antes de reimportar",
    )
    parser.add_argument(
        "--solo-entidades", action="store_true",
        help="Solo importa entidades geométricas (omite detección de recintos)",
    )
    parser.add_argument(
        "--solo-recintos", action="store_true",
        help="Solo importa recintos (omite las entidades brutas)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    dxf_path = Path(args.dxf)
    if not dxf_path.exists():
        log.error("Archivo no encontrado: %s", dxf_path)
        sys.exit(1)

    log.info("Conectando a PostgreSQL...")
    try:
        conn = psycopg2.connect(args.dsn)
    except Exception as e:
        log.error("No se pudo conectar: %s", e)
        log.error("¿Está corriendo el contenedor?  docker compose up -d")
        sys.exit(1)

    conn.autocommit = False
    cur = conn.cursor()

    try:
        reader = DxfReader(dxf_path)
        s = reader.summary()
        log.info("DXF: %s  (%d entidades, %d capas)", s["archivo"], s["entidades_total"], s["capas"])

        plano_id = upsert_plano(
            cur,
            nombre=args.nombre,
            archivo_dxf=dxf_path.name,
            crs=args.crs,
            reimportar=args.reimportar,
        )

        n_entidades = 0
        n_recintos  = 0

        if not args.solo_recintos:
            log.info("Importando entidades...")
            n_entidades = importar_entidades(cur, plano_id, reader)
            log.info("  %d entidades insertadas.", n_entidades)

        if not args.solo_entidades:
            log.info("Detectando recintos...")
            n_recintos = importar_recintos(cur, plano_id, reader, args.capas_texto)
            log.info("  %d recintos insertados.", n_recintos)

        cur.execute(
            "UPDATE dwg.planos SET n_entidades=%s, n_recintos=%s WHERE id=%s",
            (n_entidades, n_recintos, plano_id),
        )

        conn.commit()

        # ── Resumen final ──────────────────────────────────────────────────────
        cur.execute(
            """SELECT categoria, COUNT(*) AS n, ROUND(SUM(area_m2)::NUMERIC, 2) AS m2
               FROM dwg.recintos WHERE plano_id = %s
               GROUP BY categoria ORDER BY m2 DESC NULLS LAST""",
            (plano_id,),
        )
        rows = cur.fetchall()

        print(f"\n{'='*60}")
        print(f"  IMPORTACIÓN COMPLETADA — {args.nombre}")
        print(f"  plano_id : {plano_id}")
        print(f"{'='*60}")
        if rows:
            print(f"  {'Categoría':<30}  {'Recintos':>8}  {'m²':>10}")
            print(f"  {'─'*52}")
            for cat, n, m2 in rows:
                print(f"  {(cat or 'Otro'):<30}  {n:>8}  {m2 or 0:>10.2f}")
            print(f"  {'─'*52}")
            total_r = sum(r[1] for r in rows)
            total_m2 = sum(r[2] or 0 for r in rows)
            print(f"  {'TOTAL':<30}  {total_r:>8}  {total_m2:>10.2f}")
        else:
            print(f"  Sin recintos detectados.")
        print(f"\n  Entidades: {n_entidades}  |  Recintos: {n_recintos}")
        print(f"  Consulta: psql ... -c \"SELECT * FROM dwg.v_resumen WHERE id={plano_id};\"")
        print(f"{'='*60}\n")

    except ValueError as e:
        conn.rollback()
        log.error("%s", e)
        sys.exit(1)
    except Exception as e:
        conn.rollback()
        log.error("Error durante la importación: %s", e)
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
