# Horacio UX / Art Direction Brief

> **Historical research brief.** Names, sample payloads and proposed actions below belong to the earlier planning stage. The current product is **Personal Watch App** with [three-action MVP scope](../PERSONAL_WATCH_APP_MVP.md); these proposals do not authorize additional actions or backend changes.

## Hermes Agents — Apple Watch-first

**Objetivo:** crear una experiencia espectacular, rápida y premium para capturar acciones hacia Hermes Agents desde la muñeca. El Watch no debe sentirse como “mini iPhone”; debe ser un **control remoto inteligente**: capturar, confirmar, enviar y salir.

**Principio rector:** **3 segundos, 1 intención, 1 haptic de cierre.**

---

## 1. Dirección de arte

### Personalidad visual
- **Dark, elegante, minimal, moderna, tecnológica/cinemática.**
- Sensación de “cabina de mando personal”: precisa, silenciosa, premium.
- Interfaz con profundidad sutil: **glassmorphism oscuro**, sombras suaves, bordes finos, luz roja contenida.
- Evitar look “app de productividad genérica”; debe sentirse como un sistema de agentes personales.

### Paleta
- **Base:** `#050506` / `#09090B` negro profundo.
- **Superficies:** `#111114`, `#17171B`, glass oscuro con 16–28% opacity.
- **Texto primario:** `#F5F5F7`.
- **Texto secundario:** `#A7A7AD`.
- **Bordes:** `rgba(255,255,255,0.08)`.
- **Acento Hermes:** dark red `#BB000E`.
- **Estados:** éxito verde muy sobrio, warning ámbar oscuro, error rojo Hermes.

### Tipografía
- **SF Pro / SF Compact** nativa Apple Watch.
- Pesos: Semibold para acciones, Regular para contexto, Medium para microcopy.
- Nada de párrafos largos. Máximo 1–2 líneas por pantalla en Watch.

### Iconografía
- Íconos lineales, monocromos, con acento rojo solo para estado activo.
- Cada agente debe tener símbolo simple:
  - **Megan:** tarjeta/moneda.
  - **Aura:** chispa/checklist/casa.
  - **Pipo:** terminal/branch.
  - **Atenea:** prisma/libro/lupa.
  - **Horacio:** marco/pluma/luz.
  - **Argos:** ojo/radar.

### Movimiento
- Transiciones cortas, 120–220 ms.
- Efecto “signal pulse” rojo al escuchar o enviar.
- Evitar animaciones largas: el reloj debe cerrar rápido.

---

## 2. Modelo UX Watch-first

### Qué sí hace el Watch
1. Captura rápida por voz, botón o acción favorita.
2. Confirma intención crítica.
3. Envía a un agente.
4. Muestra estado mínimo: enviado, cola, error, necesita iPhone.
5. Permite repetir o corregir lo último.

### Qué NO debe hacer el Watch
- Configuración profunda.
- Historial largo.
- Edición compleja.
- Multi-step workflows extensos.
- Gestión de agentes, permisos, cuentas o templates avanzados.

Todo eso vive en el iPhone companion.

---

## 3. Arquitectura de información / navegación

### Watch App IA

```text
Complicación / Smart Stack
        ↓
Home: Hermes Pulse
        ↓
Quick Capture
 ├─ Acción sugerida
 ├─ Agentes favoritos
 ├─ Dictado universal
 └─ Última acción
        ↓
Review / Confirm
        ↓
Sent / Queued / Needs attention
        ↓
Mini History, opcional
```

### Home: “Hermes Pulse”
Pantalla inicial con tres zonas:
1. **Estado superior:** hora/conectividad mínima: `Online`, `En cola`, `Sin red`.
2. **Acción principal:** botón grande: `Capturar`.
3. **Dock de agentes:** 3–6 agentes en lista compacta o grid circular.

### Navegación recomendada
- **Default:** acción-first, no agente-first.
- Primer toque: capturar intención.
- Segundo toque: confirmar si hace falta.
- Tercer toque máximo: enviar.

### Estructura ideal
1. **Capturar** — botón primario universal.
2. **Favoritos** — chips: Megan, Aura, Pipo, Atenea, Horacio, Argos.
3. **Recientes** — repetir último gasto, último recordatorio, última captura.
4. **Estado** — mini historial de últimos 3 envíos.

---

## 4. Flujos UX Watch-first

### Flow A — Captura universal
```text
Complicación → “Capturar” → Dictado → Hermes clasifica agente → Review corto → Enviar → Haptic éxito
```

**Microcopy:**
- `Te escucho`
- `Procesando…`
- `Aura · Recordatorio`
- `¿Enviar?`
- `Listo`

**Uso:** cuando Jose no quiere elegir agente.

---

### Flow B — Acción directa por agente
```text
Home → Megan/Aura/Pipo/etc. → Acción favorita → Dictado o tap → Confirmar → Enviado
```

**Ejemplos:**
- Megan → `Gasto rápido`
- Aura → `Recordar`
- Pipo → `Tarea código`
- Atenea → `Investigar`
- Horacio → `Brief diseño`
- Argos → `Captura general`

---

### Flow C — Megan / gastos
```text
Megan → Gasto → Dictar: “23 dólares café cliente” → Review → Enviar
```

**Review:**
- `$23.00`
- `Café · Cliente`
- `Megan`
- Botones: `Enviar` / `Editar`

**Microcopy:**
- `¿Cuánto fue?`
- `Gasto detectado`
- `Falta monto`
- `Guardado`

---

### Flow D — Aura / recordatorios, mercado, hogar
```text
Aura → Recordar/Mercado/Hogar → Dictado → Confirmación contextual → Enviar
```

**Ejemplos:**
- `Recuérdame pagar luz mañana`
- `Agrega leche al mercado`
- `Apaga luces sala`

**Microcopy:**
- `¿Para cuándo?`
- `Agregado`
- `Acción hogar lista`
- `Necesita confirmar en iPhone` si es sensible.

---

### Flow E — Pipo / tareas código
```text
Pipo → Nueva tarea → Dictado → Proyecto sugerido → Enviar
```

**Review:**
- `Bug login móvil`
- `Repo sugerido: Hermes`
- `Prioridad: normal`

**Microcopy:**
- `Nueva tarea`
- `¿Repo correcto?`
- `Enviado a Pipo`

---

### Flow F — Atenea / investigación
```text
Atenea → Investigar → Dictado → Scope mínimo → Enviar
```

**Microcopy:**
- `Tema a investigar`
- `Brief creado`
- `Te aviso al terminar`

---

### Flow G — Horacio / diseño
```text
Horacio → Brief diseño → Dictado → Tipo visual → Enviar
```

**Review:**
- `Brief: landing dark premium para app`
- `Formato: UI / moodboard / prompt`

**Microcopy:**
- `Cuéntame el look`
- `Brief capturado`
- `Enviado a Horacio`

---

### Flow H — Argos / captura general y estado
```text
Argos → Capturar → Dictado libre → Etiqueta sugerida → Enviar
```

**Uso:** inbox universal, estado personal, notas rápidas, ideas sin clasificar.

**Microcopy:**
- `Captura libre`
- `Clasifico luego`
- `En cola de Argos`

---

## 5. Layout de pantallas Watch

### 5.1 Home / Hermes Pulse
```text
┌────────────────────┐
│ Online       10:42 │
│                    │
│     ◉ Hermes       │
│   Capturar ahora   │
│                    │
│ Megan  Aura  Pipo  │
│ Atenea Horacio ... │
└────────────────────┘
```

**Notas:**
- Botón central grande con glow rojo sutil.
- Agentes como chips/tarjetas glass.
- Una sola acción dominante.

### 5.2 Dictado
```text
┌────────────────────┐
│        Aura        │
│                    │
│   onda / pulso     │
│    Te escucho      │
│                    │
│   Cancelar         │
└────────────────────┘
```

**Notas:**
- Fondo negro, onda roja baja intensidad.
- Cancelar siempre visible.

### 5.3 Review
```text
┌────────────────────┐
│ Megan              │
│ $23.00             │
│ Café · Cliente     │
│                    │
│ [Enviar] [Editar]  │
└────────────────────┘
```

**Notas:**
- El dato crítico debe ser grande.
- Botón primario rojo solo si la acción está lista.

### 5.4 Sent
```text
┌────────────────────┐
│        ✓           │
│       Listo        │
│   Enviado a Aura   │
└────────────────────┘
```

**Notas:**
- Cierre automático en 1–1.5 s.
- Haptic de éxito.

### 5.5 Error recuperable
```text
┌────────────────────┐
│ Sin red            │
│ Lo dejo en cola    │
│ [Reintentar]       │
└────────────────────┘
```

---

## 6. Interacción: botones, dictado y gestures

### Input principal
- **Dictado** como vía principal.
- **Botones grandes** para acciones favoritas.
- **Digital Crown** para navegar listas cortas o cambiar agente.
- **Double Tap gesture** para confirmar cuando esté disponible.
- **Complicaciones / Smart Stack** para entrar directo a una acción.
- **Action Button en Apple Watch Ultra** solo como shortcut configurable, no como requisito.

### Reglas de interacción
- Toques mínimos; cada pantalla debe tener una decisión clara.
- Confirmación obligatoria para acciones con dinero, hogar, mensajes externos o side effects irreversibles.
- Confirmación opcional para capturas pasivas: notas, briefs, research tasks.
- Mantener `Cancelar` en escucha y `Editar` en review.

---

## 7. Microcopy corto para muñeca

### Global
- `Capturar`
- `Te escucho`
- `Procesando…`
- `¿Enviar?`
- `Listo`
- `En cola`
- `Reintentar`
- `Editar`
- `Cancelar`
- `Abrir iPhone`

### Por agente
- **Megan:** `Gasto`, `Monto`, `Guardado`.
- **Aura:** `Recordar`, `Mercado`, `Hogar`, `Agregado`.
- **Pipo:** `Tarea`, `Repo`, `Asignado`.
- **Atenea:** `Investigar`, `Tema`, `Brief listo`.
- **Horacio:** `Diseño`, `Look`, `Brief capturado`.
- **Argos:** `Captura`, `Estado`, `Clasifico luego`.

### Tono
- Corto, seguro, sin adornos.
- Evitar frases como “Estoy trabajando en ello…”.
- Preferir verbos de acción y estados claros.

---

## 8. Feedback y haptics

### Haptics sugeridos
- **Inicio dictado:** tap ligero.
- **Audio detectado:** pulso sutil opcional.
- **Confirmación enviada:** success haptic.
- **Cola offline:** tap doble suave.
- **Error:** failure haptic claro pero no agresivo.
- **Necesita iPhone:** haptic medio + pantalla persistente.

### Feedback visual
- Pulso rojo cuando escucha.
- Glow rojo corto al enviar.
- Check minimal al terminar.
- Estado offline con borde ámbar, no pantalla alarmista.

---

## 9. Estados de error y recuperación

| Estado | Microcopy | Recuperación |
|---|---|---|
| Sin red | `Sin red · En cola` | Guardar local y sincronizar luego |
| Dictado falló | `No entendí` | `Reintentar` / `Escribir` |
| Agente no disponible | `Agente pausado` | Enviar a Argos o abrir iPhone |
| Falta dato crítico | `Falta monto` / `¿Cuándo?` | Pregunta de una sola pantalla |
| Permiso micrófono | `Activa micrófono` | `Abrir iPhone` |
| Auth expirada | `Sesión vencida` | Handoff a iPhone |
| Acción sensible | `Confirma en iPhone` | Bloquear ejecución en Watch |
| Rate limit/API | `Demora temporal` | Cola + notificación posterior |
| Confianza baja de clasificación | `¿Aura o Argos?` | Dos botones máximo |

---

## 10. iPhone companion

### Rol del iPhone
- Configuración de cuenta y agentes.
- Historial completo.
- Templates y prompts favoritos.
- Personalización de complicaciones.
- Reglas de confirmación.
- Permisos, privacidad y logs.

### IA iPhone
```text
Home
├─ Agents
│  ├─ Megan
│  ├─ Aura
│  ├─ Pipo
│  ├─ Atenea
│  ├─ Horacio
│  └─ Argos
├─ Quick Actions
├─ History
├─ Automations / Confirmations
└─ Settings / Privacy
```

### Visual iPhone
- Misma estética dark premium.
- Más espacio para cards glass, timeline, estados de agentes.
- Watch preview: mostrar cómo queda cada acción en el reloj.

---

## 11. Personalización de acciones

### En Watch
- Reordenar 3–6 agentes favoritos.
- Elegir acción primaria del botón grande.
- Repetir última acción.
- Activar/desactivar confirmación por tipo.

### En iPhone
- Crear templates por agente:
  - Megan: categorías favoritas, moneda, cuentas.
  - Aura: listas, casa, horarios.
  - Pipo: repos/proyectos.
  - Atenea: profundidad, fuentes, output.
  - Horacio: estilos visuales, formatos.
  - Argos: etiquetas, routing.
- Configurar complicaciones:
  - `Capturar`
  - `Gasto`
  - `Recordar`
  - `Tarea Pipo`
  - `Brief Horacio`
- Acciones sugeridas por contexto: hora, ubicación, rutina, último uso.

---

## 12. Riesgos de sobrecargar el reloj

### Riesgos
1. Demasiados agentes visibles a la vez.
2. Mucha lectura en pantalla pequeña.
3. Confirmaciones excesivas.
4. Historial demasiado profundo.
5. Edición compleja en Watch.
6. Notificaciones/haptics intrusivas.
7. Estados de IA ambiguos o lentos.
8. Acciones sensibles ejecutadas accidentalmente.

### Mitigaciones
- Acción-first, no dashboard-first.
- Máximo 6 agentes en Watch; el resto vive en iPhone.
- Máximo 2 botones por pantalla.
- Máximo 1 pregunta aclaratoria en Watch.
- Auto-cierre tras éxito.
- Cola offline silenciosa.
- Handoff a iPhone para todo lo complejo.
- Confirmación fuerte solo para acciones con consecuencias.

---

## 13. Decisiones que deben pasar por Fable 5 Review Gate UX

Estas decisiones deben revisarse antes de entrar a implementación visual/SwiftUI final:

1. **Modelo de navegación:** action-first vs agent-first. Recomendación: action-first con dock de agentes.
2. **Densidad de Home:** cantidad máxima de agentes/chips visibles en 41 mm y 45 mm.
3. **Reglas de confirmación:** qué acciones se envían directo y cuáles requieren review.
4. **Clasificación automática:** cuándo Hermes decide agente automáticamente y cuándo pregunta.
5. **Microcopy final:** textos de muñeca, errores y estados de carga.
6. **Sistema haptic:** intensidad, frecuencia y diferenciación éxito/error/cola.
7. **Visual tokens:** contraste de glassmorphism, legibilidad, uso de `#BB000E` sin fatiga visual.
8. **Onboarding Watch+iPhone:** qué se configura en iPhone antes de usar Watch.
9. **Personalización:** límites para evitar que Jose convierta el Watch en un panel complejo.
10. **Privacidad y acciones sensibles:** gastos, hogar, mensajes externos, datos personales.
11. **Offline/queue behavior:** qué se guarda localmente y cómo se comunica.
12. **Complications/Smart Stack:** qué acciones merecen acceso directo desde esfera.
13. **Handoff iPhone:** cuándo y cómo aparece `Abrir iPhone` sin frustrar.
14. **Accesibilidad:** tamaño de texto, VoiceOver, reduce motion, alto contraste.
15. **Métricas UX:** tiempo a captura, tasa de reintento, tasa de cancelación, errores de clasificación.

### Criterio de aprobación Fable 5
Una decisión pasa el gate si cumple:
- Se entiende en menos de 3 segundos.
- Requiere máximo 2 taps para la acción frecuente.
- No mete configuración profunda en Watch.
- Tiene recuperación clara si falla.
- Mantiene estética dark premium sin sacrificar legibilidad.

---

## 14. Norte de producto

La app debe sentirse como **un botón de comando para una red de agentes personales**: silenciosa, veloz, bella y confiable. El Watch captura el impulso; el iPhone organiza el sistema; Hermes Agents hacen el trabajo.
