#!/usr/bin/env python3
"""
Gestión de la base de datos de inventario de superficies.

Uso:
  python3 db.py importar                  # importa todos los JSON encontrados
  python3 db.py importar ruta/plano.json  # importa un archivo específico
  python3 db.py proyectos                 # lista proyectos
  python3 db.py nuevo-proyecto "Nombre"   # crea un proyecto
  python3 db.py asignar <plano_id> <proyecto_id>  # asigna plano a proyecto
  python3 db.py planos                    # lista planos con su proyecto
  python3 db.py resumen                   # totales por proyecto y planta
  python3 db.py ver [proyecto_id]         # detalle de recintos
"""

import sqlite3
import json
import sys
import re
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent / "inventario.db"

# Directorios donde buscar _inventario.json por defecto
SEARCH_DIRS = [
    Path(__file__).parent / "examples" / "dxf",
    Path(__file__).parent / "data" / "dxf",
]

# ── Schema ─────────────────────────────────────────────────────────────────────

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS proyectos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre      TEXT    NOT NULL UNIQUE,
    descripcion TEXT,
    creado_en   TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS planos (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    proyecto_id      INTEGER REFERENCES proyectos(id) ON DELETE SET NULL,
    archivo          TEXT    NOT NULL UNIQUE,   -- nombre base del .json sin sufijo
    nombre_planta    TEXT,                      -- extraído del nombre de archivo
    fecha_importacion TEXT   NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS recintos (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    plano_id     INTEGER NOT NULL REFERENCES planos(id) ON DELETE CASCADE,
    nombre       TEXT,
    categoria    TEXT,
    icono        TEXT,
    area_m2      REAL,
    confianza    TEXT,
    x            REAL,
    y            REAL,
    capa_texto   TEXT,
    capa_recinto TEXT,
    metodo       TEXT
);

CREATE VIEW IF NOT EXISTS v_recintos AS
SELECT
    r.id,
    COALESCE(p.nombre, '(sin proyecto)') AS proyecto,
    pl.nombre_planta                      AS planta,
    pl.archivo,
    r.nombre,
    r.categoria,
    r.area_m2,
    r.confianza,
    r.metodo,
    r.capa_texto,
    r.capa_recinto,
    r.x,
    r.y,
    r.icono
FROM recintos r
JOIN planos pl ON r.plano_id = pl.id
LEFT JOIN proyectos p ON pl.proyecto_id = p.id;
"""

# ── Helpers ────────────────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def extract_nombre_planta(archivo: str) -> str:
    """
    Infiere el nombre de la planta desde el nombre del archivo.
    Ej: '220609_BOR-101 - PLANTA BAJA' → 'PLANTA BAJA'
        "D'L-Nivel 1"                  → 'Nivel 1'
        "D'L-SOTANO"                   → 'SOTANO'
    """
    name = archivo
    # quitar prefijo de fecha YYMMDD_
    name = re.sub(r'^\d{6}_', '', name)
    # quitar prefijo tipo "XXX-NNN - "
    m = re.search(r' - (.+)$', name)
    if m:
        return m.group(1).strip()
    # quitar prefijo tipo "D'L-" o "ARQ-" o "BOR-"
    name = re.sub(r"^[A-Za-z']+[-_]", '', name).strip()
    return name or archivo


# ── Comandos ───────────────────────────────────────────────────────────────────

def cmd_importar(args):
    init_db()
    if args:
        json_files = [Path(a) for a in args]
    else:
        json_files = []
        for d in SEARCH_DIRS:
            json_files.extend(sorted(d.glob("*_inventario.json")))

    if not json_files:
        print("No se encontraron archivos *_inventario.json.")
        return

    importados = 0
    omitidos   = 0

    with get_conn() as conn:
        for jf in json_files:
            archivo = jf.stem.removesuffix("_inventario")
            # ¿ya existe?
            row = conn.execute("SELECT id FROM planos WHERE archivo = ?", (archivo,)).fetchone()
            if row:
                print(f"  ↩  Ya existe: {archivo}")
                omitidos += 1
                continue

            try:
                recintos = json.loads(jf.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  ✗  Error leyendo {jf.name}: {e}")
                continue

            nombre_planta = extract_nombre_planta(archivo)
            cur = conn.execute(
                "INSERT INTO planos (archivo, nombre_planta) VALUES (?, ?)",
                (archivo, nombre_planta),
            )
            plano_id = cur.lastrowid

            conn.executemany(
                """INSERT INTO recintos
                   (plano_id, nombre, categoria, icono, area_m2, confianza, x, y,
                    capa_texto, capa_recinto, metodo)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        plano_id,
                        r.get("nombre"),
                        r.get("categoria"),
                        r.get("icono"),
                        r.get("area_m2"),
                        r.get("confianza"),
                        r.get("x"),
                        r.get("y"),
                        r.get("capa_texto"),
                        r.get("capa_recinto"),
                        r.get("metodo"),
                    )
                    for r in recintos
                ],
            )
            print(f"  ✓  {archivo}  →  {len(recintos)} recintos  (plano_id={plano_id})")
            importados += 1

    print(f"\nImportados: {importados}  |  Omitidos: {omitidos}")
    print(f"Base de datos: {DB_PATH}")


def cmd_proyectos(_):
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, nombre, descripcion, creado_en FROM proyectos ORDER BY id"
        ).fetchall()
    if not rows:
        print("No hay proyectos todavía.")
        print("  Crea uno con:  python3 db.py nuevo-proyecto \"Nombre del proyecto\"")
        return
    print(f"{'ID':>4}  {'Nombre':<40}  {'Descripción':<30}  Creado")
    print("─" * 90)
    for r in rows:
        print(f"  {r['id']:>2}  {r['nombre']:<40}  {r['descripcion'] or '':<30}  {r['creado_en']}")


def cmd_nuevo_proyecto(args):
    if not args:
        print("Uso: python3 db.py nuevo-proyecto \"Nombre del proyecto\" [\"Descripción opcional\"]")
        sys.exit(1)
    init_db()
    nombre = args[0]
    descripcion = args[1] if len(args) > 1 else None
    with get_conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO proyectos (nombre, descripcion) VALUES (?, ?)",
                (nombre, descripcion),
            )
            print(f"✓  Proyecto creado  →  id={cur.lastrowid}  nombre='{nombre}'")
        except sqlite3.IntegrityError:
            print(f"✗  Ya existe un proyecto con ese nombre: '{nombre}'")


def cmd_asignar(args):
    if len(args) < 2:
        print("Uso: python3 db.py asignar <plano_id> <proyecto_id>")
        sys.exit(1)
    init_db()
    plano_id, proyecto_id = int(args[0]), int(args[1])
    with get_conn() as conn:
        proj = conn.execute("SELECT nombre FROM proyectos WHERE id=?", (proyecto_id,)).fetchone()
        plano = conn.execute("SELECT archivo FROM planos WHERE id=?", (plano_id,)).fetchone()
        if not proj:
            print(f"✗  No existe proyecto con id={proyecto_id}")
            sys.exit(1)
        if not plano:
            print(f"✗  No existe plano con id={plano_id}")
            sys.exit(1)
        conn.execute("UPDATE planos SET proyecto_id=? WHERE id=?", (proyecto_id, plano_id))
    print(f"✓  Plano {plano_id} '{plano['archivo']}' → proyecto '{proj['nombre']}'")


def cmd_planos(_):
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT pl.id, COALESCE(p.nombre,'(sin proyecto)') AS proyecto,
                      pl.nombre_planta, pl.archivo,
                      COUNT(r.id) AS recintos,
                      ROUND(SUM(r.area_m2),2) AS area_total
               FROM planos pl
               LEFT JOIN proyectos p ON pl.proyecto_id = p.id
               LEFT JOIN recintos r  ON r.plano_id = pl.id
               GROUP BY pl.id ORDER BY proyecto, pl.id"""
        ).fetchall()
    if not rows:
        print("No hay planos importados todavía.")
        print("  Importa con:  python3 db.py importar")
        return
    print(f"{'ID':>4}  {'Proyecto':<30}  {'Planta':<22}  {'Recintos':>8}  {'Área total m²':>13}")
    print("─" * 85)
    for r in rows:
        print(f"  {r['id']:>2}  {r['proyecto']:<30}  {r['nombre_planta']:<22}  {r['recintos']:>8}  {r['area_total'] or 0:>13.2f}")


def cmd_resumen(_):
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT COALESCE(p.nombre,'(sin proyecto)') AS proyecto,
                      r.categoria,
                      COUNT(r.id) AS recintos,
                      ROUND(SUM(r.area_m2),2) AS area_total,
                      ROUND(AVG(r.area_m2),2) AS area_prom
               FROM recintos r
               JOIN planos pl ON r.plano_id = pl.id
               LEFT JOIN proyectos p ON pl.proyecto_id = p.id
               GROUP BY proyecto, r.categoria
               ORDER BY proyecto, area_total DESC"""
        ).fetchall()
    if not rows:
        print("Sin datos.  python3 db.py importar")
        return
    curr_proj = None
    for r in rows:
        if r['proyecto'] != curr_proj:
            curr_proj = r['proyecto']
            print(f"\n▶  {curr_proj}")
            print(f"   {'Categoría':<35}  {'Recintos':>8}  {'Total m²':>10}  {'Prom m²':>8}")
            print("   " + "─" * 66)
        print(f"   {(r['categoria'] or '(sin cat.)'):<35}  {r['recintos']:>8}  {r['area_total'] or 0:>10.2f}  {r['area_prom'] or 0:>8.2f}")


def cmd_ver(args):
    init_db()
    with get_conn() as conn:
        if args:
            proyecto_id = int(args[0])
            rows = conn.execute(
                """SELECT v.* FROM v_recintos v
                   JOIN planos pl ON v.archivo = pl.archivo
                   WHERE pl.proyecto_id = ?
                   ORDER BY v.planta, v.categoria, v.nombre""",
                (proyecto_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM v_recintos ORDER BY proyecto, planta, categoria, nombre"
            ).fetchall()

    if not rows:
        print("Sin resultados.")
        return

    print(f"{'Proyecto':<25}  {'Planta':<20}  {'Nombre':<30}  {'Categoría':<25}  {'m²':>7}  {'Conf.':<6}")
    print("─" * 120)
    for r in rows:
        print(
            f"  {(r['proyecto'] or ''):<23}  {(r['planta'] or ''):<20}  "
            f"{(r['nombre'] or ''):<30}  {(r['categoria'] or ''):<25}  "
            f"{r['area_m2'] or 0:>7.2f}  {r['confianza'] or '':<6}"
        )


# ── Dispatch ───────────────────────────────────────────────────────────────────

COMMANDS = {
    "importar":       cmd_importar,
    "proyectos":      cmd_proyectos,
    "nuevo-proyecto": cmd_nuevo_proyecto,
    "asignar":        cmd_asignar,
    "planos":         cmd_planos,
    "resumen":        cmd_resumen,
    "ver":            cmd_ver,
}

AYUDA = """
Comandos disponibles:

  importar [archivo.json ...]     Importa JSON(s) a la base de datos
  proyectos                       Lista proyectos existentes
  nuevo-proyecto "Nombre" ["Desc"] Crea un proyecto
  asignar <plano_id> <proyecto_id> Asigna un plano a un proyecto
  planos                          Lista planos importados con totales
  resumen                         Totales de área por proyecto y categoría
  ver [proyecto_id]               Tabla completa de recintos
"""

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "ayuda"):
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]
    rest = sys.argv[2:]

    if cmd not in COMMANDS:
        print(f"Comando desconocido: '{cmd}'\n{AYUDA}")
        sys.exit(1)

    COMMANDS[cmd](rest)
