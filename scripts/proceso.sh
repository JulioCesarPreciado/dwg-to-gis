#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# proceso.sh — Pipeline completo: DWG → DXF → PostGIS
#
# Uso:
#   bash proceso.sh archivo.dwg
#   bash proceso.sh archivo.dwg "Nombre del proyecto"
#   bash proceso.sh archivo.dwg "Nombre" --reimportar
#
# Variables configurables (editar la sección CONFIG):
#   CAPAS_TEXTO   — capas de etiquetas de espacio según tu oficina de arquitectos
#   CRS           — sistema de coordenadas si se conoce (ej. EPSG:6372)
#   DXF_DIR       — directorio donde se guardan los DXF convertidos
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── CONFIG ────────────────────────────────────────────────────────────────────

# Capas que contienen los nombres de habitaciones/locales.
# Ajusta según la convención de la oficina que entregó el plano:
#   D'L / Universal : "TX . 01"  "TX"  "Ar-Texto"  "T B"  "ÁREAS"
#   BOR-10x         : "A-AREA-IDEN"  "G-ANNO-TEXT"
#   AIA/NCS         : "A-AREA"  "Q-SPCQ"  "A-ANNO-NOTE"
CAPAS_TEXTO=("TX . 01" "TX" "ÁREAS" "A-AREA-IDEN" "G-ANNO-TEXT")

# CRS del plano (dejar "unknown" si no se sabe).
CRS="unknown"

# Directorios y rutas
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DXF_DIR="$ROOT/data/dxf"
PYTHON="$ROOT/.venv/bin/python"
ODA="/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"
DSN="postgresql://dwg:dwg_pass@localhost:5433/dwg"

# ── VALIDACIONES ──────────────────────────────────────────────────────────────

DWG_FILE="${1:-}"
if [[ -z "$DWG_FILE" ]]; then
    echo "Uso: bash proceso.sh archivo.dwg [\"Nombre del proyecto\"] [--reimportar]"
    echo ""
    echo "Opciones:"
    echo "  --reimportar   Borra el plano existente y reimporta desde cero"
    exit 1
fi

if [[ ! -f "$DWG_FILE" ]]; then
    echo "Error: no existe el archivo '$DWG_FILE'"
    exit 1
fi

DWG_ABS="$(cd "$(dirname "$DWG_FILE")" && pwd)/$(basename "$DWG_FILE")"
BASENAME="$(basename "$DWG_FILE" .dwg)"
NOMBRE="${2:-$BASENAME}"
DXF_FILE="$DXF_DIR/${BASENAME}.dxf"

# Flags opcionales (--reimportar puede estar en $2 o $3)
REIMPORTAR=""
for arg in "$@"; do
    if [[ "$arg" == "--reimportar" ]]; then
        REIMPORTAR="--reimportar"
    fi
done

# Verificar Python
if [[ ! -f "$PYTHON" ]]; then
    echo "Error: no se encontró el entorno virtual en .venv/"
    echo "Crea uno con: python3 -m venv .venv && .venv/bin/pip install ezdxf shapely geopandas psycopg2-binary pandas"
    exit 1
fi

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "  DWG → PostGIS Pipeline"
echo "  Archivo : $(basename "$DWG_ABS")"
echo "  Proyecto: $NOMBRE"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── PASO 1: Docker ────────────────────────────────────────────────────────────

echo "▶ 1/4  Iniciando PostgreSQL/PostGIS..."
docker compose -f "$ROOT/docker-compose.yml" up -d

# Esperar hasta que PostgreSQL acepte conexiones (máx. 30 s)
echo "       Esperando que PostgreSQL esté listo..."
for i in $(seq 1 30); do
    if docker compose -f "$ROOT/docker-compose.yml" \
        exec -T postgis pg_isready -U dwg -d dwg -q 2>/dev/null; then
        echo "       PostgreSQL listo."
        break
    fi
    if [[ $i -eq 30 ]]; then
        echo "Error: PostgreSQL no respondió en 30 segundos."
        exit 1
    fi
    sleep 1
done

# ── PASO 2: Conversión DWG → DXF ─────────────────────────────────────────────

echo ""
echo "▶ 2/4  Convirtiendo DWG → DXF..."
mkdir -p "$DXF_DIR"

if [[ -f "$DXF_FILE" && -z "$REIMPORTAR" ]]; then
    echo "       DXF ya existe, se reutiliza: $DXF_FILE"
else
    # ODAFileConverter convierte un archivo específico usando filtro por nombre
    TMP_DIR="$(mktemp -d)"
    cp "$DWG_ABS" "$TMP_DIR/"

    if [[ ! -f "$ODA" ]]; then
        echo "Advertencia: ODAFileConverter no encontrado en $ODA"
        echo "Instálalo desde https://www.opendesign.com/guestfiles/oda_file_converter"
        echo "O convierte manualmente el DWG a DXF y colócalo en: $DXF_FILE"
        rm -rf "$TMP_DIR"
        # Si el DXF ya existe (de una conversión manual), continuar
        if [[ ! -f "$DXF_FILE" ]]; then
            exit 1
        fi
    else
        "$ODA" "$TMP_DIR" "$DXF_DIR" ACAD2018 DXF 0 1
        rm -rf "$TMP_DIR"
        echo "       DXF generado: $DXF_FILE"
    fi
fi

# ── PASO 3: Importar a PostGIS ────────────────────────────────────────────────

echo ""
echo "▶ 3/4  Importando a PostGIS..."

# Construir los argumentos de capas de texto
CAPAS_ARGS=()
if [[ ${#CAPAS_TEXTO[@]} -gt 0 ]]; then
    CAPAS_ARGS+=("--capas-texto")
    for capa in "${CAPAS_TEXTO[@]}"; do
        CAPAS_ARGS+=("$capa")
    done
fi

"$PYTHON" "$ROOT/src/pg_import.py" "$DXF_FILE" \
    --nombre "$NOMBRE" \
    --dsn "$DSN" \
    --crs "$CRS" \
    ${REIMPORTAR} \
    "${CAPAS_ARGS[@]}"

# ── PASO 4: Resumen en BD ─────────────────────────────────────────────────────

echo ""
echo "▶ 4/4  Estado de la base de datos:"
docker compose -f "$ROOT/docker-compose.yml" exec -T postgis \
    psql -U dwg -d dwg -x -c "SELECT * FROM dwg.v_resumen ORDER BY procesado_en DESC LIMIT 5;"

echo ""
echo "✓ Proceso completado: '$NOMBRE'"
echo ""
echo "  Conexión directa:"
echo "    psql postgresql://dwg:dwg_pass@localhost:5433/dwg"
echo ""
echo "  Queries útiles:"
echo "    SELECT * FROM dwg.v_recintos WHERE plano = '$NOMBRE';"
echo "    SELECT * FROM dwg.v_capas    WHERE plano = '$NOMBRE';"
echo "    SELECT * FROM dwg.v_resumen;"
echo ""
