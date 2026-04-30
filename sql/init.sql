-- ─────────────────────────────────────────────────────────────────────────────
-- Esquema dwg — almacena planos CAD convertidos a GIS
-- Se ejecuta automáticamente al crear el contenedor por primera vez.
-- Seguro de relanzar (todo usa IF NOT EXISTS / OR REPLACE).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE SCHEMA IF NOT EXISTS dwg;

-- ── planos ───────────────────────────────────────────────────────────────────
-- Un registro por archivo DWG/DXF procesado.
CREATE TABLE IF NOT EXISTS dwg.planos (
    id           SERIAL PRIMARY KEY,
    nombre       TEXT NOT NULL UNIQUE,   -- clave de negocio (ej: "Casa García N1")
    archivo_dwg  TEXT,                    -- nombre del .dwg original
    archivo_dxf  TEXT,                    -- nombre del .dxf convertido
    crs          TEXT DEFAULT 'unknown',  -- ej. "EPSG:6372" si se conoce
    procesado_en TIMESTAMPTZ DEFAULT NOW(),
    n_entidades  INTEGER DEFAULT 0,       -- total de entidades geométricas
    n_recintos   INTEGER DEFAULT 0        -- total de recintos detectados
);

-- ── entidades ─────────────────────────────────────────────────────────────────
-- Todas las geometrías del DXF: líneas, polilíneas, textos, círculos, etc.
CREATE TABLE IF NOT EXISTS dwg.entidades (
    id        BIGSERIAL PRIMARY KEY,
    plano_id  INTEGER NOT NULL REFERENCES dwg.planos(id) ON DELETE CASCADE,
    tipo      TEXT,       -- LINE, LWPOLYLINE, CIRCLE, TEXT, INSERT…
    layer     TEXT,       -- nombre de capa DXF
    handle    TEXT,       -- id único dentro del DXF
    color     INTEGER,    -- color DXF (0–256)
    texto     TEXT,       -- contenido si tipo = TEXT / MTEXT
    bloque    TEXT,       -- nombre del bloque si tipo = INSERT
    radio     NUMERIC,    -- radio si tipo = CIRCLE
    geom      GEOMETRY(GEOMETRY, 0)  -- SRID 0 = coordenadas locales del plano
);

-- ── recintos ──────────────────────────────────────────────────────────────────
-- Habitaciones, locales y espacios detectados automáticamente.
CREATE TABLE IF NOT EXISTS dwg.recintos (
    id           BIGSERIAL PRIMARY KEY,
    plano_id     INTEGER NOT NULL REFERENCES dwg.planos(id) ON DELETE CASCADE,
    nombre       TEXT,
    categoria    TEXT,          -- Local comercial, Habitación, Baño, Oficina…
    icono        TEXT,
    area_m2      NUMERIC(12, 2),
    confianza    TEXT,          -- 'alta' (point-in-polygon) | 'estimada' (más cercano)
    capa_recinto TEXT,          -- layer de la polilínea
    capa_texto   TEXT,          -- layer del texto etiqueta
    metodo       TEXT,          -- descripción del método de asociación
    x            NUMERIC,       -- coordenada del texto etiqueta
    y            NUMERIC,
    geom         GEOMETRY(POLYGON, 0)
);

-- ── índices ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_entidades_geom     ON dwg.entidades USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_recintos_geom      ON dwg.recintos  USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_entidades_plano    ON dwg.entidades(plano_id, layer);
CREATE INDEX IF NOT EXISTS idx_entidades_tipo     ON dwg.entidades(tipo);
CREATE INDEX IF NOT EXISTS idx_recintos_plano     ON dwg.recintos(plano_id, categoria);

-- ── vistas ────────────────────────────────────────────────────────────────────

-- Recintos con info del plano — útil para la app web.
CREATE OR REPLACE VIEW dwg.v_recintos AS
SELECT
    r.id,
    p.nombre        AS plano,
    p.archivo_dwg,
    r.nombre,
    r.categoria,
    r.icono,
    r.area_m2,
    r.confianza,
    r.capa_recinto,
    r.metodo,
    r.x,
    r.y,
    r.geom
FROM dwg.recintos r
JOIN dwg.planos p ON r.plano_id = p.id;

-- Resumen por plano: conteos, área total y bounding box.
CREATE OR REPLACE VIEW dwg.v_resumen AS
SELECT
    p.id,
    p.nombre,
    p.archivo_dwg,
    p.procesado_en,
    p.n_entidades,
    p.n_recintos,
    COUNT(DISTINCT r.categoria)         AS categorias,
    ROUND(SUM(r.area_m2)::NUMERIC, 2)   AS area_total_m2,
    ST_AsText(ST_Envelope(ST_Collect(r.geom))) AS bbox
FROM dwg.planos p
LEFT JOIN dwg.recintos r ON r.plano_id = p.id
GROUP BY p.id, p.nombre, p.archivo_dwg, p.procesado_en, p.n_entidades, p.n_recintos;

-- Distribución de entidades por capa (para explorar un plano).
CREATE OR REPLACE VIEW dwg.v_capas AS
SELECT
    p.nombre   AS plano,
    e.layer,
    e.tipo,
    COUNT(*)   AS cantidad
FROM dwg.entidades e
JOIN dwg.planos p ON e.plano_id = p.id
GROUP BY p.nombre, e.layer, e.tipo
ORDER BY p.nombre, e.layer, cantidad DESC;
