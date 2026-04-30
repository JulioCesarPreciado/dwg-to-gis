"""
dxf_to_gis.py
─────────────────────────────────────────────────────────────────────────────
Convierte archivos DXF a estructuras geoespaciales.

Clases exportables:
  DxfReader          — carga el DXF y expone sus entidades
  GeometryConverter  — convierte entidades a geometrías Shapely con atributos
  DxfExporter        — exporta GeoDataFrame a Shapefile / GeoJSON / GeoPackage / PostGIS

Uso como script:
  python dxf_to_gis.py plano.dxf --format geojson
  python dxf_to_gis.py plano.dxf --format gpkg --output plano.gpkg --crs EPSG:4326
  python dxf_to_gis.py plano.dxf --format shp --output capas/
  python dxf_to_gis.py plano.dxf --format postgis --dsn postgresql://dwg:pass@localhost:5433/dwg

Estructura de BD sugerida (PostGIS):
─────────────────────────────────────────────────────────────────────────────
  CREATE TABLE planos (
      id           SERIAL PRIMARY KEY,
      nombre       TEXT NOT NULL,
      archivo      TEXT NOT NULL UNIQUE,
      crs          TEXT DEFAULT 'unknown',
      importado_en TIMESTAMP DEFAULT NOW()
  );

  CREATE TABLE entidades (
      id        SERIAL PRIMARY KEY,
      plano_id  INTEGER REFERENCES planos(id) ON DELETE CASCADE,
      tipo      TEXT,          -- LINE, LWPOLYLINE, TEXT, INSERT, etc.
      layer     TEXT,
      handle    TEXT,
      color     INTEGER,
      texto     TEXT,          -- contenido si es TEXT/MTEXT
      bloque    TEXT,          -- nombre del bloque si es INSERT
      radio     NUMERIC,       -- radio si es CIRCLE
      atributos JSONB,         -- cualquier atributo extra
      geom      GEOMETRY(GEOMETRY, 0)
  );

  CREATE TABLE recintos (
      id        SERIAL PRIMARY KEY,
      plano_id  INTEGER REFERENCES planos(id) ON DELETE CASCADE,
      nombre    TEXT,
      categoria TEXT,
      area_m2   NUMERIC(12,2),
      confianza TEXT,          -- 'alta' | 'estimada'
      layer     TEXT,
      metodo    TEXT,
      geom      GEOMETRY(POLYGON, 0)
  );

  CREATE INDEX entidades_geom_idx  ON entidades USING GIST(geom);
  CREATE INDEX recintos_geom_idx   ON recintos  USING GIST(geom);
  CREATE INDEX entidades_layer_idx ON entidades(plano_id, layer);
─────────────────────────────────────────────────────────────────────────────
"""

import math
import re
import sys
import logging
from collections import Counter
from pathlib import Path

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Dependencias
# ──────────────────────────────────────────────────────────────────────────────

try:
    import ezdxf
except ImportError:
    print("Dependencia faltante: pip install ezdxf")
    sys.exit(1)

try:
    from shapely.geometry import Point, LineString, Polygon
    import geopandas as gpd
    import pandas as pd
except ImportError:
    print("Dependencias faltantes: pip install shapely geopandas pandas")
    sys.exit(1)


# ──────────────────────────────────────────────────────────────────────────────
# DxfReader
# ──────────────────────────────────────────────────────────────────────────────

class DxfReader:
    """Carga un archivo DXF y expone sus entidades de forma iterable."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.doc = ezdxf.readfile(str(self.path))
        self.msp = self.doc.modelspace()

    @property
    def layers(self) -> list[str]:
        return sorted(layer.dxf.name for layer in self.doc.layers)

    def entities(
        self,
        layer: str | None = None,
        types: set[str] | None = None,
    ):
        """
        Itera entidades del modelspace.
        layer: filtra por nombre de capa exacto.
        types: filtra por tipo(s) de entidad, ej. {"TEXT", "MTEXT"}.
        """
        for e in self.msp:
            if layer is not None and e.dxf.layer != layer:
                continue
            if types is not None and e.dxftype() not in types:
                continue
            yield e

    def summary(self) -> dict:
        """Devuelve un resumen del archivo: nombre, capas y conteo de entidades."""
        counts = Counter(e.dxftype() for e in self.msp)
        return {
            "archivo": self.path.name,
            "capas": len(self.layers),
            "entidades_total": sum(counts.values()),
            "por_tipo": dict(sorted(counts.items(), key=lambda x: -x[1])),
        }


# ──────────────────────────────────────────────────────────────────────────────
# GeometryConverter
# ──────────────────────────────────────────────────────────────────────────────

class GeometryConverter:
    """
    Convierte entidades DXF a geometrías Shapely con atributos.

    Tipos soportados:
      POINT, LINE, LWPOLYLINE, POLYLINE, CIRCLE, ARC,
      TEXT, MTEXT, INSERT, SPLINE, 3DFACE
    """

    CLOSED_TOL = 0.05  # distancia para considerar una polilínea geométricamente cerrada

    def __init__(self, arc_segments: int = 32):
        self.arc_segments = arc_segments

    def convert(self, entity) -> tuple:
        """
        Retorna (geometry, attrs).
          geometry: objeto Shapely o None si el tipo no aplica o hay error.
          attrs: dict con tipo, layer, handle, color, texto, bloque, radio, etc.
        """
        t = entity.dxftype()
        attrs = {
            "tipo": t,
            "layer": entity.dxf.layer if entity.dxf.hasattr("layer") else "0",
            "handle": entity.dxf.handle if entity.dxf.hasattr("handle") else None,
        }

        if entity.dxf.hasattr("color"):
            attrs["color"] = entity.dxf.color

        # Atributos específicos por tipo
        if t == "TEXT":
            raw = entity.dxf.text if entity.dxf.hasattr("text") else ""
            attrs["texto"] = re.sub(r"\s+", " ", raw).strip()
        elif t == "MTEXT":
            raw = (
                entity.plain_mtext()
                if hasattr(entity, "plain_mtext")
                else entity.plain_text()
                if hasattr(entity, "plain_text")
                else ""
            )
            attrs["texto"] = re.sub(r"\s+", " ", raw).strip()
        elif t == "INSERT":
            attrs["bloque"] = entity.dxf.name if entity.dxf.hasattr("name") else ""
        elif t == "CIRCLE":
            attrs["radio"] = round(entity.dxf.radius, 4)

        try:
            geom = self._to_shapely(entity, t)
        except Exception as ex:
            log.debug("Error convirtiendo %s handle=%s: %s", t, attrs.get("handle"), ex)
            geom = None

        return geom, attrs

    def to_geodataframe(
        self,
        reader: DxfReader,
        crs: str | None = None,
        layer: str | None = None,
    ) -> gpd.GeoDataFrame:
        """
        Convierte las entidades de un DxfReader a un GeoDataFrame.
        Omite entidades que no se puedan convertir a geometría.
        """
        records = []
        for entity in reader.entities(layer=layer):
            geom, attrs = self.convert(entity)
            if geom is None:
                continue
            records.append({"geometry": geom, **attrs})

        if not records:
            return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry")

        gdf = gpd.GeoDataFrame(records, geometry="geometry")
        if crs:
            gdf = gdf.set_crs(crs)
        return gdf

    # ── Conversores internos ──────────────────────────────────────────────────

    def _to_shapely(self, e, t: str):
        if t == "POINT":
            return Point(e.dxf.insert.x, e.dxf.insert.y)
        if t == "LINE":
            s, d = e.dxf.start, e.dxf.end
            return LineString([(s.x, s.y), (d.x, d.y)])
        if t in ("LWPOLYLINE", "POLYLINE"):
            return self._polyline(e, t)
        if t == "CIRCLE":
            return self._circle(e)
        if t == "ARC":
            return self._arc(e)
        if t in ("TEXT", "MTEXT", "INSERT"):
            p = e.dxf.insert
            return Point(p.x, p.y)
        if t == "SPLINE":
            pts = [(p.x, p.y) for p in e.control_points]
            return LineString(pts) if len(pts) >= 2 else None
        if t == "3DFACE":
            pts = [(v.x, v.y) for v in (e.dxf.vtx0, e.dxf.vtx1, e.dxf.vtx2, e.dxf.vtx3)]
            return Polygon(pts)
        return None

    def _polyline(self, e, t: str):
        if t == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in e.get_points()]
        else:
            try:
                pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
            except Exception:
                return None

        if len(pts) < 2:
            return None

        closed = getattr(e, "is_closed", False) or getattr(e, "closed", False)
        if not closed and len(pts) >= 3:
            closed = math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < self.CLOSED_TOL

        if closed and len(pts) >= 3:
            return Polygon(pts)
        return LineString(pts)

    def _circle(self, e):
        n = self.arc_segments
        cx, cy, r = e.dxf.center.x, e.dxf.center.y, e.dxf.radius
        pts = [
            (cx + r * math.cos(2 * math.pi * i / n),
             cy + r * math.sin(2 * math.pi * i / n))
            for i in range(n)
        ]
        return Polygon(pts)

    def _arc(self, e):
        n = self.arc_segments // 2
        cx, cy = e.dxf.center.x, e.dxf.center.y
        r = e.dxf.radius
        a0 = math.radians(e.dxf.start_angle)
        a1 = math.radians(e.dxf.end_angle)
        if a1 < a0:
            a1 += 2 * math.pi
        pts = [
            (cx + r * math.cos(a0 + (a1 - a0) * i / n),
             cy + r * math.sin(a0 + (a1 - a0) * i / n))
            for i in range(n + 1)
        ]
        return LineString(pts)


# ──────────────────────────────────────────────────────────────────────────────
# DxfExporter
# ──────────────────────────────────────────────────────────────────────────────

class DxfExporter:
    """Exporta un GeoDataFrame a distintos formatos GIS."""

    def __init__(self, gdf: gpd.GeoDataFrame):
        self.gdf = gdf

    def to_geojson(self, path: str | Path):
        """Exporta todo el GeoDataFrame como un único GeoJSON."""
        self.gdf.to_file(str(path), driver="GeoJSON")
        log.info("GeoJSON → %s  (%d entidades)", path, len(self.gdf))

    def to_geopackage(self, path: str | Path, layer_name: str = "dxf"):
        """Exporta a GeoPackage (soporta geometrías mixtas en una sola capa)."""
        self.gdf.to_file(str(path), layer=layer_name, driver="GPKG")
        log.info("GeoPackage → %s  (capa: %s)", path, layer_name)

    def to_shapefile(self, directory: str | Path):
        """
        Exporta a Shapefile, un archivo por (capa × tipo_geometría).
        Shapefile requiere geometría homogénea, de ahí la separación.
        """
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        written = 0
        for layer_name, sub in self._iter_layers():
            for geom_type, type_sub in sub.groupby(sub.geometry.geom_type):
                fname = f"{layer_name}_{geom_type[:4].lower()}"
                type_sub.reset_index(drop=True).to_file(directory / f"{fname}.shp")
                written += 1
        log.info("Shapefile → %s  (%d archivos)", directory, written)

    def to_postgis(
        self,
        engine,
        table: str,
        schema: str = "public",
        if_exists: str = "replace",
    ):
        """
        Exporta a PostGIS.
        engine debe ser un sqlalchemy Engine, ej.:
          from sqlalchemy import create_engine
          engine = create_engine("postgresql://user:pass@host:port/db")
        """
        self.gdf.to_postgis(table, engine, schema=schema, if_exists=if_exists)
        log.info("PostGIS → %s.%s  (%d entidades)", schema, table, len(self.gdf))

    def by_layer(self) -> dict[str, gpd.GeoDataFrame]:
        """Devuelve dict {nombre_capa: GeoDataFrame} para procesamiento independiente."""
        return {name: sub for name, sub in self._iter_layers()}

    def _iter_layers(self):
        if "layer" not in self.gdf.columns:
            yield "default", self.gdf
            return
        for name, group in self.gdf.groupby("layer"):
            safe = str(name).replace("/", "_").replace(" ", "_").replace(".", "_")
            yield safe, group.reset_index(drop=True)


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Convierte un DXF a GeoJSON / Shapefile / GeoPackage / PostGIS"
    )
    parser.add_argument("dxf", help="Ruta al archivo .dxf")
    parser.add_argument("--output", "-o", default=None, help="Archivo o directorio de salida")
    parser.add_argument("--crs", default=None, help="CRS a asignar, ej. EPSG:4326")
    parser.add_argument(
        "--format", "-f",
        choices=["geojson", "shp", "gpkg", "postgis"],
        default="geojson",
        help="Formato de salida (default: geojson)",
    )
    parser.add_argument("--layer", default=None, help="Exportar solo esta capa DXF")
    parser.add_argument("--dsn", default=None, help="DSN PostgreSQL para --format postgis")
    parser.add_argument("--summary", action="store_true", help="Solo mostrar resumen del archivo")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    dxf_path = Path(args.dxf)
    if not dxf_path.exists():
        log.error("Archivo no encontrado: %s", dxf_path)
        sys.exit(1)

    reader = DxfReader(dxf_path)
    s = reader.summary()
    log.info("Archivo  : %s", s["archivo"])
    log.info("Capas    : %d", s["capas"])
    log.info("Entidades: %d total", s["entidades_total"])
    for tipo, n in list(s["por_tipo"].items())[:10]:
        log.info("  %-20s %d", tipo, n)

    if args.summary:
        return

    converter = GeometryConverter()
    gdf = converter.to_geodataframe(reader, crs=args.crs, layer=args.layer)
    log.info("Geometrías convertidas: %d", len(gdf))

    if gdf.empty:
        log.warning("No se encontraron entidades convertibles.")
        sys.exit(0)

    exporter = DxfExporter(gdf)
    stem = dxf_path.stem
    out = args.output

    if args.format == "geojson":
        exporter.to_geojson(out or f"{stem}.geojson")
    elif args.format == "gpkg":
        exporter.to_geopackage(out or f"{stem}.gpkg", layer_name=stem)
    elif args.format == "shp":
        exporter.to_shapefile(out or stem)
    elif args.format == "postgis":
        from sqlalchemy import create_engine
        if not args.dsn:
            log.error("--dsn requerido. Ej: postgresql://dwg:pass@localhost:5433/dwg")
            sys.exit(1)
        engine = create_engine(args.dsn)
        exporter.to_postgis(engine, table=stem)


if __name__ == "__main__":
    main()
