#!/usr/bin/env python3
"""
Visor local de recintos guardados en PostGIS.

Lee `dwg.recintos` y `dwg.entidades` para superponer los polígonos detectados
sobre las geometrías base del DXF. No requiere dependencias web externas.

Uso:
  python src/recintos_viewer.py
  python src/recintos_viewer.py --port 8765
  python src/recintos_viewer.py --dsn "host=localhost port=5433 dbname=dwg user=dwg password=dwg_pass"
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
from decimal import Decimal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    raise SystemExit("Dependencia faltante: pip install psycopg2-binary")


DSN_DEFAULT = "host=localhost port=5433 dbname=dwg user=dwg password=dwg_pass"
MAX_ENTIDADES_DEFAULT = 12000
ROOT = Path(__file__).resolve().parents[1]

log = logging.getLogger("recintos_viewer")


def json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


class Db:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def connect(self):
        return psycopg2.connect(self.dsn)

    def planos(self) -> list[dict]:
        sql = """
            SELECT
                p.id,
                p.nombre,
                p.archivo_dxf,
                p.procesado_en,
                p.n_entidades,
                p.n_recintos,
                COUNT(DISTINCT r.categoria) AS categorias,
                ROUND(SUM(r.area_m2)::NUMERIC, 2) AS area_total_m2
            FROM dwg.planos p
            LEFT JOIN dwg.recintos r ON r.plano_id = p.id
            GROUP BY p.id, p.nombre, p.archivo_dxf, p.procesado_en, p.n_entidades, p.n_recintos
            ORDER BY p.procesado_en DESC, p.id DESC
        """
        with self.connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            return [dict(row) for row in cur.fetchall()]

    def plano(self, plano_id: int, max_entidades: int = MAX_ENTIDADES_DEFAULT) -> dict:
        with self.connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, nombre, archivo_dxf, crs, procesado_en, n_entidades, n_recintos
                FROM dwg.planos
                WHERE id = %s
                """,
                (plano_id,),
            )
            plano = cur.fetchone()
            if not plano:
                raise KeyError(f"Plano no encontrado: {plano_id}")

            cur.execute(
                """
                SELECT
                    id,
                    nombre,
                    categoria,
                    icono,
                    area_m2,
                    confianza,
                    capa_recinto,
                    capa_texto,
                    metodo,
                    x,
                    y,
                    ST_AsGeoJSON(geom, 6)::json AS geometry
                FROM dwg.recintos
                WHERE plano_id = %s
                ORDER BY id
                """,
                (plano_id,),
            )
            recintos = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT
                    id,
                    tipo,
                    layer,
                    texto,
                    color,
                    ST_AsGeoJSON(geom, 6)::json AS geometry
                FROM dwg.entidades
                WHERE plano_id = %s
                  AND geom IS NOT NULL
                  AND tipo IN (
                      'LINE', 'LWPOLYLINE', 'POLYLINE', 'CIRCLE', 'ARC',
                      'TEXT', 'MTEXT', 'INSERT', 'POINT', '3DFACE'
                  )
                ORDER BY
                    CASE tipo
                        WHEN 'LINE' THEN 1
                        WHEN 'LWPOLYLINE' THEN 2
                        WHEN 'POLYLINE' THEN 3
                        WHEN 'ARC' THEN 4
                        WHEN 'CIRCLE' THEN 5
                        ELSE 6
                    END,
                    id
                LIMIT %s
                """,
                (plano_id, max_entidades),
            )
            entidades = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT layer, tipo, COUNT(*) AS cantidad
                FROM dwg.entidades
                WHERE plano_id = %s
                GROUP BY layer, tipo
                ORDER BY layer, tipo
                """,
                (plano_id,),
            )
            capas = [dict(row) for row in cur.fetchall()]

        return {
            "plano": dict(plano),
            "recintos": recintos,
            "entidades": entidades,
            "capas": capas,
            "entidades_limit": max_entidades,
        }


INDEX_HTML = r"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Visor de recintos</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f5f7;
      --panel: #ffffff;
      --line: #d6dae0;
      --text: #1f2933;
      --muted: #66727f;
      --ink: #2f3a44;
      --accent: #0f766e;
      --accent-2: #b45309;
      --danger: #b91c1c;
      --shadow: 0 8px 22px rgba(31, 41, 51, 0.08);
    }

    * { box-sizing: border-box; }

    html, body {
      width: 100%;
      height: 100%;
      margin: 0;
      overflow: hidden;
      font: 14px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background: var(--bg);
    }

    body {
      display: grid;
      grid-template-columns: 320px minmax(0, 1fr);
      grid-template-rows: 48px minmax(0, 1fr) 28px;
      grid-template-areas:
        "top top"
        "side main"
        "status status";
    }

    header {
      grid-area: top;
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 0 14px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfd;
      min-width: 0;
    }

    h1 {
      font-size: 15px;
      font-weight: 700;
      margin: 0;
      white-space: nowrap;
    }

    select, button, input[type="search"] {
      height: 32px;
      border: 1px solid #c7cdd4;
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      font: inherit;
    }

    select {
      min-width: 230px;
      max-width: 34vw;
      padding: 0 34px 0 10px;
    }

    button {
      width: 32px;
      display: inline-grid;
      place-items: center;
      cursor: pointer;
      user-select: none;
      font-size: 16px;
    }

    button.active {
      border-color: var(--accent);
      color: #fff;
      background: var(--accent);
    }

    button:disabled {
      cursor: not-allowed;
      opacity: 0.5;
    }

    input[type="search"] {
      width: 100%;
      padding: 0 10px;
    }

    .toolbar {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }

    .spacer { flex: 1; }

    aside {
      grid-area: side;
      min-width: 0;
      overflow: hidden;
      border-right: 1px solid var(--line);
      background: var(--panel);
      display: grid;
      grid-template-rows: auto auto minmax(0, 1fr);
    }

    .metrics {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 8px;
      padding: 12px;
      border-bottom: 1px solid var(--line);
    }

    .metric {
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px;
      background: #fbfcfd;
    }

    .metric b {
      display: block;
      font-size: 18px;
      line-height: 1.1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .metric span {
      display: block;
      margin-top: 4px;
      color: var(--muted);
      font-size: 12px;
    }

    .filters {
      display: grid;
      gap: 8px;
      padding: 12px;
      border-bottom: 1px solid var(--line);
    }

    .checkline {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      color: var(--muted);
    }

    .checkline input { margin: 0; }

    .tabs {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 6px;
    }

    .tabs button {
      width: 100%;
      height: 30px;
      font-size: 13px;
      color: var(--muted);
      background: #f8fafb;
    }

    .tabs button.active {
      color: #fff;
      background: var(--ink);
      border-color: var(--ink);
    }

    .list {
      min-height: 0;
      overflow: auto;
      padding: 8px;
    }

    .row {
      display: grid;
      gap: 2px;
      min-width: 0;
      padding: 8px;
      border: 1px solid transparent;
      border-radius: 8px;
      cursor: pointer;
    }

    .row:hover, .row.active {
      background: #f2f7f7;
      border-color: #c7e4df;
    }

    .row strong {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 13px;
    }

    .row span {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--muted);
      font-size: 12px;
    }

    main {
      grid-area: main;
      position: relative;
      min-width: 0;
      min-height: 0;
      overflow: hidden;
      background:
        linear-gradient(#eef1f4 1px, transparent 1px),
        linear-gradient(90deg, #eef1f4 1px, transparent 1px),
        #ffffff;
      background-size: 28px 28px;
    }

    canvas {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      cursor: grab;
    }

    canvas.dragging { cursor: grabbing; }

    .tooltip {
      position: absolute;
      min-width: 180px;
      max-width: 280px;
      padding: 8px 10px;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.96);
      box-shadow: var(--shadow);
      pointer-events: none;
      transform: translate(12px, 12px);
      display: none;
      font-size: 12px;
    }

    .tooltip b {
      display: block;
      margin-bottom: 2px;
      font-size: 13px;
    }

    footer {
      grid-area: status;
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 0 12px;
      border-top: 1px solid var(--line);
      background: #fbfcfd;
      color: var(--muted);
      font-size: 12px;
      min-width: 0;
    }

    .status-text {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      min-width: 0;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      height: 20px;
      padding: 0 7px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: #fff;
      white-space: nowrap;
    }

    .error {
      color: var(--danger);
      font-weight: 600;
    }

    @media (max-width: 780px) {
      body {
        grid-template-columns: 1fr;
        grid-template-rows: 48px 210px minmax(0, 1fr) 28px;
        grid-template-areas:
          "top"
          "side"
          "main"
          "status";
      }

      header { gap: 8px; }
      h1 { display: none; }
      select { min-width: 0; max-width: none; flex: 1; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); }
      .metrics { grid-template-columns: repeat(4, 1fr); }
    }
  </style>
</head>
<body>
  <header>
    <h1>Recintos</h1>
    <select id="planos"></select>
    <div class="toolbar" aria-label="herramientas">
      <button id="fit" title="Ajustar vista">⌖</button>
      <button id="toggleBase" class="active" title="Mostrar entidades DXF">▤</button>
      <button id="toggleRecintos" class="active" title="Mostrar recintos">▰</button>
      <button id="toggleLabels" class="active" title="Mostrar etiquetas">T</button>
    </div>
    <div class="spacer"></div>
  </header>

  <aside>
    <section class="metrics" aria-label="resumen">
      <div class="metric"><b id="mEntidades">0</b><span>entidades</span></div>
      <div class="metric"><b id="mRecintos">0</b><span>recintos</span></div>
      <div class="metric"><b id="mArea">0</b><span>m²</span></div>
      <div class="metric"><b id="mCapas">0</b><span>capas</span></div>
    </section>
    <section class="filters">
      <input id="search" type="search" placeholder="Filtrar recinto o capa">
      <label class="checkline"><input id="onlySelectedLayer" type="checkbox"> solo capa seleccionada</label>
      <div class="tabs">
        <button id="tabRecintos" class="active">Recintos</button>
        <button id="tabCapas">Capas</button>
      </div>
    </section>
    <section id="list" class="list" aria-label="lista"></section>
  </aside>

  <main>
    <canvas id="canvas"></canvas>
    <div id="tooltip" class="tooltip"></div>
  </main>

  <footer>
    <span id="status" class="status-text">Listo</span>
    <span id="coords" class="pill">x 0 · y 0</span>
    <span id="scale" class="pill">100%</span>
  </footer>

  <script>
    const state = {
      planos: [],
      data: null,
      selectedPlano: null,
      selectedRecinto: null,
      selectedLayer: null,
      tab: "recintos",
      search: "",
      showBase: true,
      showRecintos: true,
      showLabels: true,
      onlySelectedLayer: false,
      dpr: Math.max(1, window.devicePixelRatio || 1),
      view: { scale: 1, tx: 0, ty: 0 },
      dragging: false,
      last: null,
      hover: null,
    };

    const el = {
      planos: document.getElementById("planos"),
      canvas: document.getElementById("canvas"),
      tooltip: document.getElementById("tooltip"),
      status: document.getElementById("status"),
      coords: document.getElementById("coords"),
      scale: document.getElementById("scale"),
      fit: document.getElementById("fit"),
      toggleBase: document.getElementById("toggleBase"),
      toggleRecintos: document.getElementById("toggleRecintos"),
      toggleLabels: document.getElementById("toggleLabels"),
      search: document.getElementById("search"),
      onlySelectedLayer: document.getElementById("onlySelectedLayer"),
      tabRecintos: document.getElementById("tabRecintos"),
      tabCapas: document.getElementById("tabCapas"),
      list: document.getElementById("list"),
      mEntidades: document.getElementById("mEntidades"),
      mRecintos: document.getElementById("mRecintos"),
      mArea: document.getElementById("mArea"),
      mCapas: document.getElementById("mCapas"),
    };

    const ctx = el.canvas.getContext("2d");
    const colors = ["#0f766e", "#b45309", "#5b21b6", "#0369a1", "#be123c", "#4d7c0f", "#a16207", "#4338ca"];

    function setStatus(text, isError = false) {
      el.status.textContent = text;
      el.status.className = isError ? "status-text error" : "status-text";
    }

    async function getJson(url) {
      const res = await fetch(url);
      const body = await res.text();
      if (!res.ok) throw new Error(body || res.statusText);
      return JSON.parse(body);
    }

    async function loadPlanos() {
      setStatus("Cargando planos");
      state.planos = await getJson("/api/planos");
      el.planos.innerHTML = "";
      for (const plano of state.planos) {
        const opt = document.createElement("option");
        opt.value = plano.id;
        opt.textContent = `${plano.nombre} (${plano.n_recintos || 0} recintos)`;
        el.planos.appendChild(opt);
      }
      if (!state.planos.length) {
        setStatus("No hay planos importados", true);
        return;
      }
      await loadPlano(state.planos[0].id);
    }

    async function loadPlano(id) {
      state.selectedPlano = Number(id);
      state.selectedRecinto = null;
      state.selectedLayer = null;
      setStatus("Cargando geometrías");
      state.data = await getJson(`/api/plano?id=${encodeURIComponent(id)}`);
      updateMetrics();
      renderList();
      fitView();
      setStatus(`Plano ${state.data.plano.nombre} cargado`);
    }

    function updateMetrics() {
      const data = state.data;
      if (!data) return;
      const area = data.recintos.reduce((sum, r) => sum + Number(r.area_m2 || 0), 0);
      const layers = new Set(data.capas.map(c => c.layer || "0"));
      el.mEntidades.textContent = Number(data.plano.n_entidades || data.entidades.length).toLocaleString("es-MX");
      el.mRecintos.textContent = Number(data.recintos.length).toLocaleString("es-MX");
      el.mArea.textContent = area.toLocaleString("es-MX", { maximumFractionDigits: 1 });
      el.mCapas.textContent = layers.size.toLocaleString("es-MX");
    }

    function filteredRecintos() {
      if (!state.data) return [];
      const q = state.search.trim().toLowerCase();
      return state.data.recintos.filter(r => {
        const text = `${r.nombre || ""} ${r.categoria || ""} ${r.capa_recinto || ""} ${r.capa_texto || ""}`.toLowerCase();
        return !q || text.includes(q);
      });
    }

    function layerRows() {
      if (!state.data) return [];
      const q = state.search.trim().toLowerCase();
      const grouped = new Map();
      for (const row of state.data.capas) {
        const layer = row.layer || "0";
        if (!grouped.has(layer)) grouped.set(layer, { layer, total: 0, types: [] });
        const item = grouped.get(layer);
        item.total += Number(row.cantidad || 0);
        item.types.push(`${row.tipo}:${row.cantidad}`);
      }
      return [...grouped.values()]
        .filter(row => !q || row.layer.toLowerCase().includes(q))
        .sort((a, b) => b.total - a.total || a.layer.localeCompare(b.layer));
    }

    function renderList() {
      el.list.innerHTML = "";
      el.tabRecintos.classList.toggle("active", state.tab === "recintos");
      el.tabCapas.classList.toggle("active", state.tab === "capas");

      if (state.tab === "recintos") {
        for (const r of filteredRecintos()) {
          const row = document.createElement("div");
          row.className = "row" + (r.id === state.selectedRecinto ? " active" : "");
          row.innerHTML = `<strong>${escapeHtml(r.nombre || "Sin nombre")}</strong>
            <span>${escapeHtml(r.categoria || "Otro")} · ${fmt(r.area_m2)} m² · ${escapeHtml(r.capa_texto || "")}</span>`;
          row.addEventListener("click", () => {
            state.selectedRecinto = r.id;
            renderList();
            zoomToGeometry(r.geometry);
            draw();
          });
          el.list.appendChild(row);
        }
      } else {
        for (const rowData of layerRows()) {
          const row = document.createElement("div");
          row.className = "row" + (rowData.layer === state.selectedLayer ? " active" : "");
          row.innerHTML = `<strong>${escapeHtml(rowData.layer)}</strong>
            <span>${rowData.total.toLocaleString("es-MX")} entidades · ${escapeHtml(rowData.types.slice(0, 4).join(" · "))}</span>`;
          row.addEventListener("click", () => {
            state.selectedLayer = state.selectedLayer === rowData.layer ? null : rowData.layer;
            renderList();
            draw();
          });
          el.list.appendChild(row);
        }
      }
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    }

    function fmt(value) {
      return Number(value || 0).toLocaleString("es-MX", { maximumFractionDigits: 2 });
    }

    function resizeCanvas() {
      const rect = el.canvas.getBoundingClientRect();
      state.dpr = Math.max(1, window.devicePixelRatio || 1);
      el.canvas.width = Math.max(1, Math.floor(rect.width * state.dpr));
      el.canvas.height = Math.max(1, Math.floor(rect.height * state.dpr));
      ctx.setTransform(state.dpr, 0, 0, state.dpr, 0, 0);
      draw();
    }

    function worldToScreen(p) {
      return {
        x: p[0] * state.view.scale + state.view.tx,
        y: -p[1] * state.view.scale + state.view.ty,
      };
    }

    function screenToWorld(x, y) {
      return [
        (x - state.view.tx) / state.view.scale,
        -(y - state.view.ty) / state.view.scale,
      ];
    }

    function allVisibleGeometries() {
      if (!state.data) return [];
      const geoms = [];
      if (state.showBase) {
        for (const e of state.data.entidades) {
          if (state.onlySelectedLayer && state.selectedLayer && (e.layer || "0") !== state.selectedLayer) continue;
          geoms.push(e.geometry);
        }
      }
      if (state.showRecintos) {
        for (const r of state.data.recintos) geoms.push(r.geometry);
      }
      return geoms.filter(Boolean);
    }

    function geometryBbox(geom, box = [Infinity, Infinity, -Infinity, -Infinity]) {
      walkCoords(geom.coordinates, coord => {
        box[0] = Math.min(box[0], coord[0]);
        box[1] = Math.min(box[1], coord[1]);
        box[2] = Math.max(box[2], coord[0]);
        box[3] = Math.max(box[3], coord[1]);
      });
      return box;
    }

    function walkCoords(coords, fn) {
      if (!Array.isArray(coords)) return;
      if (typeof coords[0] === "number") {
        fn(coords);
        return;
      }
      for (const c of coords) walkCoords(c, fn);
    }

    function fitView() {
      const rect = el.canvas.getBoundingClientRect();
      const geoms = allVisibleGeometries();
      if (!geoms.length || rect.width <= 0 || rect.height <= 0) return;
      const box = geoms.reduce((acc, geom) => geometryBbox(geom, acc), [Infinity, Infinity, -Infinity, -Infinity]);
      if (!isFinite(box[0])) return;
      const w = Math.max(1, box[2] - box[0]);
      const h = Math.max(1, box[3] - box[1]);
      const pad = 32;
      const sx = (rect.width - pad * 2) / w;
      const sy = (rect.height - pad * 2) / h;
      const scale = Math.max(0.000001, Math.min(sx, sy));
      const cx = (box[0] + box[2]) / 2;
      const cy = (box[1] + box[3]) / 2;
      state.view.scale = scale;
      state.view.tx = rect.width / 2 - cx * scale;
      state.view.ty = rect.height / 2 + cy * scale;
      draw();
    }

    function zoomToGeometry(geom) {
      if (!geom) return;
      const rect = el.canvas.getBoundingClientRect();
      const box = geometryBbox(geom);
      const w = Math.max(1, box[2] - box[0]);
      const h = Math.max(1, box[3] - box[1]);
      const pad = 80;
      const scale = Math.max(0.000001, Math.min((rect.width - pad) / w, (rect.height - pad) / h));
      const cx = (box[0] + box[2]) / 2;
      const cy = (box[1] + box[3]) / 2;
      state.view.scale = scale;
      state.view.tx = rect.width / 2 - cx * scale;
      state.view.ty = rect.height / 2 + cy * scale;
    }

    function draw() {
      const rect = el.canvas.getBoundingClientRect();
      ctx.clearRect(0, 0, rect.width, rect.height);
      if (!state.data) return;

      ctx.save();
      ctx.lineCap = "round";
      ctx.lineJoin = "round";

      if (state.showBase) drawBase();
      if (state.showRecintos) drawRecintos();
      if (state.showLabels) drawLabels();
      if (state.hover) drawHover(state.hover);

      ctx.restore();
      el.scale.textContent = `${Math.round(state.view.scale * 1000) / 10}%`;
    }

    function drawBase() {
      for (const e of state.data.entidades) {
        const layer = e.layer || "0";
        if (state.onlySelectedLayer && state.selectedLayer && layer !== state.selectedLayer) continue;
        const selected = state.selectedLayer && layer === state.selectedLayer;
        ctx.strokeStyle = selected ? "#0f766e" : "#b6bec8";
        ctx.fillStyle = selected ? "rgba(15, 118, 110, 0.10)" : "rgba(99, 110, 123, 0.06)";
        ctx.lineWidth = selected ? 1.6 : 0.9;
        if (e.geometry.type === "Point") {
          const p = worldToScreen(e.geometry.coordinates);
          ctx.beginPath();
          ctx.arc(p.x, p.y, selected ? 3 : 2, 0, Math.PI * 2);
          ctx.fillStyle = selected ? "#0f766e" : "#87919c";
          ctx.fill();
        } else {
          drawGeometry(e.geometry, false);
        }
      }
    }

    function colorFor(text) {
      let hash = 0;
      for (let i = 0; i < String(text).length; i++) hash = ((hash << 5) - hash + String(text).charCodeAt(i)) | 0;
      return colors[Math.abs(hash) % colors.length];
    }

    function drawRecintos() {
      for (const r of filteredRecintos()) {
        const selected = r.id === state.selectedRecinto;
        const color = colorFor(r.categoria || r.nombre || r.id);
        ctx.strokeStyle = selected ? "#111827" : color;
        ctx.fillStyle = selected ? "rgba(180, 83, 9, 0.28)" : hexToRgba(color, 0.18);
        ctx.lineWidth = selected ? 2.5 : 1.8;
        if (r.geometry && r.geometry.type === "Point") drawPoint(r.geometry.coordinates, color, selected);
        else drawGeometry(r.geometry, true);
      }
    }

    function drawLabels() {
      ctx.save();
      ctx.font = "12px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
      ctx.textBaseline = "middle";
      for (const r of filteredRecintos()) {
        const c = centroid(r.geometry);
        if (!c) continue;
        const p = worldToScreen(c);
        const label = r.nombre || r.categoria || `#${r.id}`;
        if (p.x < -80 || p.y < -20 || p.x > el.canvas.clientWidth + 80 || p.y > el.canvas.clientHeight + 20) continue;
        const width = Math.min(170, ctx.measureText(label).width + 10);
        ctx.fillStyle = "rgba(255, 255, 255, 0.84)";
        ctx.strokeStyle = "rgba(47, 58, 68, 0.22)";
        roundRect(p.x - width / 2, p.y - 10, width, 20, 6);
        ctx.fill();
        ctx.stroke();
        ctx.fillStyle = "#1f2933";
        ctx.fillText(label, p.x - width / 2 + 5, p.y, width - 10);
      }
      ctx.restore();
    }

    function drawHover(item) {
      ctx.save();
      ctx.strokeStyle = "#b91c1c";
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 4]);
      drawGeometry(item.geometry, false);
      ctx.restore();
    }

    function drawGeometry(geom, fillPolygon) {
      if (!geom) return;
      if (geom.type === "Point") {
        drawPoint(geom.coordinates, "#87919c", false);
      } else if (geom.type === "LineString") {
        drawLine(geom.coordinates);
      } else if (geom.type === "Polygon") {
        drawPolygon(geom.coordinates, fillPolygon);
      } else if (geom.type === "MultiLineString") {
        for (const line of geom.coordinates) drawLine(line);
      } else if (geom.type === "MultiPolygon") {
        for (const poly of geom.coordinates) drawPolygon(poly, fillPolygon);
      } else if (geom.type === "GeometryCollection") {
        for (const g of geom.geometries || []) drawGeometry(g, fillPolygon);
      }
    }

    function drawPoint(coord, color, selected) {
      const p = worldToScreen(coord);
      ctx.save();
      ctx.beginPath();
      ctx.fillStyle = selected ? "#b45309" : color;
      ctx.strokeStyle = selected ? "#111827" : "#ffffff";
      ctx.lineWidth = selected ? 2.4 : 1.5;
      ctx.arc(p.x, p.y, selected ? 6 : 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.restore();
    }

    function drawLine(coords) {
      if (!coords || coords.length < 2) return;
      ctx.beginPath();
      coords.forEach((coord, i) => {
        const p = worldToScreen(coord);
        if (i === 0) ctx.moveTo(p.x, p.y);
        else ctx.lineTo(p.x, p.y);
      });
      ctx.stroke();
    }

    function drawPolygon(rings, fillPolygon) {
      if (!rings || !rings.length) return;
      ctx.beginPath();
      for (const ring of rings) {
        ring.forEach((coord, i) => {
          const p = worldToScreen(coord);
          if (i === 0) ctx.moveTo(p.x, p.y);
          else ctx.lineTo(p.x, p.y);
        });
        ctx.closePath();
      }
      if (fillPolygon) ctx.fill("evenodd");
      ctx.stroke();
    }

    function centroid(geom) {
      const points = [];
      walkCoords(geom.coordinates, coord => points.push(coord));
      if (!points.length) return null;
      const sum = points.reduce((acc, p) => [acc[0] + p[0], acc[1] + p[1]], [0, 0]);
      return [sum[0] / points.length, sum[1] / points.length];
    }

    function hexToRgba(hex, alpha) {
      const n = parseInt(hex.slice(1), 16);
      const r = (n >> 16) & 255;
      const g = (n >> 8) & 255;
      const b = n & 255;
      return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }

    function roundRect(x, y, w, h, r) {
      ctx.beginPath();
      ctx.moveTo(x + r, y);
      ctx.arcTo(x + w, y, x + w, y + h, r);
      ctx.arcTo(x + w, y + h, x, y + h, r);
      ctx.arcTo(x, y + h, x, y, r);
      ctx.arcTo(x, y, x + w, y, r);
      ctx.closePath();
    }

    function pickRecinto(x, y) {
      if (!state.data || !state.showRecintos) return null;
      const world = screenToWorld(x, y);
      for (let i = state.data.recintos.length - 1; i >= 0; i--) {
        const r = state.data.recintos[i];
        if (pointInGeometry(world, r.geometry)) return r;
      }
      return null;
    }

    function pointInGeometry(point, geom) {
      if (!geom) return false;
      if (geom.type === "Polygon") return pointInPolygon(point, geom.coordinates);
      if (geom.type === "MultiPolygon") return geom.coordinates.some(poly => pointInPolygon(point, poly));
      return false;
    }

    function pointInPolygon(point, rings) {
      if (!rings || !rings.length) return false;
      let inside = ringContains(point, rings[0]);
      for (let i = 1; i < rings.length && inside; i++) {
        if (ringContains(point, rings[i])) inside = false;
      }
      return inside;
    }

    function ringContains(point, ring) {
      const [x, y] = point;
      let inside = false;
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const xi = ring[i][0], yi = ring[i][1];
        const xj = ring[j][0], yj = ring[j][1];
        const intersect = ((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / ((yj - yi) || 1e-12) + xi);
        if (intersect) inside = !inside;
      }
      return inside;
    }

    function showTooltip(item, x, y) {
      if (!item) {
        el.tooltip.style.display = "none";
        return;
      }
      el.tooltip.innerHTML = `<b>${escapeHtml(item.nombre || "Sin nombre")}</b>
        ${escapeHtml(item.categoria || "Otro")} · ${fmt(item.area_m2)} m²<br>
        texto: ${escapeHtml(item.capa_texto || "")}<br>
        recinto: ${escapeHtml(item.capa_recinto || "")}`;
      el.tooltip.style.left = `${x}px`;
      el.tooltip.style.top = `${y}px`;
      el.tooltip.style.display = "block";
    }

    el.planos.addEventListener("change", event => loadPlano(event.target.value).catch(showError));
    el.fit.addEventListener("click", fitView);
    el.toggleBase.addEventListener("click", () => {
      state.showBase = !state.showBase;
      el.toggleBase.classList.toggle("active", state.showBase);
      draw();
    });
    el.toggleRecintos.addEventListener("click", () => {
      state.showRecintos = !state.showRecintos;
      el.toggleRecintos.classList.toggle("active", state.showRecintos);
      draw();
    });
    el.toggleLabels.addEventListener("click", () => {
      state.showLabels = !state.showLabels;
      el.toggleLabels.classList.toggle("active", state.showLabels);
      draw();
    });
    el.search.addEventListener("input", event => {
      state.search = event.target.value;
      renderList();
      draw();
    });
    el.onlySelectedLayer.addEventListener("change", event => {
      state.onlySelectedLayer = event.target.checked;
      draw();
    });
    el.tabRecintos.addEventListener("click", () => {
      state.tab = "recintos";
      renderList();
    });
    el.tabCapas.addEventListener("click", () => {
      state.tab = "capas";
      renderList();
    });

    el.canvas.addEventListener("pointerdown", event => {
      state.dragging = true;
      state.last = { x: event.clientX, y: event.clientY };
      el.canvas.classList.add("dragging");
      el.canvas.setPointerCapture(event.pointerId);
    });
    el.canvas.addEventListener("pointerup", event => {
      state.dragging = false;
      state.last = null;
      el.canvas.classList.remove("dragging");
      el.canvas.releasePointerCapture(event.pointerId);
    });
    el.canvas.addEventListener("pointermove", event => {
      const rect = el.canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const world = screenToWorld(x, y);
      el.coords.textContent = `x ${Math.round(world[0] * 100) / 100} · y ${Math.round(world[1] * 100) / 100}`;
      if (state.dragging && state.last) {
        state.view.tx += event.clientX - state.last.x;
        state.view.ty += event.clientY - state.last.y;
        state.last = { x: event.clientX, y: event.clientY };
        draw();
        return;
      }
      state.hover = pickRecinto(x, y);
      showTooltip(state.hover, x, y);
      draw();
    });
    el.canvas.addEventListener("mouseleave", () => {
      state.hover = null;
      showTooltip(null);
      draw();
    });
    el.canvas.addEventListener("click", event => {
      const rect = el.canvas.getBoundingClientRect();
      const item = pickRecinto(event.clientX - rect.left, event.clientY - rect.top);
      if (item) {
        state.selectedRecinto = item.id;
        state.tab = "recintos";
        renderList();
        draw();
      }
    });
    el.canvas.addEventListener("wheel", event => {
      event.preventDefault();
      const rect = el.canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      const before = screenToWorld(x, y);
      const factor = event.deltaY < 0 ? 1.12 : 0.89;
      state.view.scale = Math.max(0.000001, Math.min(100, state.view.scale * factor));
      state.view.tx = x - before[0] * state.view.scale;
      state.view.ty = y + before[1] * state.view.scale;
      draw();
    }, { passive: false });

    function showError(err) {
      console.error(err);
      setStatus(err.message || String(err), true);
    }

    window.addEventListener("resize", resizeCanvas);
    resizeCanvas();
    loadPlanos().catch(showError);
  </script>
</body>
</html>
"""


class ViewerHandler(BaseHTTPRequestHandler):
    db: Db

    def log_message(self, fmt: str, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self.send_text(INDEX_HTML, "text/html; charset=utf-8")
            elif parsed.path == "/api/planos":
                self.send_json(self.db.planos())
            elif parsed.path == "/api/plano":
                query = parse_qs(parsed.query)
                plano_id = int(query.get("id", ["0"])[0])
                max_entidades = int(query.get("max_entidades", [str(MAX_ENTIDADES_DEFAULT)])[0])
                self.send_json(self.db.plano(plano_id, max_entidades=max_entidades))
            elif parsed.path == "/favicon.ico":
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except KeyError as exc:
            self.send_error(HTTPStatus.NOT_FOUND, str(exc))
        except Exception as exc:
            log.exception("Error atendiendo %s", self.path)
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def send_text(self, body: str, content_type: str):
        data = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, body):
        data = json.dumps(body, ensure_ascii=False, default=json_default).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    parser = argparse.ArgumentParser(description="Visor local de recintos desde PostGIS.")
    parser.add_argument("--dsn", default=DSN_DEFAULT, help="DSN PostgreSQL")
    parser.add_argument("--host", default="127.0.0.1", help="Host del servidor HTTP")
    parser.add_argument("--port", type=int, default=8765, help="Puerto del servidor HTTP")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    mimetypes.add_type("text/html", ".html")

    ViewerHandler.db = Db(args.dsn)

    try:
        with ViewerHandler.db.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception as exc:
        raise SystemExit(f"No se pudo conectar a PostgreSQL: {exc}") from exc

    server = ThreadingHTTPServer((args.host, args.port), ViewerHandler)
    url = f"http://{args.host}:{args.port}/"
    log.info("Visor de recintos listo: %s", url)
    log.info("Ctrl+C para detener.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
