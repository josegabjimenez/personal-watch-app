# Siguiente batch de agentes

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

Cuando terminen Pipo/Atenea/Horacio, Argos debe lanzar segundo batch:

## Megan

Objetivo:

- Definir contrato financiero seguro para `megan.expense_capture` desde Watch.
- Validar campos mínimos, confirmaciones necesarias, y compatibilidad con Firefly.
- Revisar idempotencia para evitar doble gasto.
- Definir mensajes cortos para Watch.

Preguntas clave:

- ¿Cuándo Megan puede escribir automáticamente?
- ¿Cuándo debe devolver `needs_confirmation`?
- ¿Qué texto mínimo debe ver Jose en el Watch?

## Aura

Objetivo:

- Definir contrato de `reminder_capture`, `grocery_capture`, `home_action`.
- Validar routing a Notion Tasks/Notes/lista de mercado oficial.
- Confirmar fecha/hora explícita en respuestas.
- Diseñar lifecycle con Google Calendar y dispatcher.

Preguntas clave:

- ¿Cómo evitar crear tareas literales cuando Jose pide listar/cancelar/completar?
- ¿Cómo confirmar fechas parseadas desde dictado de Watch?
- ¿Cómo manejar Calendar OAuth fallando sin perder la tarea?

## Argos/Fable 5 review

Objetivo:

- Consolidar todos los briefs.
- Aprobar/rechazar decisiones de arquitectura.
- Convertir roadmap en tickets ejecutables para Pipo.
