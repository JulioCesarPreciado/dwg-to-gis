#!/bin/bash
# Convierte todos los DWG de data/ a DXF en data/dxf/

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INPUT_DIR="$ROOT/data"
OUTPUT_DIR="$ROOT/data/dxf"
ODA="/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"

mkdir -p "$OUTPUT_DIR"

"$ODA" "$INPUT_DIR" "$OUTPUT_DIR" ACAD2018 DXF 0 1

echo "Conversión completada. Archivos DXF en: $OUTPUT_DIR"
