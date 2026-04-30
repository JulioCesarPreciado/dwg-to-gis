# Normas de homologación DWG para inventario de superficies

Guía para que el script `inventario_superficies.py` funcione correctamente con planos de distintos despachos.

---

## Qué tipo de plano debe entregarse

Solo se procesan **plantas arquitectónicas de distribución de espacios interiores**.  
La condición mínima es que el plano contenga simultáneamente:

1. **Polilíneas cerradas** que delimiten cada recinto (`LWPOLYLINE` o `POLYLINE` con flag `Closed = Yes`)
2. **Etiquetas de texto** con el nombre del espacio dentro o cerca de cada recinto (`TEXT` o `MTEXT`)

Si falta cualquiera de los dos, el plano no es procesable.

| Tipo de plano | ¿Procesable? | Motivo |
|---|---|---|
| Planta baja / alta / sótano con locales numerados | ✅ Sí | Polilíneas de recinto + etiquetas en capas reconocidas |
| Planta SIAPA (con giros y aforos) | ✅ Sí | Igual que arriba + datos de uso por local |
| Planta de sótano con cajones de estacionamiento | ✅ Sí | Si los cajones tienen polilínea cerrada y etiqueta |
| Planta de conjunto / sitio | ❌ No | Sin polilíneas de recinto individuales por local |
| Planta de azoteas | ❌ No | Sin recintos cerrados; solo anotaciones de pendiente y drenes |
| Levantamiento topográfico (UTM, curvas de nivel) | ❌ No | Sin espacios arquitectónicos |
| Fachada / alzado / corte | ❌ No | Sin recintos en planta |
| Plano de instalaciones (eléctrico, hidráulico, etc.) | ❌ No | Polilíneas son de recorrido, no de recintos |

---

## Capas requeridas (por despacho / convención)

El script reconoce las capas de los despachos validados. Si el plano viene de otro despacho, el dibujante **debe usar una de estas convenciones** o coordinar para añadir la nueva capa al script.

| Convención | Capa de etiquetas de espacio | Capa de polilíneas de recinto | Despacho validado |
|---|---|---|---|
| AIA / NCS | `A-AREA-IDEN` | `A-AREA-BNDY` | BOR-10x ✅ |
| D'L | `TX . 01` | Varias (`MC`, `EJ`, `P1`, etc.) + 3DFACE | D'L ✅ |
| Universal nueva | `A-SPACE-LABEL` | `A-SPACE-BNDY` | (recomendado para nuevos proyectos) |

Para añadir un nuevo despacho: editar `CAPAS_TEXTO` y `CAPAS_BNDY` en el script (`inventario_superficies.py`, líneas ~30-40).

---

## Por qué fallan planos que "parecen correctos"

Estos son los problemas encontrados en los planos de prueba que no funcionaron:

### A. Contenido dentro de XREFs no explotados
**Planos afectados:** ARQ-01, ARQ-02, ARQ-03  
El archivo DWG contiene solo un bloque `INSERT` que apunta a un archivo externo (XREF). Al convertir a DXF, el contenido del XREF no se incrusta — el archivo queda prácticamente vacío.

✅ **Solución:** Antes de entregar, ejecutar en AutoCAD:  
`XREF → Bind → Bind (no Insert)` o `_XBIND` para incrustar todas las referencias externas.  
Verificar con `XREF Manager` que no quede ningún XREF pendiente.

---

### B. Polilíneas de recinto en capas incorrectas
**Planos afectados:** MO-Plano (A-101, A-102, A-103), La Riioja Final, PROYECTO VALDEPENAS, KIVA-LA VENTA  
El plano tiene texto con nombres de espacios en la capa correcta (`A-AREA-IDEN` en el caso de MO) pero las polilíneas están en capas de muro (`A-WALL`) o mobiliario, no en la capa de recintos (`A-AREA-BNDY`).

| Plano | Tiene etiquetas | Tiene polilíneas | Capa de polilíneas real | Problema |
|---|---|---|---|---|
| MO-A-101 | ✅ `A-AREA-IDEN` (34) | ✅ `A-WALL` (6) | `A-WALL` | Capa equivocada |
| MO-A-102 | ✅ `A-AREA-IDEN` (32) | ❌ ninguna | — | Sin polilíneas |
| La Riioja | ❌ sin capa reconocida | ✅ `MOBILIARIO FIJO`, `MACHUELO` | Mobiliario/muros | Ambas capas equivocadas |
| KIVA | ❌ sin capa reconocida | ✅ `DWG-ESPACO-BALIZAMIENTO` | Convención propia | Capa desconocida |

✅ **Solución:** Dibujar o mover las polilíneas de cada recinto a la capa `A-AREA-BNDY`.  
Una polilínea en `A-AREA-BNDY` debe corresponder **exactamente** a un espacio: local, terraza, pasillo, baño, etc.  
Las polilíneas de muros, ejes, mobiliario y otros elementos **no deben** estar en `A-AREA-BNDY`.

---

### C. Etiquetas en capas no reconocidas
**Planos afectados:** La Riioja Final, PROYECTO VALDEPENAS, KIVA-LA VENTA  
Los nombres de espacios están en capas como `A-NOTE-100`, `Txt`, `Carga`, `DWG-TREX-TEXTOS` que el script no lee.

✅ **Solución:** Mover las etiquetas de nombre de espacio a la capa `A-AREA-IDEN` (o `TX . 01` si se usa la convención D'L).  
Las anotaciones de área, pendientes, cotas y notas constructivas deben ir en otras capas.

---

### D. Tipo de plano incorrecto
**Planos afectados:** BOR-100 (conjunto), BOR-103 (azoteas), LEV UTM (topográfico)  
No son plantas de distribución de espacios. No tienen los dos ingredientes base (polilíneas de recinto + etiquetas).

✅ **Solución:** No enviar este tipo de planos al proceso. Solo plantas de distribución arquitectónica.

---

## Reglas de dibujo para el dibujante

### 1. Una entidad de texto por espacio — no partir etiquetas

❌ Mal — 4 entidades TEXT separadas:
```
ELEVADOR PARA   |   PERSONAS CON   |   CAPACIDADES   |   DIFERENTES
```

✅ Bien — un solo MTEXT o TEXT completo:
```
VENTILACION MECANICA     (una sola entidad MTEXT en capa A-AREA-IDEN)
```

---

### 2. La etiqueta debe estar **dentro** de la polilínea del recinto

El script hace *point-in-polygon* primero (`[✓]` — máxima confianza).  
Si la etiqueta está fuera, cae a búsqueda por distancia (`[?]` — radio máx. 15 m, menos confiable).

✅ Colocar el texto en el centroide visual del espacio, nunca sobre el borde del muro.

---

### 3. La polilínea de cada recinto debe estar cerrada (`CLOSED = 1`)

Sin el flag `closed`, el script usa tolerancia geométrica (primer ≈ último punto < 5 cm). Funciona, pero es frágil.

✅ Usar siempre `PLINE` → `Cerrar` o activar `Closed = Yes` en el inspector de propiedades.

---

### 4. Separar nombre del espacio de anotación de área

❌ Mal — nombre y área en un solo texto:
```
LOCAL 101 — 96.25 m²
```

✅ Bien — dos entidades distintas en `A-AREA-IDEN`:
```
LOCAL 101        (etiqueta)
96.25 m²         (anotación — el script la ignora automáticamente)
```

---

### 5. No mezclar anotaciones constructivas con etiquetas de espacio

| Contenido | Capa correcta |
|---|---|
| Nombres de espacios (`LOCAL 101`, `TERRAZA 201`, `BAÑO H`) | `A-AREA-IDEN` |
| Anotaciones de área (`96.25 m²`) | `A-AREA-IDEN` (filtradas automáticamente) |
| Polilíneas de recintos | `A-AREA-BNDY` |
| Indicaciones constructivas (`SUBE`, `BAJA`, `PROYECCIÓN`, `PENDIENTE`) | `G-ANNO-TEXT` |
| Ejes de referencia (`X`, `Y-1`, `Y-4`) | `A-GRID` |
| Muros, trabes, columnas | `A-WALL`, `A-COLS`, etc. |
| Mobiliario | `A-FURN` o `MOBILIARIO` |

---

### 6. No usar XREFs — incrustar todo antes de entregar

Si el plano usa archivos de referencia externa (XREF), el contenido no estará disponible en el DXF resultante.

✅ En AutoCAD antes de guardar:  
`Insertar → Referencia externa → clic derecho → Bind → Bind`  
Confirmar que `XREF Manager` muestre "Sin referencias externas".

---

## Checklist de entrega

Antes de entregar un plano para procesamiento, verificar:

- [ ] Es una planta de distribución arquitectónica (no conjunto, azotea, alzado, topografía)
- [ ] No tiene XREFs pendientes (todo incrustado / bound)
- [ ] Cada recinto tiene una polilínea cerrada en la capa `A-AREA-BNDY`
- [ ] Cada recinto tiene una etiqueta de nombre en la capa `A-AREA-IDEN`
- [ ] La etiqueta está dentro de la polilínea correspondiente
- [ ] El nombre del espacio es una sola entidad TEXT o MTEXT (no partido en varias)
- [ ] Las anotaciones de área, cotas y notas están en capas distintas a `A-AREA-IDEN`

---

## Sistema de confianza del script

| Símbolo | Método | Confianza |
|---|---|---|
| `[✓]` | Polilínea cerrada que contiene el punto del texto | Alta |
| `[~]` | Área acumulada de superficies 3DFACE (planos 3D) | Media |
| `[?]` | Polilínea más cercana dentro de 15 m de radio | Baja |
| `[–]` | Sin geometría asociada encontrada | Sin área |

---

## Parámetros ajustables en el script

```python
AREA_MIN_M2  = 0.5   # ignorar polilíneas más pequeñas que esto (m²)
RADIO_BUSQ   = 15.0  # radio máximo para búsqueda de polilínea cercana (m)
RADIO_3DFACE = 8.0   # radio para acumular 3DFACEs por texto (m)
```

---

## Flujo de trabajo recomendado

```bash
# 1. Convertir DWG → DXF
bash convert.sh

# 2. Analizar un plano
python3 inventario_superficies.py "data/dxf/MiPlano.dxf"

# 3. Los resultados quedan en:
#    data/dxf/MiPlano_inventario.json
#    data/dxf/MiPlano_inventario.csv
```
