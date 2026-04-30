"""
detectar_recintos.py
─────────────────────────────────────────────────────────────────────────────
Detecta habitaciones y espacios en planos DXF usando 3 estrategias en cascada.

Estrategia 1 — Polilíneas cerradas        confianza: alta
  Busca LWPOLYLINE/POLYLINE cerradas y asocia textos por point-in-polygon.
  Funciona cuando el arquitecto dibujó cada recinto como polilínea cerrada.

Estrategia 2 — Polygonize (red de muros)  confianza: media
  Toma líneas de los layers de muro, snapea endpoints y reconstruye
  polígonos con shapely.ops.polygonize. Funciona cuando los muros
  forman circuitos cerrados aunque no sean polilíneas explícitas.

Estrategia 3 — Ray-casting                confianza: baja
  Para cada texto sin recinto, lanza rayos en 4 direcciones hasta chocar
  con un muro y genera un bbox aproximado del recinto.

Cada recinto guardado incluye el campo `estrategia` con el método usado,
para que la aplicación sepa cuáles necesitan revisión manual.

Uso:
  python detectar_recintos.py plano.dxf
  python detectar_recintos.py plano.dxf --capas-texto "TX . 01" "ÁREAS"
  python detectar_recintos.py plano.dxf --capas-muros "A-MUROS" "A-MUROS 2"
  python detectar_recintos.py plano.dxf --snap-tol 0.5 --format geojson
  python detectar_recintos.py plano.dxf --info   # muestra capas y recomienda config
"""

from __future__ import annotations

import json
import logging
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize
from shapely import STRtree

from dxf_to_gis import DxfExporter, DxfReader, GeometryConverter

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Categorías de espacios
# ──────────────────────────────────────────────────────────────────────────────

_CATEGORIAS = [
    (re.compile(r"^local\b",                                           re.I), "Local comercial",     "🏪"),
    (re.compile(r"^a-\d+|^depto?\.?\s*\d+|^departamento",             re.I), "Departamento",        "🏠"),
    (re.compile(r"habitaci[oó]n|recamara|recámara|dormitorio|master|bedroom|bed\s*room", re.I), "Habitación", "🛏️"),
    (re.compile(r"\bsala\b|living|sala\s+de\s+estar|lounge|family\s*room|sunken", re.I), "Sala",    "🛋️"),
    (re.compile(r"cocina|kitchen",                                     re.I), "Cocina",              "🍳"),
    (re.compile(r"comedor|dining|breakfast",                           re.I), "Comedor",             "🍽"),
    (re.compile(r"oficina|despacho|office|estudio|study",              re.I), "Oficina",             "💼"),
    (re.compile(r"w\.c|ba[ñn]o|bathroom|toilet|wc\b|powder",         re.I), "Baño",                "🚻"),
    (re.compile(r"terraza|balc[oó]n|balcony|deck",                     re.I), "Terraza",             "🌿"),
    (re.compile(r"patio|jardin|garden|yard|courtyard",                 re.I), "Patio/Jardín",        "🌱"),
    (re.compile(r"pergolado",                                          re.I), "Pergolado",           "🌳"),
    (re.compile(r"pasillo|corredor|circulacion|hallway|corridor",      re.I), "Pasillo/Circulación", "🚶"),
    (re.compile(r"e\.s\.|e\.e\.|escalera|stair|stairway",             re.I), "Escalera",            "🪜"),
    (re.compile(r"estacion|caj[oó]n|estacionamiento|parking|garage|cochera|garaje", re.I), "Estacionamiento", "🚗"),
    (re.compile(r"azotea|roof",                                        re.I), "Azotea",              "🏢"),
    (re.compile(r"lobby|recepci[oó]n|reception|vestibulo|foyer|entry|entryway", re.I), "Lobby",     "🏛"),
    (re.compile(r"bodega|almac[eé]n|storage|warehouse|pantry|pant\.",  re.I), "Bodega/Almacén",     "📦"),
    (re.compile(r"closet|vestidor|walk.in|wardrobe|hers|his\b|dressing", re.I), "Closet",           "👔"),
    (re.compile(r"lavander[íi]a|lavado|laundry|utility|mud\s*room",   re.I), "Lavandería",          "🧺"),
    (re.compile(
        r"cuarto\s+(de\s+)?(m[aá]quinas|bombeo|basura|control|el[eé]ctrico|residuos)"
        r"|cuarto\s+elec|site\b|subestaci[oó]n|caseta|transformadores|mechanical",
        re.I,
    ), "Cuarto técnico", "⚙️"),
    (re.compile(r"rampa|ciclopuerto|ramp",                             re.I), "Circulación/Acceso",  "🔄"),
    (re.compile(
        r"cafeter[íi]a|restaurante|gimnasio|cl[íi]nica|farmacia|laboratorio|salon",
        re.I,
    ), "Giro comercial", "🍽️"),
]

# Patrones de nombres de capas que contienen muros (español e inglés)
_WALL_LAYER_PATTERNS = re.compile(
    r"mur[oa]|pared|wall|tabique|cerr|partition|barrier|estructura",
    re.I,
)

# Capas a excluir siempre del análisis de muros
_NON_WALL_LAYERS = re.compile(
    r"text|cota|dimen|mobil|simb|eje|axis|axis|anno|hatch|relleno"
    r"|cotas|acot|acotacion|titulo|north|norte|logo|viewport",
    re.I,
)

# Textos que no son nombres de recintos
_IGNORAR = re.compile(
    r"^[\d\.\,\s]+(?:m[²2]?)?\s*$"
    r"|^not\s+enclosed$"
    r"|^area\s+total:?\s*$"
    r"|^[xy]$|^[xy]-\d+[a-z]?$"
    r"|^b\.a\.p\.?$"
    r"|^pendiente[\s\d\.]+%?$|^[\d\.]+\s*%$"
    r"|^parteaguas$"
    r"|^(l[íi]mite|limite)\s+de\s+"
    r"|^(sube|baja)(\s+rampa)?$"
    r"|^proyecci[oó]n(\s+cisterna)?$"
    r"|^edificio$|^lote$|^x-x$|^a-a$"
    r"|^av\.|^calle\s+"
    r"|^\d+[\.\,]\d+\s*m2?\s*$"
    r"|^(medidores|tarja|transformadores)$"
    r"|^(banqueta|banqueta\s+exterior)$"
    r"|^(ingreso|salida|entrada)$"
    r"|^(area\s+basura|espacio\s+abierto)$"
    r"|^planta\s+(baja|alta|nivel|piso)"
    r"|^elevaci[oó]n|^fachada|^corte|^seccion"
    r"|^section\b|^elevation\b|^facade\b"
    r"|^level\s*\d|^floor\s*\d|^nivel\s*\d"
    r"|^[a-z]$|^[A-Z]$",          # letras sueltas (ejes de columnas)
    re.I,
)

AREA_MIN   = 0.5    # m² mínimo para considerar un polígono
SNAP_TOL   = 0.35   # m de tolerancia para unir endpoints de muros
MAX_RAY_AREA = 80.0 # m² máximo para aceptar un bbox de ray-casting


def _clasificar(nombre: str) -> tuple[str, str]:
    for patron, categoria, icono in _CATEGORIAS:
        if patron.search(nombre):
            return categoria, icono
    return "Otro", "📐"


def _es_valido(txt: str) -> bool:
    return bool(txt) and not _IGNORAR.match(txt)


# ──────────────────────────────────────────────────────────────────────────────
# RecintoDetector — pipeline de 3 estrategias
# ──────────────────────────────────────────────────────────────────────────────

class RecintoDetector:
    """
    Detecta recintos en un DXF usando 3 estrategias en cascada.

    Primero intenta con polilíneas cerradas (máxima precisión).
    Si quedan textos sin recinto, intenta reconstruir polígonos desde
    la red de muros (polygonize). Finalmente usa ray-casting para los
    recintos que ninguna estrategia anterior pudo resolver.
    """

    # Metros por unidad de dibujo según $INSUNITS del DXF
    _UNIT_SCALE = {
        0: 1.0,      # sin unidades — asume metros
        1: 0.0254,   # pulgadas
        2: 0.3048,   # pies
        4: 0.001,    # milímetros
        5: 0.01,     # centímetros
        6: 1.0,      # metros
        7: 1000.0,   # kilómetros
    }

    def __init__(
        self,
        reader: DxfReader,
        capas_texto: list[str] | None = None,
        capas_muros: list[str] | None = None,
        area_min: float = AREA_MIN,
        snap_tol: float = SNAP_TOL,
        max_ray_area: float = MAX_RAY_AREA,
    ):
        self.reader      = reader
        self.capas_texto = set(capas_texto) if capas_texto else None
        self.capas_muros = set(capas_muros) if capas_muros else None
        self._conv       = GeometryConverter()

        # Escala: convierte unidades del DXF a metros
        dxf_units  = getattr(reader.doc, "units", 0)
        self._m_per_unit = self._UNIT_SCALE.get(dxf_units, 1.0)
        log.info("Unidades DXF: %d  →  1 unidad = %.4f m", dxf_units, self._m_per_unit)

        # Convertir parámetros (dados en metros/m²) a unidades del DXF
        u  = self._m_per_unit
        self.area_min     = area_min     / (u ** 2)
        self.snap_tol     = snap_tol     / u
        self.max_ray_area = max_ray_area / (u ** 2)

    # ── API pública ───────────────────────────────────────────────────────────

    def detectar(self) -> "gpd.GeoDataFrame":
        """Ejecuta el pipeline completo y devuelve un GeoDataFrame."""
        import geopandas as gpd

        textos   = self._get_textos()
        log.info("Textos de espacio encontrados: %d", len(textos))

        if not textos:
            log.warning("No se encontraron textos de espacio. Prueba --capas-texto.")
            return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry")

        pendientes = list(range(len(textos)))   # índices aún sin recinto
        resultados = []

        # ── Estrategia 1: polilíneas cerradas ─────────────────────────────────
        polys_1 = self._get_polilineas_cerradas()
        log.info("[S1] Polilíneas cerradas: %d", len(polys_1))
        if polys_1:
            asignados, pendientes = self._asociar(textos, pendientes, polys_1,
                                                  "alta", "polilínea cerrada")
            resultados.extend(asignados)
            log.info("[S1] Recintos asignados: %d  |  pendientes: %d",
                     len(asignados), len(pendientes))

        # ── Estrategia 2: polygonize ───────────────────────────────────────────
        if pendientes:
            wall_lines = self._get_wall_lines()
            polys_2    = self._polygonize(wall_lines)
            log.info("[S2] Polígonos reconstruidos: %d", len(polys_2))
            if polys_2:
                asignados, pendientes = self._asociar(textos, pendientes, polys_2,
                                                      "media", "polygonize (red de muros)")
                resultados.extend(asignados)
                log.info("[S2] Recintos asignados: %d  |  pendientes: %d",
                         len(asignados), len(pendientes))
            else:
                log.info("[S2] Sin polígonos reconstruibles (muros con gaps grandes)")

        # ── Estrategia 3: ray-casting ─────────────────────────────────────────
        if pendientes:
            wall_lines = self._get_wall_lines()
            # Detectar vistas separadas (ej. Planta Baja + Planta Alta en la misma hoja)
            # y limitar los rayos a la vista de cada texto para evitar escapes entre vistas.
            view_bounds = self._detect_views([textos[i] for i in pendientes], wall_lines)
            asignados   = self._raycast(textos, pendientes, wall_lines, view_bounds)
            resultados.extend(asignados)
            log.info("[S3] Recintos por ray-casting: %d", len(asignados))

        log.info("Total recintos detectados: %d", len(resultados))

        if not resultados:
            return gpd.GeoDataFrame(columns=["geometry"], geometry="geometry")

        return gpd.GeoDataFrame(resultados, geometry="geometry")

    def info(self) -> dict:
        """
        Analiza el DXF y devuelve un dict con recomendaciones de configuración:
        capas candidatas para texto, capas candidatas para muros, etc.
        """
        from collections import defaultdict
        capas_por_tipo = defaultdict(lambda: defaultdict(int))
        for e in self.reader.entities():
            capas_por_tipo[e.dxftype()][e.dxf.layer] += 1

        # Capas de texto (tienen TEXT/MTEXT)
        text_layers = {}
        for t in ("TEXT", "MTEXT"):
            for layer, n in capas_por_tipo[t].items():
                text_layers[layer] = text_layers.get(layer, 0) + n

        # Capas candidatas de muro (tienen LINE)
        line_layers = dict(capas_por_tipo["LINE"])

        wall_candidates = {
            l: n for l, n in line_layers.items()
            if _WALL_LAYER_PATTERNS.search(l) or (n >= 10 and not _NON_WALL_LAYERS.search(l))
        }

        return {
            "capas_texto_candidatas":  sorted(text_layers, key=lambda l: -text_layers[l]),
            "capas_muro_candidatas":   sorted(wall_candidates, key=lambda l: -wall_candidates[l]),
            "capas_detectadas_auto":   list(self._auto_wall_layers()),
            "resumen_entidades":       self.reader.summary()["por_tipo"],
        }

    # ── Extracción de entidades ───────────────────────────────────────────────

    def _get_textos(self) -> list[dict]:
        textos = []
        for e in self.reader.entities(types={"TEXT", "MTEXT"}):
            layer = e.dxf.layer if e.dxf.hasattr("layer") else "0"
            if self.capas_texto and layer not in self.capas_texto:
                continue
            _, attrs = self._conv.convert(e)
            txt = attrs.get("texto", "").strip()
            if not _es_valido(txt):
                continue
            p = e.dxf.insert
            textos.append({"texto": txt, "x": float(p.x), "y": float(p.y), "layer": layer})
        return textos

    def _get_polilineas_cerradas(self) -> list[dict]:
        polys = []
        for e in self.reader.entities(types={"LWPOLYLINE", "POLYLINE"}):
            geom, attrs = self._conv.convert(e)
            if geom is None or geom.geom_type != "Polygon":
                continue
            if geom.area < self.area_min:
                continue
            polys.append({
                "geom":    geom,
                "layer":   attrs.get("layer", "0"),
                "area_m2": round(geom.area, 2),
            })
        return polys

    def _get_wall_lines(self) -> list[LineString]:
        wall_layers = self.capas_muros or self._auto_wall_layers()
        lines = []
        for e in self.reader.entities(types={"LINE", "LWPOLYLINE"}):
            if e.dxf.layer not in wall_layers:
                continue
            if e.dxftype() == "LINE":
                s, d = e.dxf.start, e.dxf.end
                seg = LineString([(float(s.x), float(s.y)), (float(d.x), float(d.y))])
                if seg.length > 0.02:
                    lines.append(seg)
            elif e.dxftype() == "LWPOLYLINE":
                pts = [(float(p[0]), float(p[1])) for p in e.get_points()]
                for i in range(len(pts) - 1):
                    seg = LineString([pts[i], pts[i + 1]])
                    if seg.length > 0.02:
                        lines.append(seg)
        return lines

    def _auto_wall_layers(self) -> set[str]:
        """Detecta automáticamente capas de muros por nombre y conteo de LINEs."""
        line_counts: dict[str, int] = Counter(
            e.dxf.layer
            for e in self.reader.entities(types={"LINE"})
        )
        if not line_counts:
            return set()

        # Prioridad 1: nombre coincide con patrón de muro
        by_name = {l for l in line_counts if _WALL_LAYER_PATTERNS.search(l)}

        # Prioridad 2: las capas con más LINEs que no son claramente no-muros
        threshold = max(line_counts.values()) * 0.15   # al menos 15% del máximo
        by_count = {
            l for l, n in line_counts.items()
            if n >= threshold and not _NON_WALL_LAYERS.search(l)
        }

        candidates = by_name | by_count
        if not candidates:
            # Último recurso: la capa con más líneas
            candidates = {max(line_counts, key=line_counts.get)}

        log.debug("Capas de muro detectadas: %s", candidates)
        return candidates

    def _auto_bounds(self) -> tuple[float, float, float, float]:
        """Bounding box de las líneas de muro (para limitar rayos)."""
        wall_layers = self.capas_muros or self._auto_wall_layers()
        xs, ys = [], []
        for e in self.reader.entities(types={"LINE"}):
            if e.dxf.layer not in wall_layers:
                continue
            s, d = e.dxf.start, e.dxf.end
            xs.extend([float(s.x), float(d.x)])
            ys.extend([float(s.y), float(d.y)])
        if not xs:
            return (0, 1e6, 0, 1e6)
        return (min(xs), max(xs), min(ys), max(ys))

    # ── Estrategia 2: polygonize ──────────────────────────────────────────────

    def _polygonize(self, lines: list[LineString]) -> list[dict]:
        if not lines:
            return []

        # Recolectar todos los endpoints
        raw_pts: list[tuple[float, float]] = []
        seg_pt_idx: list[tuple[int, int]] = []

        for seg in lines:
            coords = list(seg.coords)
            for i in range(len(coords) - 1):
                si = len(raw_pts); raw_pts.append(coords[i])
                ei = len(raw_pts); raw_pts.append(coords[i + 1])
                seg_pt_idx.append((si, ei))

        canonical, mapping = self._snap_pts(raw_pts, self.snap_tol)

        snapped_lines = []
        for si, ei in seg_pt_idx:
            sp = canonical[mapping[si]]
            ep = canonical[mapping[ei]]
            if sp != ep:
                snapped_lines.append(LineString([sp, ep]))

        polys = [
            p for p in polygonize(snapped_lines)
            if p.area >= self.area_min
        ]

        return [
            {"geom": p, "layer": "reconstructed", "area_m2": round(p.area, 2)}
            for p in polys
        ]

    @staticmethod
    def _snap_pts(
        pts: list[tuple[float, float]],
        tol: float,
    ) -> tuple[list[tuple[float, float]], list[int]]:
        """
        Agrupa puntos cercanos (< tol) en un representante canónico.
        Devuelve (lista_canonicos, mapping: indice_original → indice_canonico).
        """
        grid: dict[tuple[int, int], int] = {}
        canonical: list[tuple[float, float]] = []
        mapping: list[int] = []

        for pt in pts:
            cx = int(pt[0] / tol)
            cy = int(pt[1] / tol)
            found = -1
            for dx in range(-1, 2):
                for dy in range(-1, 2):
                    key = (cx + dx, cy + dy)
                    if key in grid:
                        idx = grid[key]
                        ex, ey = canonical[idx]
                        if math.hypot(pt[0] - ex, pt[1] - ey) < tol:
                            found = idx
                            break
                if found != -1:
                    break

            if found == -1:
                found = len(canonical)
                canonical.append(pt)
                grid[(cx, cy)] = found

            mapping.append(found)

        return canonical, mapping

    # ── Asociación texto → polígono ───────────────────────────────────────────

    def _asociar(
        self,
        textos: list[dict],
        pendientes: list[int],
        polys: list[dict],
        confianza: str,
        metodo: str,
    ) -> tuple[list[dict], list[int]]:
        """
        Asocia textos pendientes con polígonos por point-in-polygon.
        Devuelve (lista de recintos asignados, índices todavía sin asignar).
        """
        if not polys or not pendientes:
            return [], pendientes

        geoms = [p["geom"] for p in polys]
        tree  = STRtree(geoms)

        asignados   = []
        sin_asignar = []

        for idx in pendientes:
            t  = textos[idx]
            pt = Point(t["x"], t["y"])

            # Candidatos por bbox (intersects es más robusto que contains con STRtree)
            candidates = tree.query(pt, predicate="intersects")
            # Filtrar los que realmente contienen el punto
            hits = [i for i in candidates if geoms[i].contains(pt)]
            if hits:
                # El polígono más pequeño que contiene el punto
                best_i = min(hits, key=lambda i: geoms[i].area)
                p      = polys[best_i]
                cat, ico = _clasificar(t["texto"])
                asignados.append(self._build_row(t, p, confianza, metodo, cat, ico))
            else:
                sin_asignar.append(idx)

        return asignados, sin_asignar

    # ── Detección de vistas ───────────────────────────────────────────────────

    def _detect_views(
        self,
        textos_pendientes: list[dict],
        wall_lines: list[LineString],
        gap_factor: float = 0.15,
    ) -> dict[int, tuple[float, float, float, float]]:
        """
        Detecta vistas separadas en el plano (PB + PA + Elevaciones en una hoja).
        Devuelve un dict { texto_idx_global: (xmin,xmax,ymin,ymax) } con el bbox
        de la vista a la que pertenece cada texto pendiente.

        El algoritmo agrupa textos por su coordenada X: un gap en la distribución
        de X mayor a `gap_factor * ancho_total` indica una vista nueva.
        """
        if not textos_pendientes:
            return {}

        # Bounding box de los muros (límite real del plano)
        wall_xs, wall_ys = [], []
        for seg in wall_lines:
            for x, y in seg.coords:
                wall_xs.append(x); wall_ys.append(y)

        global_xmin = min(wall_xs) if wall_xs else 0
        global_xmax = max(wall_xs) if wall_xs else 1e9
        global_ymin = min(wall_ys) if wall_ys else 0
        global_ymax = max(wall_ys) if wall_ys else 1e9

        # Ordenar textos por X y encontrar gaps
        sorted_xs = sorted(t["x"] for t in textos_pendientes)
        total_width = sorted_xs[-1] - sorted_xs[0] if len(sorted_xs) > 1 else 1
        min_gap = total_width * gap_factor

        # Construir clusters de X
        clusters: list[tuple[float, float]] = []   # (xmin, xmax) por cluster
        cluster_start = sorted_xs[0]
        prev = sorted_xs[0]
        for x in sorted_xs[1:]:
            if x - prev > min_gap:
                clusters.append((cluster_start, prev))
                cluster_start = x
            prev = x
        clusters.append((cluster_start, prev))

        log.debug("Vistas detectadas: %d  (%s)",
                  len(clusters), [(f"{c[0]:.0f}-{c[1]:.0f}") for c in clusters])

        # Asignar cada texto a su vista y calcular el bbox de muros en esa vista
        result: dict[int, tuple[float, float, float, float]] = {}
        for i, t in enumerate(textos_pendientes):
            for cx_min, cx_max in clusters:
                if cx_min <= t["x"] <= cx_max:
                    # Padding entre vistas: mitad del gap anterior/posterior
                    left_pad  = (cx_min - (clusters[clusters.index((cx_min, cx_max)) - 1][1]
                                           if clusters.index((cx_min, cx_max)) > 0
                                           else global_xmin)) / 2
                    right_idx = clusters.index((cx_min, cx_max)) + 1
                    right_pad = ((clusters[right_idx][0] - cx_max) / 2
                                 if right_idx < len(clusters) else 0)
                    xmin = max(global_xmin, cx_min - left_pad)
                    xmax = min(global_xmax, cx_max + right_pad)
                    result[i] = (xmin, xmax, global_ymin, global_ymax)
                    break
            else:
                result[i] = (global_xmin, global_xmax, global_ymin, global_ymax)
        return result

    # ── Estrategia 3: ray-casting ─────────────────────────────────────────────

    def _raycast(
        self,
        textos: list[dict],
        pendientes: list[int],
        wall_lines: list[LineString],
        view_bounds: dict[int, tuple[float, float, float, float]],
    ) -> list[dict]:
        if not pendientes:
            return []

        # Construir índice local para view_bounds (0..len(pendientes)-1 → bounds)
        results = []
        # max_d escalado a unidades del DXF (default 15m → mm para planos en mm)
        max_d = 15.0 / self._m_per_unit

        for local_i, idx in enumerate(pendientes):
            t  = textos[idx]
            tx, ty = t["x"], t["y"]
            xmin, xmax, ymin, ymax = view_bounds.get(local_i, self._auto_bounds())

            de = min(self._ray_hit(tx, ty,  1,  0, wall_lines, max_d), xmax - tx)
            dw = min(self._ray_hit(tx, ty, -1,  0, wall_lines, max_d), tx - xmin)
            dn = min(self._ray_hit(tx, ty,  0,  1, wall_lines, max_d), ymax - ty)
            ds = min(self._ray_hit(tx, ty,  0, -1, wall_lines, max_d), ty - ymin)

            bx0, bx1 = tx - dw, tx + de
            by0, by1 = ty - ds, ty + dn
            area = float((bx1 - bx0) * (by1 - by0))

            cat, ico = _clasificar(t["texto"])

            area_m2 = area * (self._m_per_unit ** 2)
            if area <= self.max_ray_area and area >= self.area_min:
                geom   = Polygon([(bx0,by0),(bx1,by0),(bx1,by1),(bx0,by1)])
                conf   = "baja"
                metodo = f"ray-casting ({area_m2:.1f} m²)"
            else:
                # Fallback: solo punto
                geom   = Point(tx, ty)
                area   = None
                conf   = "posición"
                metodo = "punto (sin recinto detectado)"

            p = {"geom": geom, "layer": "raycast", "area_m2": area or 0}
            results.append(self._build_row(t, p, conf, metodo, cat, ico,
                                           area_override=area))

        return results

    @staticmethod
    def _ray_hit(ox, oy, dx, dy, walls, max_d=15.0):
        best = float(max_d)
        ray  = LineString([(ox, oy), (ox + dx * max_d, oy + dy * max_d)])
        for seg in walls:
            inter = ray.intersection(seg)
            if inter.is_empty:
                continue
            pts = list(inter.geoms) if hasattr(inter, "geoms") else [inter]
            for pt in pts:
                if hasattr(pt, "x"):
                    d = math.hypot(pt.x - ox, pt.y - oy)
                    if d > 0.02:
                        best = min(best, d)
        return best

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_row(self, texto, poly, confianza, metodo, categoria, icono,
                   area_override=None) -> dict:
        area_du = area_override if area_override is not None else poly.get("area_m2")
        # Convertir área de unidades del DXF a m²
        u    = self._m_per_unit
        area = round(area_du * (u ** 2), 2) if area_du else None
        return {
            "geometry":     poly["geom"],
            "nombre":       texto["texto"],
            "categoria":    categoria,
            "icono":        icono,
            "area_m2":      area,
            "confianza":    confianza,
            "estrategia":   metodo,
            "capa_recinto": poly.get("layer", "0"),
            "capa_texto":   texto.get("layer", "0"),
            "x":            round(texto["x"], 2),
            "y":            round(texto["y"], 2),
        }


# ──────────────────────────────────────────────────────────────────────────────
# Función de conveniencia (backward compatible)
# ──────────────────────────────────────────────────────────────────────────────

def detectar_recintos(
    reader: DxfReader,
    capas_texto: list[str] | None = None,
    capas_muros: list[str] | None = None,
    area_min: float = AREA_MIN,
    snap_tol: float = SNAP_TOL,
) -> "gpd.GeoDataFrame":
    """Detecta recintos usando el pipeline de 3 estrategias."""
    detector = RecintoDetector(
        reader,
        capas_texto=capas_texto,
        capas_muros=capas_muros,
        area_min=area_min,
        snap_tol=snap_tol,
    )
    return detector.detectar()


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def _imprimir_resumen(gdf) -> None:
    print(f"\n{'='*68}")
    print(f"  RECINTOS DETECTADOS: {len(gdf)}")
    print(f"{'='*68}")

    if "estrategia" in gdf.columns:
        from collections import Counter
        ests = Counter(gdf["estrategia"])
        for est, n in sorted(ests.items()):
            print(f"  [{n:>3}]  {est}")
        print(f"  {'─'*64}")

    if "categoria" in gdf.columns:
        for cat, grp in gdf.groupby("categoria"):
            areas = grp["area_m2"].dropna()
            total = areas.sum()
            print(f"  {cat:<30} {len(grp):>4}  {total:>10.2f} m²")
        print(f"  {'─'*64}")

    total_area = gdf["area_m2"].dropna().sum() if "area_m2" in gdf.columns else 0
    print(f"  {'TOTAL':<30} {len(gdf):>4}  {total_area:>10.2f} m²")
    print(f"{'='*68}\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Detecta recintos en un plano DXF (3 estrategias en cascada)."
    )
    parser.add_argument("dxf", help="Archivo .dxf")
    parser.add_argument("--capas-texto", nargs="*", metavar="CAPA",
                        help="Capas con etiquetas. Default: todas.")
    parser.add_argument("--capas-muros", nargs="*", metavar="CAPA",
                        help="Capas de muros. Default: auto-detectar.")
    parser.add_argument("--snap-tol", type=float, default=SNAP_TOL,
                        help=f"Tolerancia de snap para polygonize (default {SNAP_TOL})")
    parser.add_argument("--area-min", type=float, default=AREA_MIN,
                        help=f"Área mínima de recinto (default {AREA_MIN})")
    parser.add_argument("--output", "-o", default=None)
    parser.add_argument("--format", "-f",
                        choices=["geojson", "gpkg", "shp", "json"],
                        default="geojson")
    parser.add_argument("--crs", default=None)
    parser.add_argument("--info", action="store_true",
                        help="Solo mostrar análisis de capas y salir")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    dxf_path = Path(args.dxf)
    if not dxf_path.exists():
        log.error("Archivo no encontrado: %s", dxf_path)
        sys.exit(1)

    reader   = DxfReader(dxf_path)
    detector = RecintoDetector(
        reader,
        capas_texto=args.capas_texto,
        capas_muros=args.capas_muros,
        area_min=args.area_min,
        snap_tol=args.snap_tol,
    )

    # Modo info: solo analizar el archivo
    if args.info:
        info = detector.info()
        s    = reader.summary()
        print(f"\nArchivo: {s['archivo']}  ({s['entidades_total']} entidades, {s['capas']} capas)")
        print(f"\nEntidades por tipo:")
        for tipo, n in sorted(s["por_tipo"].items(), key=lambda x: -x[1]):
            print(f"  {tipo:<20} {n}")
        print(f"\nCapas candidatas para --capas-texto:")
        for l in info["capas_texto_candidatas"]:
            print(f"  '{l}'")
        print(f"\nCapas candidatas para --capas-muros (auto-detectadas):")
        for l in info["capas_detectadas_auto"]:
            print(f"  '{l}'")
        return

    gdf = detector.detectar()

    if gdf.empty:
        log.warning(
            "Sin recintos detectados.\n"
            "  Prueba: python detectar_recintos.py %s --info\n"
            "  para ver las capas disponibles y ajustar --capas-texto y --capas-muros.",
            args.dxf,
        )
        sys.exit(0)

    if args.crs:
        gdf = gdf.set_crs(args.crs)

    _imprimir_resumen(gdf)

    exporter = DxfExporter(gdf)
    stem = dxf_path.stem
    out  = args.output

    if args.format == "geojson":
        exporter.to_geojson(out or f"{stem}_recintos.geojson")
    elif args.format == "gpkg":
        exporter.to_geopackage(out or f"{stem}_recintos.gpkg", layer_name="recintos")
    elif args.format == "shp":
        exporter.to_shapefile(out or f"{stem}_recintos")
    elif args.format == "json":
        dest = out or f"{stem}_inventario.json"
        cols = [c for c in gdf.columns if c != "geometry"]
        Path(dest).write_text(
            json.dumps(gdf[cols].to_dict(orient="records"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info("JSON → %s", dest)


if __name__ == "__main__":
    main()
