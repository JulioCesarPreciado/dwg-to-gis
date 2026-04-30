"""
detectar_recintos.py
─────────────────────────────────────────────────────────────────────────────
Detecta habitaciones, locales y espacios en un plano arquitectónico DXF.

Estrategia:
  1. Extrae polígonos cerrados (LWPOLYLINE/POLYLINE) → candidatos a recintos.
  2. Extrae textos (TEXT/MTEXT) → etiquetas de espacio.
  3. Asocia cada texto a su recinto mediante point-in-polygon (Shapely).
     Si no hay coincidencia exacta, usa el polígono más cercano.
  4. Clasifica por categoría (local, habitación, baño, etc.).
  5. Exporta GeoDataFrame con polígonos + atributos.

Uso:
  python detectar_recintos.py plano.dxf
  python detectar_recintos.py plano.dxf --capas-texto "TX . 01" "ÁREAS"
  python detectar_recintos.py plano.dxf --output recintos.geojson
  python detectar_recintos.py plano.dxf --format gpkg --output recintos.gpkg
  python detectar_recintos.py plano.dxf --format json   # compatible con db.py

Capas de texto por convención de oficina:
  D'L / Universal : "TX . 01", "TX", "Ar-Texto", "T B", "ÁREAS"
  BOR-10x         : "A-AREA-IDEN", "G-ANNO-TEXT"
  AIA/NCS         : "A-AREA", "Q-SPCQ", "A-ANNO-NOTE"
"""

import json
import logging
import math
import re
import sys
from pathlib import Path

from dxf_to_gis import DxfExporter, DxfReader, GeometryConverter
from shapely.geometry import Point

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Configuración de categorías
# ──────────────────────────────────────────────────────────────────────────────

_CATEGORIAS = [
    (re.compile(r"^local\b", re.I),                                  "Local comercial",     "🏪"),
    (re.compile(r"^a-\d+|^depto?\.?\s*\d+|^departamento", re.I),    "Departamento",        "🏠"),
    (re.compile(r"habitaci[oó]n|recamara|recámara|dormitorio|master|bedroom", re.I), "Habitación", "🛏️"),
    (re.compile(r"sala|living|sala\s+de\s+estar", re.I),             "Sala",                "🛋️"),
    (re.compile(r"cocina|kitchen", re.I),                             "Cocina",              "🍳"),
    (re.compile(r"comedor|dining", re.I),                             "Comedor",             "🍽"),
    (re.compile(r"oficina|despacho|office", re.I),                    "Oficina",             "💼"),
    (re.compile(r"w\.c|baño", re.I),                                  "Baño",                "🚻"),
    (re.compile(r"terraza", re.I),                                    "Terraza",             "🌿"),
    (re.compile(r"pergolado", re.I),                                  "Pergolado",           "🌳"),
    (re.compile(r"pasillo|corredor|circulacion", re.I),               "Pasillo/Circulación", "🚶"),
    (re.compile(r"e\.s\.|e\.e\.|escalera", re.I),                     "Escalera",            "🪜"),
    (re.compile(r"estacion|caj[oó]n|estacionamiento", re.I),          "Estacionamiento",     "🚗"),
    (re.compile(r"azotea", re.I),                                     "Azotea",              "🏢"),
    (re.compile(r"lobby|recepcion|recepción", re.I),                  "Lobby",               "🏛"),
    (re.compile(r"jard[íi]n|area\s+verde", re.I),                    "Área verde",          "🌱"),
    (re.compile(r"bodega|almac[eé]n", re.I),                          "Bodega/Almacén",      "📦"),
    (re.compile(
        r"cuarto\s+(de\s+)?(m[aá]quinas|bombeo|basura|control|el[eé]ctrico|residuos)"
        r"|cuarto\s+elec|site\b|subestaci[oó]n|caseta|transformadores|ducto",
        re.I,
    ), "Cuarto técnico", "⚙️"),
    (re.compile(r"rampa|ciclopuerto", re.I),                          "Circulación/Acceso",  "🔄"),
    (re.compile(
        r"cafeter[íi]a|restaurante|gimnasio|cl[íi]nica|farmacia|laboratorio"
        r"|sal[oó]n\s+de|lavander[íi]a",
        re.I,
    ), "Giro comercial", "🍽️"),
]

# Textos que NO son nombres de espacios (medidas, ejes, fragmentos, etc.)
_IGNORAR = re.compile(
    r"^[\d\.\,\s]+(?:m[²2]?)?\s*$"           # números solos o "96.25 m²"
    r"|^not\s+enclosed$"                       # interno de ezdxf
    r"|^area\s+total:?\s*$"
    r"|^[xy]$|^[xy]-\d+[a-z]?$"              # ejes X, Y-1, Y-2…
    r"|^b\.a\.p\.?$"
    r"|^pendiente\s+[\d\.]+\s*%?$|^[\d\.]+\s*%$"
    r"|^parteaguas$"
    r"|^(l[íi]mite|limite)\s+de\s+"
    r"|^(sube|baja)(\s+rampa)?$"
    r"|^proyecci[oó]n(\s+cisterna)?$"
    r"|^edificio|^lote|^x-x|^a-a"
    r"|^av\.|^c\.\s+\w+|^calle\s+"
    r"|^\d+[\.\,]\d+\s*m2?\s*$"
    r"|^(medidores|tarja|transformadores)$"
    r"|^(banqueta|banqueta\s+exterior)$"
    r"|^(ingreso|salida)$"
    r"|^(area\s+basura|espacio\s+abierto)$",
    re.I,
)

AREA_MIN_M2 = 0.5   # polígonos más pequeños se ignoran como ruido
RADIO_BUSQ  = 15.0  # radio máximo para buscar el polígono más cercano


def _clasificar(nombre: str) -> tuple[str, str]:
    for patron, categoria, icono in _CATEGORIAS:
        if patron.search(nombre):
            return categoria, icono
    return "Otro", "📐"


# ──────────────────────────────────────────────────────────────────────────────
# Función principal de detección
# ──────────────────────────────────────────────────────────────────────────────

def detectar_recintos(
    reader: DxfReader,
    capas_texto: list[str] | None = None,
    area_min: float = AREA_MIN_M2,
    radio_busq: float = RADIO_BUSQ,
) -> "gpd.GeoDataFrame":
    """
    Detecta recintos (habitaciones, locales, etc.) en el DXF.

    Args:
        reader:       DxfReader con el archivo cargado.
        capas_texto:  Capas de donde leer etiquetas. None = todas las capas.
        area_min:     Área mínima (unidades del DXF) para aceptar un polígono.
        radio_busq:   Radio máximo para el fallback de polígono más cercano.

    Returns:
        GeoDataFrame con columnas:
          geometry, nombre, categoria, icono, area_m2, confianza,
          capa_recinto, capa_texto, metodo, x, y
    """
    import geopandas as gpd

    converter = GeometryConverter()

    # 1. Recolectar polígonos cerrados — candidatos a recintos
    poligonos = []
    for e in reader.entities(types={"LWPOLYLINE", "POLYLINE"}):
        geom, attrs = converter.convert(e)
        if geom is None or geom.geom_type != "Polygon":
            continue
        if geom.area < area_min:
            continue
        poligonos.append({
            "geom":    geom,
            "layer":   attrs.get("layer", "0"),
            "area_m2": round(geom.area, 2),
            "cx":      geom.centroid.x,
            "cy":      geom.centroid.y,
        })

    log.info("Polígonos cerrados encontrados: %d", len(poligonos))

    # 2. Recolectar textos de etiqueta de espacio
    textos = []
    for e in reader.entities(types={"TEXT", "MTEXT"}):
        layer = e.dxf.layer if e.dxf.hasattr("layer") else "0"
        if capas_texto and layer not in capas_texto:
            continue
        _, attrs = converter.convert(e)
        txt = attrs.get("texto", "").strip()
        if not txt or _IGNORAR.match(txt):
            continue
        p = e.dxf.insert
        textos.append({"texto": txt, "x": p.x, "y": p.y, "layer": layer})

    log.info("Etiquetas de espacio: %d", len(textos))

    if not poligonos:
        log.warning("No se encontraron polígonos cerrados en el plano.")
        return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry")

    # 3. Asociar cada texto a un polígono
    resultados = []
    for t in textos:
        pt = Point(t["x"], t["y"])
        best = None
        confianza = None
        metodo = "sin geometría"

        # 3a. Point-in-polygon exacto (alta confianza)
        for p in sorted(poligonos, key=lambda x: x["area_m2"]):
            if p["geom"].contains(pt):
                best = p
                confianza = "alta"
                metodo = "polilínea exacta"
                break

        # 3b. Polígono más cercano dentro del radio (baja confianza)
        if best is None:
            mejor_d = radio_busq
            for p in poligonos:
                d = math.hypot(t["x"] - p["cx"], t["y"] - p["cy"])
                if d < mejor_d:
                    mejor_d = d
                    best = p
                    confianza = "estimada"
                    metodo = f"más cercano ({mejor_d:.1f}m)"

        if best is None:
            continue

        categoria, icono = _clasificar(t["texto"])
        resultados.append({
            "geometry":     best["geom"],
            "nombre":       t["texto"],
            "categoria":    categoria,
            "icono":        icono,
            "area_m2":      best["area_m2"],
            "confianza":    confianza,
            "capa_recinto": best["layer"],
            "capa_texto":   t["layer"],
            "metodo":       metodo,
            "x":            round(t["x"], 2),
            "y":            round(t["y"], 2),
        })

    log.info("Recintos asociados: %d", len(resultados))
    return gpd.GeoDataFrame(resultados, geometry="geometry")


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def _imprimir_resumen(gdf) -> None:
    print(f"\n{'='*65}")
    print(f"  RECINTOS DETECTADOS: {len(gdf)}")
    print(f"{'='*65}")
    if "categoria" in gdf.columns:
        for cat, grp in gdf.groupby("categoria"):
            total = grp["area_m2"].sum() if "area_m2" in grp.columns else 0
            print(f"  {cat:<32} {len(grp):>4} recintos   {total:>10.2f} m²")
        print(f"  {'─'*63}")
    total_area = gdf["area_m2"].sum() if "area_m2" in gdf.columns else 0
    print(f"  {'TOTAL':<32} {len(gdf):>4} recintos   {total_area:>10.2f} m²")
    print(f"{'='*65}\n")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Detecta habitaciones y locales en un plano DXF."
    )
    parser.add_argument("dxf", help="Ruta al archivo .dxf")
    parser.add_argument(
        "--capas-texto", nargs="*", metavar="CAPA",
        help='Capas con etiquetas de espacio. Ej: "TX . 01" "ÁREAS". Default: todas.',
    )
    parser.add_argument("--output", "-o", default=None, help="Archivo de salida")
    parser.add_argument(
        "--format", "-f",
        choices=["geojson", "gpkg", "shp", "json"],
        default="geojson",
        help="Formato de salida (default: geojson)",
    )
    parser.add_argument("--crs", default=None, help="CRS a asignar, ej. EPSG:4326")
    parser.add_argument("--area-min", type=float, default=AREA_MIN_M2,
                        help=f"Área mínima para recintos (default: {AREA_MIN_M2})")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    dxf_path = Path(args.dxf)
    if not dxf_path.exists():
        log.error("Archivo no encontrado: %s", dxf_path)
        sys.exit(1)

    reader = DxfReader(dxf_path)
    s = reader.summary()
    log.info("Leyendo: %s  (%d entidades, %d capas)", s["archivo"], s["entidades_total"], s["capas"])

    gdf = detectar_recintos(
        reader,
        capas_texto=args.capas_texto,
        area_min=args.area_min,
    )

    if gdf.empty:
        log.warning(
            "No se detectaron recintos.\n"
            "Sugerencias:\n"
            "  1. Verifica que el DXF tiene polilíneas cerradas.\n"
            "  2. Especifica --capas-texto con los nombres de capas de etiquetas.\n"
            "  3. Ajusta --area-min si las unidades no son metros.\n"
            "  4. Usa: python dxf_to_gis.py %s --summary  para inspeccionar capas.",
            args.dxf,
        )
        sys.exit(0)

    if args.crs:
        gdf = gdf.set_crs(args.crs)

    _imprimir_resumen(gdf)

    exporter = DxfExporter(gdf)
    stem = dxf_path.stem
    out = args.output

    if args.format == "geojson":
        exporter.to_geojson(out or f"{stem}_recintos.geojson")
    elif args.format == "gpkg":
        exporter.to_geopackage(out or f"{stem}_recintos.gpkg", layer_name="recintos")
    elif args.format == "shp":
        exporter.to_shapefile(out or f"{stem}_recintos")
    elif args.format == "json":
        # JSON plano compatible con db.py
        dest = out or f"{stem}_inventario.json"
        data = gdf.drop(columns=["geometry"]).to_dict(orient="records")
        Path(dest).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("JSON → %s", dest)


if __name__ == "__main__":
    main()
