## Resultado del review — `mobile-capture-v1`

> **Historical research brief.** Names, sample payloads and proposed actions below belong to the earlier planning stage. The current product is **Personal Watch App** with [three-action MVP scope](../PERSONAL_WATCH_APP_MVP.md); these proposals do not authorize additional actions or backend changes.

### Qué hice
- Revisé `<PROJECT_ROOT>` y la configuración/CLI de Hermes Webhooks.
- No modifiqué configuración ni reinicié gateway.
- Verifiqué la ruta en vivo con POSTs firmados/no firmados usando HMAC V2, sin exponer secretos.
- Revisé prompt, fixtures Megan/Aura, script de firma, docs de staging/idempotencia y estado de herramientas del platform `webhook`.

### Estado actual encontrado
- La ruta **sí existe** como suscripción dinámica:
  - `/webhooks/mobile-capture-v1`
  - evento: `mobile_capture.v1`
  - deliver: `log`
  - secret presente y archivo auxiliar `<HERMES_HOME>/mobile-capture-v1-secret.txt` con modo `0600`, coincidente con la suscripción.
- Gateway saludable:
  - `GET http://localhost:8644/health` → `200 {"status":"ok","platform":"webhook"}`
- HMAC V2 funciona:
  - fixture firmado duplicado → `200 {"status":"duplicate", ...}`
  - firma inválida → `401 {"error":"Invalid signature"}`
  - evento inválido firmado → `200 {"status":"ignored", ...}`
- Existen archivos de staging:
  - `backend/mobile-capture-v1-webhook-prompt.md`
  - `backend/scripts/sign_and_post_mobile_capture.py`
  - `backend/fixtures/megan-expense-simple.json`
  - `backend/fixtures/aura-reminder-simple.json`
  - `backend/fixtures/invalid-event.json`
- El script de firma parsea OK por AST y no imprime secretos.

### Bloqueadores antes de habilitar writes
1. **No hay ledger persistente real todavía.**  
   No encontré tabla/SQLite/archivo ledger. Hermes solo está usando cache temporal de delivery ID por ~1h.

2. **Conflicto de idempotencia no se rechaza.**  
   Probe ejecutado:
   - mismo `X-Request-ID` + mismo body → `200 duplicate`
   - mismo `X-Request-ID` + body cambiado → también `200 duplicate`  
   Esperado antes de writes: `409/rejected` por `payload_hash` distinto.

3. **No se valida `X-Request-ID == body.request_id`.**  
   Probe con header y body distintos retornó `202 accepted`.  
   Esto debe rechazarse antes de llegar al agente.

4. **La respuesta HTTP real del webhook no es el JSON del prompt.**  
   Hermes agent-mode responde inmediatamente `202 accepted`; la respuesta planeada por el agente se va a logs/delivery. Para Watch UX con `display_message`, hace falta:
   - endpoint/BFF síncrono, o
   - ledger + polling por `request_id`, o
   - aceptar que el POST solo confirma recepción.

5. **Dry-run/no-write es principalmente prompt-level.**  
   El platform `webhook` tiene habilitados `terminal`, `file`, `web` y `skills`. En las sesiones de fixture el agente cargó skills de dominio y ejecutó herramientas read-only. Antes de writes, no basta con “no escribas” en el prompt: debe haber flags server-side y enforcement determinístico.

6. **Docs/contrato deben alinearse a HMAC V2.**  
   Hermes actual recomienda `X-Webhook-Signature-V2` + `X-Webhook-Timestamp`; `X-Webhook-Signature` body-only queda legacy por replay risk.

7. **Logs guardan payload crudo.**  
   `{__raw__}` termina en sesión/logs. Para producción, minimizar payload en prompt y redacción de dictados sensibles.

### Recomendaciones accionables
- Implementar antes de writes un pre-handler/ledger determinístico:
  - validar HMAC V2 y timestamp;
  - validar JSON/schema required fields;
  - validar `X-Request-ID == body.request_id`;
  - calcular `sha256(raw_body)`;
  - guardar `request_id`, `payload_hash`, `domain`, `status`, `external_ids`, `response_cached`;
  - rechazar mismo `request_id` con body distinto;
  - devolver cached response para duplicados.
- Usar flags server-side, no campos del cliente, para `allow_write`.
- Ajustar prompt: cambiar “si el payload indica habilitado” por “solo si config server-side habilita dominio”.
- Reducir `{__raw__}` en prompt a campos mínimos/sanitizados.
- Añadir al script de fixture headers del contrato:
  - `X-Hermes-Payload-Version: 1`
  - `X-Hermes-Client`
  - `X-Hermes-Device-ID`
- Actualizar docs/fixtures a HMAC V2 y decidir si `request_id` debe ser UUID estricto; fixtures actuales usan IDs legibles tipo `fixture-megan-expense-0001`.

### Comandos seguros de verificación
```bash
hermes webhook list
hermes tools list --platform webhook
python backend/scripts/sign_and_post_mobile_capture.py backend/fixtures/megan-expense-simple.json
python backend/scripts/sign_and_post_mobile_capture.py backend/fixtures/aura-reminder-simple.json
python backend/scripts/sign_and_post_mobile_capture.py backend/fixtures/invalid-event.json
python backend/scripts/sign_and_post_mobile_capture.py backend/fixtures/megan-expense-simple.json --invalid-signature
```

### Archivos creados/modificados por mí
- No dejé archivos creados ni modificados.
- El directorio no es repo Git (`git status` falló con “not a git repository”), así que no pude producir diff.
- Mis POSTs sí generaron sesiones/logs normales del gateway para los probes.