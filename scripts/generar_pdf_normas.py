#!/usr/bin/env python3
"""Genera docs/NORMAS_DWG.pdf desde el contenido de la guía."""

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "NORMAS_DWG.pdf"

FONT_DIR = Path("/System/Library/Fonts/Supplemental")
F_REG    = str(FONT_DIR / "Arial.ttf")
F_BOLD   = str(FONT_DIR / "Arial Bold.ttf")
F_ITALIC = str(FONT_DIR / "Arial Italic.ttf")

TEAL     = (15, 118, 110)
TEAL_DK  = (6, 95, 70)
INK      = (31, 41, 51)
MUTED    = (102, 114, 127)
LIGHT    = (244, 245, 247)
LINE     = (214, 218, 224)
WHITE    = (255, 255, 255)
GREEN_BG = (240, 253, 250)
AMBER_BG = (255, 251, 235)
RED_BG   = (254, 242, 242)


class PDF(FPDF):
    def header(self):
        self.set_fill_color(*TEAL)
        self.rect(0, 0, 210, 10, "F")

    def footer(self):
        self.set_y(-14)
        self.set_font("regular", size=8)
        self.set_text_color(*MUTED)
        self.cell(0, 8, f"dwg-to-gis  \u00b7  Guia de entrega de planos DWG  \u00b7  Pagina {self.page_no()}", align="C")


def build():
    pdf = PDF(orientation="P", unit="mm", format="A4")
    pdf.add_font("regular", style="",  fname=F_REG)
    pdf.add_font("regular", style="B", fname=F_BOLD)
    pdf.add_font("regular", style="I", fname=F_ITALIC)
    pdf.set_auto_page_break(True, margin=18)

    W = pdf.w - 36

    def lm():
        return pdf.l_margin

    def h1(text):
        pdf.ln(4)
        pdf.set_fill_color(*TEAL)
        pdf.set_text_color(*WHITE)
        pdf.set_font("regular", style="B", size=13)
        pdf.cell(W, 9, text, fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(*INK)
        pdf.ln(2)

    def h2(text):
        pdf.ln(5)
        pdf.set_font("regular", style="B", size=11)
        pdf.set_text_color(*TEAL)
        pdf.cell(W, 7, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_draw_color(*TEAL)
        pdf.set_line_width(0.4)
        pdf.line(lm(), pdf.get_y(), lm() + W, pdf.get_y())
        pdf.set_text_color(*INK)
        pdf.set_draw_color(0, 0, 0)
        pdf.ln(2)

    def h3(text):
        pdf.ln(3)
        pdf.set_font("regular", style="B", size=10)
        pdf.set_text_color(*INK)
        pdf.cell(W, 6, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1)

    def para(text, indent=0):
        if not text.strip():
            return
        pdf.set_font("regular", size=9.5)
        pdf.set_text_color(*INK)
        pdf.set_x(lm() + indent)
        pdf.multi_cell(W - indent, 5.5, text)
        pdf.ln(1)

    def bullet(text, indent=5):
        pdf.set_font("regular", size=9.5)
        pdf.set_text_color(*INK)
        pdf.set_x(lm() + indent)
        pdf.cell(5, 5.5, "\u2022")
        pdf.set_x(lm() + indent + 5)
        pdf.multi_cell(W - indent - 5, 5.5, text)

    def checklist_item(text, indent=5):
        pdf.set_font("regular", size=9.5)
        pdf.set_text_color(*INK)
        pdf.set_x(lm() + indent)
        pdf.cell(7, 5.5, "[ ]")
        pdf.set_x(lm() + indent + 7)
        pdf.multi_cell(W - indent - 7, 5.5, text)

    def mono(text):
        pdf.set_font("regular", size=8.5)
        pdf.set_fill_color(*LIGHT)
        pdf.set_draw_color(*LINE)
        pdf.set_line_width(0.3)
        lines = text.strip().splitlines()
        block_h = len(lines) * 5.2 + 4
        x0, y0 = lm(), pdf.get_y()
        pdf.rect(x0, y0, W, block_h, "FD")
        pdf.set_y(y0 + 2)
        for line in lines:
            pdf.set_x(x0 + 4)
            pdf.cell(W - 8, 5.2, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2)

    def trow(cells, widths, header=False, bg=None):
        if bg:
            pdf.set_fill_color(*bg)
        elif header:
            pdf.set_fill_color(*LIGHT)
        else:
            pdf.set_fill_color(*WHITE)
        pdf.set_draw_color(*LINE)
        pdf.set_line_width(0.25)
        pdf.set_font("regular", style="B" if header else "", size=8.5)
        pdf.set_text_color(*MUTED if header else INK)
        y0, x0 = pdf.get_y(), lm()
        rh = 7
        for cell, w in zip(cells, widths):
            pdf.rect(x0, y0, w, rh, "FD")
            pdf.set_xy(x0 + 2, y0 + 1)
            pdf.cell(w - 4, rh - 2, str(cell)[:62])
            x0 += w
        pdf.set_y(y0 + rh)

    def info_box(title, body, bg, border_color, text_color):
        pdf.set_fill_color(*bg)
        pdf.set_draw_color(*border_color)
        pdf.set_line_width(0.4)
        x0, y0 = lm(), pdf.get_y()
        lines = body.strip().splitlines()
        h = len(lines) * 5.5 + 10
        pdf.rect(x0, y0, W, h, "FD")
        pdf.set_xy(x0 + 4, y0 + 2)
        pdf.set_font("regular", style="B", size=9)
        pdf.set_text_color(*text_color)
        pdf.cell(W - 8, 5, title)
        for i, line in enumerate(lines):
            pdf.set_xy(x0 + 4, y0 + 7 + i * 5.5)
            pdf.set_font("regular", size=8.5)
            pdf.cell(W - 8, 5, line)
        pdf.ln(h + 3)
        pdf.set_text_color(*INK)

    # ── PORTADA ──────────────────────────────────────────────────────────────
    pdf.add_page()
    pdf.set_left_margin(18)
    pdf.set_right_margin(18)
    pdf.set_fill_color(*TEAL)
    pdf.rect(0, 0, 210, 297, "F")

    pdf.set_text_color(*WHITE)
    pdf.set_font("regular", style="B", size=24)
    pdf.set_y(80)
    pdf.cell(0, 14, "Guia de entrega de planos DWG", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("regular", size=12)
    pdf.cell(0, 8, "Pipeline dwg-to-gis \u2014 Deteccion automatica de recintos", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(12)
    pdf.set_font("regular", style="I", size=10)
    pdf.set_text_color(200, 235, 232)
    pdf.cell(0, 7, "Para arquitectos y dibujantes", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_fill_color(0, 100, 92)
    pdf.rect(0, 258, 210, 39, "F")
    pdf.set_y(270)
    pdf.set_font("regular", size=9)
    pdf.set_text_color(200, 235, 232)
    pdf.cell(0, 6, "dwg-to-gis  \u00b7  docs/NORMAS_DWG.md", align="C")

    # ── PAGINA 2 ─────────────────────────────────────────────────────────────
    pdf.add_page()
    pdf.set_text_color(*INK)

    pdf.set_font("regular", style="B", size=17)
    pdf.set_text_color(*TEAL)
    pdf.ln(4)
    pdf.cell(W, 11, "Guia de entrega de planos DWG", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(*INK)
    pdf.set_font("regular", size=10)
    pdf.cell(W, 6, "dwg-to-gis \u2014 Deteccion automatica de recintos", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_draw_color(*TEAL)
    pdf.set_line_width(0.8)
    pdf.line(lm(), pdf.get_y() + 2, lm() + W, pdf.get_y() + 2)
    pdf.ln(6)

    # 1. Como funciona
    h1("1. Como funciona la deteccion automatica")
    para(
        "El sistema procesa el archivo DWG en tres estrategias en orden de precision. "
        "Cada recinto detectado queda marcado con su nivel de confianza."
    )
    widths = [56, 26, W - 82]
    trow(["Estrategia", "Confianza", "Que necesita"], widths, header=True)
    trow(["S1 \u2014 Polilineas cerradas", "Alta",  "Recinto como polilinea cerrada con etiqueta dentro"], widths, bg=GREEN_BG)
    trow(["S2 \u2014 Red de muros",        "Media", "Muros como lineas continuas en capa nombrada + etiqueta"], widths, bg=AMBER_BG)
    trow(["S3 \u2014 Ray-casting",         "Baja",  "Cualquier geometria; area estimada por proyeccion de rayos"], widths, bg=RED_BG)
    pdf.ln(3)
    para("La mejor calidad se logra con S1. S2 y S3 son fallback automatico para planos que no siguen S1.")

    # 2. Opcion 1
    h1("2. Opcion 1 (recomendada) \u2014 Polilineas cerradas por recinto")
    para(
        "Cada habitacion o local debe estar dibujado como una sola polilinea cerrada "
        "(LWPOLYLINE o POLYLINE con Closed = Yes) que trace exactamente el perimetro del espacio."
    )
    para("La etiqueta de nombre (TEXT o MTEXT) debe estar DENTRO de esa polilinea.")
    mono(
        "+------------------+\n"
        "|                  |\n"
        "|   HABITACION     |   <- TEXT o MTEXT dentro de la polilinea\n"
        "|      101         |\n"
        "|                  |\n"
        "+------------------+\n"
        "  ^ LWPOLYLINE cerrada, capa: cualquiera"
    )

    h3("Checklist para Opcion 1")
    for item in [
        "Cada recinto es una polilinea cerrada (propiedad Closed = Yes)",
        "La etiqueta de nombre esta DENTRO del poligono, no sobre el borde del muro",
        "La etiqueta es una sola entidad TEXT o MTEXT (no partir el nombre en varias lineas)",
        "Las etiquetas de nombre estan en una capa de texto reconocida (ver tabla de capas)",
    ]:
        checklist_item(item)

    # 3. Opcion 2
    h2("3. Opcion 2 (aceptable) \u2014 Muros como lineas")
    para(
        "Si los muros estan dibujados como lineas individuales (LINE), el sistema los une "
        "automaticamente para reconstruir los recintos, siempre que:"
    )
    for item in [
        "Las lineas se toquen en los extremos (sin huecos visibles en esquinas)",
        "Todos los muros esten en una sola capa con un nombre descriptivo",
        "Cada recinto tenga su etiqueta de texto dentro del area correspondiente",
    ]:
        bullet(item)
    pdf.ln(2)
    info_box(
        "Tolerancia de cierre de esquinas",
        "El sistema acepta huecos de hasta 35 cm en los extremos de lineas.\n"
        "Huecos mayores rompen la deteccion del recinto y este cae a S3 (ray-casting).",
        AMBER_BG, (180, 83, 9), (120, 53, 15)
    )

    # ── PAGINA 3 ─────────────────────────────────────────────────────────────
    pdf.add_page()

    # 4. Capas
    h1("4. Capas reconocidas")
    h3("Capas de texto (etiquetas de espacios)")
    w2 = [50, W - 50]
    trow(["Convencion", "Capas que debe usar"], w2, header=True)
    trow(["D'L / Universal", "TX . 01  \u00b7  TX  \u00b7  Ar-Texto  \u00b7  T B  \u00b7  AREAS"], w2)
    trow(["BOR-10x",         "A-AREA-IDEN  \u00b7  G-ANNO-TEXT"], w2)
    trow(["AIA / NCS",       "A-AREA  \u00b7  Q-SPCQ  \u00b7  A-ANNO-NOTE"], w2)
    pdf.ln(2)
    para("Si el plano viene de otra oficina con capas distintas, indicarlo al entregar el archivo para que se configure el sistema.")

    h3("Capas de muros (para Opcion 2)")
    para("El sistema reconoce automaticamente capas cuyos nombres contengan:")
    mono("muro  \u00b7  muros  \u00b7  pared  \u00b7  paredes  \u00b7  wall  \u00b7  walls  \u00b7  tabique  \u00b7  cerramiento  \u00b7  partition")
    para("Si la capa de muros no se detecta automaticamente, indicar el nombre exacto al importar.")

    # 5. Reglas
    h1("5. Reglas de dibujo")

    h3("5.1  No partir etiquetas en varias entidades de texto")
    mono(
        "MAL  -- 3 entidades TEXT:   SALA   DE   ESTAR\n"
        "BIEN -- una sola MTEXT:     SALA DE ESTAR"
    )

    h3("5.2  La etiqueta debe estar DENTRO del recinto")
    para(
        "El sistema asigna el texto al recinto que lo contiene (point-in-polygon). "
        "Si el texto esta fuera, el area puede ser incorrecta. "
        "Colocar el texto en el centro visual del espacio, sin tocar el borde del muro."
    )

    h3("5.3  No mezclar anotaciones con etiquetas de nombre")
    w3 = [72, W - 72]
    trow(["Contenido", "Capa correcta"], w3, header=True)
    trow(["Nombre del espacio (HABITACION 101)", "TX . 01 / A-AREA-IDEN (segun convencion)"], w3)
    trow(["Area numerica (96.25 m2)",             "Misma capa \u2014 filtrada automaticamente"], w3)
    trow(["Cotas, pendientes, NPT, SUBE, BAJA",   "COTAS / NOTAS / G-ANNO-TEXT"], w3)
    trow(["Ejes de referencia (A, B, 1, 1-1)",    "EJES / A-GRID"], w3)
    trow(["Muros, trabes, columnas",               "A-WALL / MURO / PARED"], w3)
    pdf.ln(3)

    h3("5.4  No usar XREFs \u2014 incrustar todo antes de entregar")
    para("Si el plano usa referencias externas (XREF), el contenido no estara disponible al convertir a DXF.")
    info_box(
        "En AutoCAD antes de guardar:",
        "Insertar -> Referencia externa -> clic derecho -> Bind -> Bind\n"
        "Confirmar en XREF Manager que no quede ninguna referencia pendiente.",
        GREEN_BG, TEAL, TEAL_DK
    )

    h3("5.5  Planos con multiples vistas en una hoja")
    for item in [
        "Mantener las plantas bien separadas horizontalmente (minimo 1 m entre ellas)",
        "Las etiquetas de cada planta deben estar dentro del area de esa planta",
        "El sistema detecta automaticamente los grupos de vistas para evitar cruces",
    ]:
        bullet(item)

    # ── PAGINA 4 ─────────────────────────────────────────────────────────────
    pdf.add_page()

    # 6. Tipos de plano
    h1("6. Tipos de plano procesables")
    w4 = [85, 16, W - 101]
    trow(["Tipo de plano", "OK?", "Notas"], w4, header=True)
    rows6 = [
        ("Planta arquitectonica de distribucion", "SI",  "Recintos + etiquetas", GREEN_BG),
        ("Planta de estacionamiento con cajones",  "SI",  "Si cada cajon tiene polilinea + etiqueta", GREEN_BG),
        ("Planta de conjunto / sitio",             "(!)", "Solo si los lotes tienen polilineas + etiquetas", AMBER_BG),
        ("Planta de azotea (solo cubiertas)",      "NO",  "Sin recintos habitables", RED_BG),
        ("Fachada / alzado / corte transversal",   "NO",  "Sin plantas en planta", RED_BG),
        ("Topografico / levantamiento UTM",        "NO",  "Sin espacios arquitectonicos", RED_BG),
        ("Plano de instalaciones (electrico...)",  "NO",  "Polilineas son recorridos, no recintos", RED_BG),
    ]
    for tipo, ok, nota, bg in rows6:
        trow([tipo, ok, nota], w4, bg=bg)
    pdf.ln(4)

    # 7. Confianza
    h1("7. Sistema de confianza del resultado")
    para("Cada recinto detectado lleva un indicador de confianza que indica la calidad del area calculada:")
    w5 = [22, 60, W - 82]
    trow(["Nivel", "Metodo", "Que indica"], w5, header=True)
    trow(["alta",  "Polilinea cerrada con texto dentro",      "Area exacta del dibujo"], w5, bg=GREEN_BG)
    trow(["media", "Polilinea reconstruida desde muros",      "Area calculada; posibles imprecisiones por huecos"], w5, bg=AMBER_BG)
    trow(["baja",  "Ray-casting (proyeccion de rayos)",       "Area estimada; puede ser incorrecta"], w5, bg=RED_BG)
    pdf.ln(3)
    para("Para trabajo de inventario o certificacion de superficies, solo usar recintos con confianza alta o media.")

    # 8. Checklist
    h1("8. Checklist de entrega")
    items_check = [
        "Es una planta de distribucion arquitectonica (no conjunto, azotea, alzado ni topografia)",
        "No tiene XREFs pendientes \u2014 todo incrustado (Bind) antes de guardar",
        "Cada recinto tiene una polilinea cerrada (Closed = Yes)",
        "Cada recinto tiene una etiqueta de nombre en una capa reconocida",
        "La etiqueta esta dentro de la polilinea correspondiente",
        "El nombre del espacio es una sola entidad TEXT o MTEXT (no partido en varios textos)",
        "Las cotas, pendientes y notas constructivas estan en capas distintas a las de texto",
        "Se indica la convencion de capas de la oficina (D'L, BOR-10x, AIA/NCS u otra)",
    ]
    pdf.set_fill_color(*LIGHT)
    pdf.set_draw_color(*LINE)
    pdf.set_line_width(0.3)
    rh = 8
    x0, y0 = lm(), pdf.get_y()
    total_h = len(items_check) * rh + 4
    pdf.rect(x0, y0, W, total_h, "FD")
    for i, item in enumerate(items_check):
        pdf.set_xy(x0 + 4, y0 + 2 + i * rh)
        pdf.set_font("regular", size=9.5)
        pdf.set_text_color(*INK)
        pdf.cell(8, rh - 1, "[ ]")
        pdf.set_x(x0 + 13)
        pdf.cell(W - 14, rh - 1, item)
    pdf.ln(total_h + 4)

    # 9. Resumen
    h1("9. Resumen rapido")
    items_s = [
        ("1", "Mejor resultado",     "Polilinea cerrada por recinto + etiqueta dentro + capas de texto estandar"),
        ("2", "Resultado aceptable", "Muros como lineas continuas en una capa nombrada + etiquetas dentro del area"),
        ("3", "Siempre",             "Incrustar XREFs, no partir etiquetas, no poner texto fuera del recinto"),
        ("4", "Indicar",             "La convencion de capas de la oficina si no es alguna de las listadas"),
    ]
    for num, title, desc in items_s:
        pdf.set_fill_color(*TEAL)
        x0, y0 = lm(), pdf.get_y()
        pdf.rect(x0, y0, 8, 8, "F")
        pdf.set_xy(x0 + 1, y0 + 1)
        pdf.set_font("regular", style="B", size=8)
        pdf.set_text_color(*WHITE)
        pdf.cell(6, 6, num, align="C")
        pdf.set_text_color(*INK)
        pdf.set_xy(x0 + 11, y0 + 0.5)
        pdf.set_font("regular", style="B", size=9.5)
        pdf.cell(45, 5, title)
        pdf.set_xy(x0 + 11, y0 + 5.5)
        pdf.set_font("regular", size=9)
        pdf.multi_cell(W - 11, 4.5, desc)
        pdf.ln(2)

    pdf.output(str(OUT))
    size_kb = OUT.stat().st_size // 1024
    print(f"PDF generado: {OUT}  ({size_kb} KB, {pdf.page} paginas)")


if __name__ == "__main__":
    build()
