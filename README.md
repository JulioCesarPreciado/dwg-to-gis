# dwg-to-gis

Pipeline en Python para convertir planos arquitectónicos **DWG/DXF** a estructuras GIS consultables desde una aplicación web.

Extrae entidades geométricas del plano, detecta habitaciones y locales automáticamente, y los almacena en **PostgreSQL/PostGIS** con geometrías consultables por SQL espacial.

---

## Flujo general

```
DWG  ──►  DXF  ──►  Entidades GIS  ──►  Detección de recintos  ──►  PostGIS
      ODA         ezdxf / Shapely        point-in-polygon              dwg.*
```

---

## Características

- Convierte DWG a DXF via ODA FileConverter
- Lee DXF con **ezdxf** y convierte a geometrías **Shapely**
- Detecta recintos (habitaciones, locales, baños, etc.) por point-in-polygon o ray-casting
- Clasifica espacios en categorías: Habitación, Sala, Cocina, Baño, Local comercial, etc.
- Exporta a **GeoJSON**, **GeoPackage**, **Shapefile** o **PostGIS**
- Base de datos **PostgreSQL/PostGIS** con Docker, lista para conectar a una app web
- Soporte para múltiples convenciones de capas (D'L, BOR-10x, AIA/NCS)

---

## Requisitos

| Herramienta | Versión |
|---|---|
| Python | 3.11+ |
| Docker + Docker Compose | cualquier versión reciente |
| ODA FileConverter | [descargar aquí](https://www.opendesign.com/guestfiles/oda_file_converter) |

---

## Instalación

```bash
# 1. Clonar el repositorio
git clone https://github.com/JulioCesarPreciado/dwg-to-gis.git
cd dwg-to-gis

# 2. Crear entorno virtual e instalar dependencias
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Iniciar PostgreSQL/PostGIS (crea el schema automáticamente)
docker compose up -d

# 4. (macOS) Si ODA FileConverter no arranca, re-firmarlo una vez:
sudo codesign --force --deep --sign - /Applications/ODAFileConverter.app
```

---

## Uso rápido

### Pipeline completo (DWG → PostGIS)

```bash
bash scripts/proceso.sh data/plano.dwg "Nombre del proyecto"

# Re-importar si el plano cambió
bash scripts/proceso.sh data/plano.dwg "Nombre del proyecto" --reimportar
```

### Solo convertir DWG → DXF

```bash
bash scripts/convert.sh
# Los DXF quedan en data/dxf/
```

### Inspeccionar un DXF antes de importar

```bash
python src/dxf_to_gis.py data/dxf/plano.dxf --summary
```

### Detectar recintos y exportar a GeoJSON

```bash
python src/detectar_recintos.py data/dxf/plano.dxf \
  --capas-texto "TX . 01" "ÁREAS" \
  --format geojson \
  --output recintos.geojson
```

### Importar directamente a PostGIS

```bash
python src/pg_import.py data/dxf/plano.dxf \
  --nombre "Edificio X - Nivel 1" \
  --capas-texto "TX . 01" "ÁREAS"
```

### Ver recintos importados sobre el plano

```bash
python src/recintos_viewer.py
```

Abre `http://127.0.0.1:8765/`. El visor lee `dwg.recintos` y `dwg.entidades`
desde PostGIS para comparar los polígonos detectados contra las geometrías del DXF.

### Gestionar inventario en SQLite (sin PostGIS)

```bash
python db.py importar data/dxf/plano_inventario.json
python db.py resumen
python db.py ver
```

---

## Estructura del proyecto

```
dwg-to-gis/
├── src/
│   ├── dxf_to_gis.py          # DxfReader · GeometryConverter · DxfExporter
│   ├── detectar_recintos.py   # Detección de recintos por point-in-polygon
│   └── pg_import.py           # Importador DXF → PostGIS
├── scripts/
│   ├── proceso.sh             # Pipeline completo (orquestador)
│   └── convert.sh             # Conversión DWG → DXF
├── sql/
│   └── init.sql               # Schema PostgreSQL (se aplica automáticamente)
├── docs/
│   └── NORMAS_DWG.md          # Guía para arquitectos: cómo entregar los planos
├── data/
│   ├── samples/               # Planos de ejemplo (no se suben a git)
│   └── dxf/                   # DXF convertidos (no se suben a git)
├── docker-compose.yml
├── requirements.txt
└── db.py                      # CLI SQLite (alternativa sin PostGIS)
```

---

## Base de datos

La base de datos tiene tres tablas principales en el schema `dwg`:

```sql
dwg.planos      -- Un registro por archivo DWG procesado
dwg.entidades   -- Todas las geometrías del DXF (líneas, polilíneas, textos…)
dwg.recintos    -- Habitaciones y locales detectados con su polígono
```

### Queries de ejemplo

```sql
-- Resumen de todos los planos importados
SELECT * FROM dwg.v_resumen;

-- Recintos de un plano con su geometría
SELECT nombre, categoria, area_m2, confianza
FROM dwg.v_recintos
WHERE plano = 'Mi Proyecto'
ORDER BY categoria;

-- Buscar recintos por categoría en todos los planos
SELECT plano, nombre, area_m2
FROM dwg.v_recintos
WHERE categoria = 'Habitación'
ORDER BY area_m2 DESC;

-- Entidades por capa
SELECT * FROM dwg.v_capas WHERE plano = 'Mi Proyecto';
```

Conexión: `postgresql://dwg:dwg_pass@localhost:5433/dwg`

---

## Capas de texto por convención de oficina

| Convención | Capas con etiquetas de espacio |
|---|---|
| D'L / Universal | `TX . 01`, `TX`, `Ar-Texto`, `T B`, `ÁREAS` |
| BOR-10x | `A-AREA-IDEN`, `G-ANNO-TEXT` |
| AIA/NCS | `A-AREA`, `Q-SPCQ`, `A-ANNO-NOTE` |

Ver [docs/NORMAS_DWG.md](docs/NORMAS_DWG.md) para la guía completa de entrega de planos.

---

## Limitaciones conocidas

- **DWG con muros como líneas individuales**: la detección de recintos funciona mejor cuando las habitaciones están dibujadas como **polilíneas cerradas**. Si los muros son líneas sueltas, se usa ray-casting como aproximación.
- **Sin CRS nativo**: los DXF no traen sistema de coordenadas. Asignar con `--crs EPSG:XXXX` cuando se conozca.
- **Bloques (INSERT)**: los bloques se almacenan como puntos, no se expanden.

---

## Roadmap

- [ ] Detección de recintos desde muros como líneas (topología automática)
- [ ] API REST para subir DWG y consultar resultados
- [ ] Soporte para archivos IFC/BIM
- [ ] Visualizador web con MapLibre/Leaflet
