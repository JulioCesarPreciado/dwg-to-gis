# Guía de entrega de planos DWG — dwg-to-gis

Guía para arquitectos y dibujantes sobre cómo preparar un plano DWG para que el sistema detecte automáticamente habitaciones, locales y recintos con la mayor precisión posible.

---

## Cómo funciona la detección automática

El sistema intenta detectar recintos con tres estrategias en orden de precisión:

| Estrategia | Confianza | Qué necesita |
|---|---|---|
| **S1 — Polilíneas cerradas** | 🟢 Alta | Cada recinto dibujado como una polilínea cerrada con su etiqueta dentro |
| **S2 — Red de muros** | 🟡 Media | Muros como líneas continuas que se unen en esquinas, en una capa nombrada |
| **S3 — Ray-casting** | 🔴 Baja (área aproximada) | Cualquier geometría; el sistema estima el área por proyección de rayos |

**La mejor calidad se logra con S1.** S2 y S3 son fallback automático para planos que no siguen S1.

---

## Opción 1 (recomendada): Polilíneas cerradas por recinto

Cada habitación o local debe estar dibujado como **una sola polilínea cerrada** (`LWPOLYLINE` o `POLYLINE` con `Closed = Yes`) que trace exactamente el perímetro del espacio.

La **etiqueta de nombre** (`TEXT` o `MTEXT`) debe estar **dentro** de esa polilínea.

```
┌─────────────────┐
│                 │
│   HABITACIÓN    │  ← TEXT o MTEXT dentro de la polilínea
│      101        │
│                 │
└─────────────────┘
  ↑ LWPOLYLINE cerrada, capa: cualquiera
```

### Checklist para Opción 1

- [ ] Cada recinto es una polilínea cerrada (propiedad `Closed = Yes`)
- [ ] La etiqueta de nombre está **dentro** del polígono, no sobre el borde
- [ ] La etiqueta es una sola entidad `TEXT` o `MTEXT` (no partir el nombre en varias líneas)
- [ ] Las etiquetas de nombre están en una de las capas de texto reconocidas (ver tabla abajo)

---

## Opción 2 (aceptable): Muros como líneas

Si los muros están dibujados como **líneas individuales** (`LINE`), el sistema los une automáticamente para reconstruir los recintos, siempre que:

- Las líneas se toquen en los extremos (sin huecos visibles en esquinas)
- Todos los muros estén en **una sola capa** con un nombre descriptivo (ver tabla abajo)
- Cada recinto tenga su etiqueta de texto dentro del área correspondiente

### Tolerancia de cierre de esquinas

El sistema acepta huecos de hasta **35 cm** en los extremos de líneas. Huecos mayores rompen la detección del recinto y éste cae a S3 (ray-casting).

---

## Capas reconocidas

### Capas de texto (etiquetas de espacios)

| Convención | Capas que debe usar |
|---|---|
| D'L / Universal | `TX . 01`, `TX`, `Ar-Texto`, `T B`, `ÁREAS` |
| BOR-10x | `A-AREA-IDEN`, `G-ANNO-TEXT` |
| AIA/NCS | `A-AREA`, `Q-SPCQ`, `A-ANNO-NOTE` |

Si el plano viene de otra oficina con capas distintas, indicarlo al entregar el archivo para que se configure el sistema.

### Capas de muros (para Opción 2)

El sistema reconoce automáticamente capas cuyos nombres contengan:

`muro`, `muros`, `pared`, `paredes`, `wall`, `walls`, `tabique`, `cerramiento`, `partition`

También acepta cualquier nombre en inglés equivalente. Si la capa de muros no se detecta automáticamente, indicar el nombre exacto al momento de importar.

---

## Reglas de dibujo

### 1. No partir etiquetas en varias entidades de texto

❌ Mal — 3 entidades TEXT separadas:
```
SALA    DE    ESTAR
```

✅ Bien — una sola entidad TEXT o MTEXT:
```
SALA DE ESTAR
```

---

### 2. La etiqueta debe estar dentro del recinto

El sistema asigna el texto al recinto que lo contiene (point-in-polygon). Si el texto está fuera, se usa ray-casting como aproximación y el área puede ser incorrecta.

✅ Colocar el texto en el centro visual del espacio, sin que toque el borde del muro.

---

### 3. No mezclar anotaciones con etiquetas de nombre

| Contenido | Capa |
|---|---|
| Nombre del espacio (`HABITACIÓN 101`, `LOCAL COMERCIAL`) | Capa de texto reconocida (p.ej. `TX . 01`) |
| Área numérica (`96.25 m²`) | Puede ir en la misma capa — el sistema la ignora automáticamente |
| Cotas, pendientes, notas constructivas (`SUBE`, `BAJA`, `NPT`) | Otras capas (p.ej. `COTAS`, `NOTAS`) |
| Ejes de referencia (`A`, `B`, `1`, `1-1`) | Capa de ejes (p.ej. `EJES`) |

---

### 4. No usar XREFs — incrustar todo antes de entregar

Si el plano usa **referencias externas (XREF)**, el contenido no estará disponible al convertir a DXF.

✅ En AutoCAD antes de guardar:
`Insertar → Referencia externa → clic derecho → Bind → Bind (no Insertar)`
Confirmar en el XREF Manager que no quede ninguna referencia pendiente.

---

### 5. Planos con múltiples vistas en una hoja

Si la hoja contiene varias plantas (p.ej. Nivel 1 + Nivel 2 + Corte) lado a lado:

- Mantener las plantas **bien separadas horizontalmente** (mínimo 1 m de espacio entre ellas)
- Las etiquetas de cada planta deben estar dentro del área de esa planta
- El sistema detecta automáticamente los grupos de vistas para evitar que los rayos de una planta crucen a otra

---

## Tipos de plano procesables

| Tipo de plano | ¿Procesable? | Notas |
|---|---|---|
| Planta arquitectónica de distribución | ✅ Sí | Recintos + etiquetas |
| Planta de estacionamiento con cajones numerados | ✅ Sí | Si cada cajón tiene polilínea cerrada + etiqueta |
| Planta de conjunto / sitio | ⚠️ Parcial | Solo si los lotes tienen polilíneas + etiquetas |
| Planta de azotea (solo cubiertas) | ❌ No | Sin recintos habitables |
| Fachada / alzado / corte transversal | ❌ No | Sin plantas en planta |
| Topográfico / levantamiento UTM | ❌ No | Sin espacios arquitectónicos |
| Plano de instalaciones (eléctrico, hidráulico) | ❌ No | Polilíneas son recorridos, no recintos |

---

## Sistema de confianza del resultado

Cada recinto detectado lleva un indicador de confianza:

| Confianza | Método | Qué indica |
|---|---|---|
| `alta` | Polilínea cerrada con texto dentro | Área exacta del dibujo |
| `media` | Polilínea reconstruida a partir de muros | Área calculada; posibles imprecisiones por huecos |
| `baja` | Ray-casting (proyección de rayos) | Área estimada; puede ser incorrecta |

Para trabajo de inventario o certificación de superficies, solo usar recintos con confianza `alta` o `media`.

---

## Resumen rápido

1. **Mejor resultado**: polilínea cerrada por recinto + etiqueta dentro + capas de texto estándar.
2. **Resultado aceptable**: muros como líneas continuas en una capa nombrada + etiquetas dentro del área.
3. **Siempre**: incrustar XREFs, no partir etiquetas, no poner texto fuera del recinto.
4. **Indicar** la convención de capas de la oficina si no es alguna de las listadas.
