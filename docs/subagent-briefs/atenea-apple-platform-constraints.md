# Atenea — Apple platform constraints para app Watch-first de Hermes Agents

> **Historical research brief.** Names, sample payloads and proposed actions below belong to the earlier planning stage. The current product is **Personal Watch App** with [three-action MVP scope](../PERSONAL_WATCH_APP_MVP.md); these proposals do not authorize additional actions or backend changes.

**Fecha de consulta:** 2026-07-06  
**Alcance:** app nativa iOS + watchOS para Jose; prioridad captura rápida desde Apple Watch con Siri/App Intents, dictado, botones rápidos y llamadas HTTPS a Hermes webhooks/API; Jose ya tiene Shortcuts existentes.  
**Regla de rigor:** las versiones exactas se citan solo cuando salieron de Apple Developer docs consultadas. Las APIs nuevas por símbolo se deben revalidar en el SDK/Xcode objetivo antes de diseñar sobre ellas.

## 1) Decisiones ejecutivas recomendadas

1. **Arquitectura Watch-first e independiente.** La captura crítica debe funcionar en el Apple Watch sin depender del iPhone. Apple recomienda que las apps watchOS independientes no requieran companion iOS; WatchConnectivity no puede ser la fuente principal de datos para una app independiente.
2. **HTTPS directo desde watchOS como camino principal.** El Watch puede conectar a web services directamente; el sistema puede enrutar por iPhone proxy, Wi‑Fi conocido o celular. Hay que probar todas esas rutas reales.
3. **Captura local-first + cola idempotente.** Para un webhook Hermes pequeño, usar `URLSession` foreground/default o ephemeral cuando la app está activa. Persistir primero el evento localmente y reintentar, porque watchOS puede desactivar la app al bajar la muñeca y los background requests se pueden diferir.
4. **App Intents/App Shortcuts como integración Siri/Shortcuts.** Verificado: framework App Intents está documentado para iOS 16.0+ y watchOS 9.0+. `AppShortcut`/`AppShortcutsProvider` también están documentados para esas plataformas. Pero algunas capacidades nuevas de ejecución/targeting (`supportedModes`, `allowedExecutionTargets`) aparecen con SDKs mucho más nuevos; no depender de ellas sin validar deployment target.
5. **Dictado: preferir input de sistema.** En watchOS, `presentTextInputController` ofrece frases sugeridas, dictado y emoji. `UNTextInputNotificationAction` también permite introducir o dictar texto desde notificaciones. El framework `Speech` consultado no lista watchOS; no planear STT custom en el Watch con `SFSpeechRecognizer` sin revalidar en Xcode.
6. **WatchConnectivity solo como acelerador/puente.** Usar `sendMessage` para interacciones live cuando `isReachable`; `transferUserInfo`/`transferFile`/`updateApplicationContext` para sync oportunista. No usarlo como única ruta de captura hacia Hermes.
7. **Credenciales por dispositivo.** Guardar token/API key de Hermes en Keychain del Watch y del iPhone por separado. Keychain Sharing/App Groups existen en watchOS, pero no asumir que resuelven sincronización iPhone↔Watch; validar entitlements y bootstrap de token en dispositivo real.
8. **Notificaciones para workflows largos.** Local/APNs para confirmaciones, errores y tareas asíncronas; actionable notifications y texto/dictado pueden servir para “reply/capture”. Background execution no está garantizado.
9. **Complications/widgets = estado + launcher, no captura primaria.** WidgetKit soporta watch complications/Smart Stack; usar para estado, “tap to open”, o superficies de acceso rápido. Validar interactividad y familias soportadas en el target watchOS.
10. **Privacidad/seguridad desde el diseño.** Solo HTTPS/ATS sin excepciones si Hermes endpoint lo permite; privacidad manifiesta (`PrivacyInfo.xcprivacy`), App Privacy labels, no logs de texto dictado/token, y frases de App Shortcuts sin secretos porque Apple documenta extracción anonimizada de datos de App Shortcuts.
11. **TestFlight + device testing obligatorio.** Simulador no replica rendimiento, conectividad, dictado/Siri/notifications ni energía. Probar en Apple Watch físico emparejado y, si aplica, con celular/sin iPhone.

## 2) Matriz de constraints con evidencia Apple

| Área | Certezas verificadas en docs Apple | Decisión/impacto para Hermes Watch app |
|---|---|---|
| App Intents | `App Intents` está documentado como iOS 16.0+ / watchOS 9.0+ y sirve para Siri, Spotlight, Shortcuts y widgets. Fuente: <https://developer.apple.com/documentation/AppIntents> | Implementar intents de captura: “capturar nota”, “capturar tarea”, “capturar idea”, “enviar a Hermes”. Mantener frases cortas y localizadas. |
| App Shortcuts / Siri | `AppShortcut` define un atajo preconfigurado para un intent; `AppShortcutsProvider` provee shortcuts. Apple puede extraer datos anonimizados como frases localizadas/títulos/descripciones para mejorar App Shortcuts. Fuentes: <https://developer.apple.com/documentation/appintents/appshortcut>, <https://developer.apple.com/documentation/appintents/appshortcutsprovider> | Diseñar frases sin secretos ni PII. No romper Shortcuts existentes de Jose; añadir App Shortcuts como capa nativa y mantener HTTP Shortcuts como fallback. |
| APIs nuevas de App Intents | `authenticationPolicy` está iOS 16/watchOS 9; default permite ejecutar sin auth incluso con device locked. `supportedModes` aparece en docs como iOS/watchOS 26+; `allowedExecutionTargets` como 27+. Fuentes: `/AppIntent/authenticationPolicy`, `/supportedModes`, `/allowedExecutionTargets` | Validar el deployment target. Si se requiere soportar watchOS anterior, no diseñar dependencias críticas sobre APIs de modo/target nuevas. Definir explícitamente qué intents pueden ejecutarse locked. |
| Dictado de captura | `WKInterfaceController.presentTextInputController` está watchOS 2.0+ y muestra input con sugerencias, dictado o emoji. Fuente: <https://developer.apple.com/documentation/watchkit/wkinterfacecontroller/presenttextinputcontroller(withsuggestions:allowedinputmode:completion:)> | Para captura rápida: botón → modal dictado/sugerencias → persistir → POST. Validar UX SwiftUI actual equivalente vs WatchKit bridge. |
| STT custom | `Speech`/`SFSpeechRecognizer` docs consultadas listan iOS/iPadOS/macOS/visionOS pero no watchOS; `SFSpeechRecognizer` requiere autorización y Apple documenta límite de tareas largas (~1 minuto). Fuentes: <https://developer.apple.com/documentation/speech>, <https://developer.apple.com/documentation/speech/sfspeechrecognizer> | No basar MVP en reconocimiento de voz propio dentro del Watch. Si se necesita audio crudo → permiso micrófono, subida a Hermes/servidor y revisión privacy; validar App Review. |
| URLSession foreground | watchOS puede conectar a web services; default/ephemeral son para foreground y “solo continúan mientras la app corre en foreground”; hay que cancelar/reemplazar por background al pasar a background. Fuentes: <https://developer.apple.com/documentation/watchos-apps/keeping-your-watchos-app-s-content-up-to-date>, <https://developer.apple.com/documentation/watchos-apps/making-default-and-ephemeral-requests> | El webhook rápido debe ser pequeño, con timeout corto, UI de “guardado local / enviando”, y retry si la app se desactiva. |
| URLSession background | Background session persiste si la app cierra, pero puede diferir entrega por recursos, conectividad u otros; transferencias pequeñas mejor. `WKURLSessionRefreshBackgroundTask` dice que background transfers continúan después de terminar la app; async uploads/downloads se suspenden con la app. Fuentes: <https://developer.apple.com/documentation/watchos-apps/making-background-requests>, <https://developer.apple.com/documentation/watchkit/wkurlsessionrefreshbackgroundtask> | Para garantía de entrega: cola local + background upload/download si aplica + reintentos cuando app se activa. Validar soporte de POST JSON/background upload en watchOS target. |
| WatchConnectivity | `WCSession` comunica watchOS+iOS; ambos deben crear/activar sesión. Si ambos activos, mensajes inmediatos; si solo uno activo, transfers oportunistas en background. `sendMessage` requiere counterpart reachable; `transferUserInfo` asegura entrega y continúa si app suspendida; `updateApplicationContext` sirve para estado latest y puede llamarse aunque counterpart no reachable. Fuentes: <https://developer.apple.com/documentation/watchconnectivity/wcsession>, `/sendmessage`, `/transferuserinfo`, `/updateapplicationcontext`, `/isreachable` | Usar WC para bootstrap token, sync de estado o fallback a iPhone; no para ruta primaria de captura Hermes. Manejar reachable/unreachable explícitamente. |
| App independiente | Apple: usuarios esperan que Watch apps funcionen sin iPhone; independent app no puede usar WatchConnectivity como fuente principal y debe acceder a info por sí misma. Fuente: <https://developer.apple.com/documentation/watchos-apps/creating-independent-watchos-apps> | Si Hermes endpoint/token no están disponibles en Watch, el producto falla. Onboarding en Watch o bootstrap robusto desde iPhone + estado claro. |
| Background execution | watchOS corre principalmente en foreground; solo casos limitados en background. Background refresh no está garantizado; docs citan presupuestos limitados y prioridad para apps con complication activa/dock. Fuente: <https://developer.apple.com/documentation/watchkit/background-execution>, <https://developer.apple.com/documentation/watchkit/wkapplicationrefreshbackgroundtask> | No prometer envío inmediato si app está cerrada. Usar APNs/notificaciones/server-side para confirmaciones, no polling intensivo. |
| Notifications | UserNotifications disponible watchOS 3.0+. Local/remote notifications; actionable notifications; `UNTextInputNotificationAction` acepta texto y sistema permite introducir o dictar. Fuentes: <https://developer.apple.com/documentation/usernotifications>, <https://developer.apple.com/documentation/usernotifications/untextinputnotificationaction> | Útil para “Hermes necesita aclaración”, confirmación de captura, error de autenticación o quick reply. Requiere permiso de notificaciones. |
| Widgets/complications | WidgetKit watchOS 9.0+; crea widgets, watch complications, Live Activities, controls. Accessory widgets aparecen como complications en Apple Watch y dan contenido glanceable/quick access. Fuentes: <https://developer.apple.com/documentation/widgetkit>, <https://developer.apple.com/documentation/widgetkit/creating-accessory-widgets-and-watch-complications> | Complication con estado de cola/último envío + tap para abrir capture. Validar si se puede ejecutar AppIntent directo desde la superficie objetivo; si no, solo launcher. |
| Keychain/App Groups | Keychain access groups entitlement watchOS 2.0+; App Groups entitlement watchOS 2.0+; permiten shared containers/keychain groups para apps del mismo team. Fuentes: <https://developer.apple.com/documentation/bundleresources/entitlements/keychain-access-groups>, <https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.security.application-groups>, <https://developer.apple.com/documentation/security/sharing-access-to-keychain-items-among-a-collection-of-apps> | Configurar entitlements por target: iOS app, watch app, widget/App Intent extension. Validar alcance real iPhone↔Watch; tratar token como per-device hasta probar lo contrario. |
| ATS/HTTPS | ATS exige HTTPS para conexiones HTTP hechas típicamente con `URLSession` y bloquea conexiones que no cumplen mínimos TLS; excepciones reducen seguridad. Fuente: <https://developer.apple.com/documentation/bundleresources/information_property_list/nsapptransportsecurity> | Hermes webhooks/API deben tener HTTPS válido, TLS moderno, dominio estable. Evitar excepciones ATS; considerar certificate pinning solo si se puede operar rotación. |
| Privacy manifests | Privacy manifest describe datos recolectados y required-reason APIs; aplica a iOS/iPadOS/tvOS/visionOS/watchOS para required reasons. Fuente: <https://developer.apple.com/documentation/bundleresources/privacy_manifest_files> | Declarar texto dictado, identifiers, diagnostics, server endpoint, SDKs. Revisar si APIs usadas (UserDefaults/App Group, file timestamps, etc.) requieren reasons. |
| App Privacy | Apple exige informar prácticas de datos/App Privacy en App Store Connect. Fuente: <https://developer.apple.com/app-store/app-privacy-details/> | Las capturas a Hermes son contenido de usuario enlazado a Jose; documentar colección, propósito y retención. |
| TestFlight/signing | Xcode puede gestionar signing/provisioning automáticamente; TestFlight distribuye beta builds y requiere Apple Developer Program para registered devices/TestFlight/App Store. Fuente: <https://developer.apple.com/documentation/xcode/running-your-app-in-simulator-or-on-a-device>, <https://developer.apple.com/documentation/xcode/distributing-your-app-for-beta-testing-and-releases>, <https://developer.apple.com/testflight/> | Probar archive Release + TestFlight temprano, no solo Debug. Incluir Apple Watch físico en plan de device matrix. |

## 3) Cosas a validar explícitamente en Xcode/docs/dispositivo

- **Deployment target real:** iOS/watchOS mínimos que Jose aceptará; confirmar disponibilidad de cada símbolo usado con compiler checks.
- **Siri/App Shortcuts en Watch:** invocación por voz en Apple Watch físico, con pantalla bloqueada/levantada, sin iPhone cerca, y con idioma español/inglés.
- **App Intent execution model:** si el intent corre en app, extension o widget extension; qué pasa si requiere foreground; confirmar `authenticationPolicy` para capturas mientras device locked.
- **Interoperabilidad con Shortcuts existentes:** si Jose puede seguir usando sus Shortcuts HTTP actuales y/o reemplazarlos por App Intents sin duplicados ni cambios manuales excesivos.
- **Dictado:** `presentTextInputController` vs SwiftUI `TextField`/`TextEditor`; latencia, cancelación, idioma, autocorrección, resultado vacío, emoji accidental.
- **Networking real:** POST a Hermes por iPhone proxy, Wi‑Fi, celular del Watch, modo avión, sin iPhone, captive/poor network; validar ATS/TLS/cert.
- **Background POST:** comportamiento al bajar muñeca justo después de dictar; si background session acepta el tipo de POST/upload requerido; entrega diferida; idempotency key.
- **Keychain/App Groups:** entitlements por target y suite names; acceso desde watch app, iOS app, widget extension/App Intents extension; no asumir sync cross-device.
- **Onboarding/token:** flujo de login/token cuando solo está el Watch; QR/deep link/code pairing; revocación/rotación; fallback vía iPhone.
- **Notifications:** permisos en Watch, local vs APNs, actionable text input, background notification handling, duplicados iPhone+Watch.
- **Complication/widget:** familias soportadas, update budget, tap target, si puede lanzar captura o solo abrir app; Smart Stack behavior.
- **Privacidad/App Review:** privacy manifest, App Privacy labels, uso de voz/texto, endpoint Hermes, logs, export compliance por HTTPS/encryption.
- **TestFlight:** instalación en watchOS companion/independent, internal/external review, símbolos/crash logs, Release build con optimización.

## 4) Fable 5 technical audit

Usar estas cinco “fábulas” como auditoría técnica antes de escribir demasiado código. Cada una debe tener test manual en Watch físico, logs, captura de pantalla si aplica y criterio pass/fail.

1. **Fábula de la muñeca levantada:** Jose levanta la muñeca, toca “Capturar”, dicta 8–20 palabras, recibe confirmación.  
   - Probar: dictado, persistencia local, POST Hermes, feedback haptic/visual.  
   - Falla típica a evitar: webhook OK pero UI queda incierta; texto vacío; token en log.

2. **Fábula de la muñeca caída:** Jose dicta, baja la muñeca antes de terminar la red.  
   - Probar: evento ya persistido, request cancelado/reemplazado por retry/background, no pérdida, notificación o estado “pendiente”.  
   - Falla típica: `dataTask` muere y la captura desaparece.

3. **Fábula de Siri sin iPhone:** Jose dice “Oye Siri, captura en Hermes …” en el Watch con iPhone lejos/bloqueado.  
   - Probar: App Shortcut visible, frase reconocida, auth policy correcta, ejecución sin companion, manejo de red ausente.  
   - Falla típica: Siri invoca intent en iPhone o pide abrir app sin salida clara.

4. **Fábula del puente iPhone/Shortcuts:** Jose usa Shortcuts existentes y la app nativa el mismo día.  
   - Probar: no hay duplicados; WatchConnectivity solo mejora bootstrap/sync; Shortcuts HTTP siguen funcionando; tokens no se pisan.  
   - Falla típica: migración rompe automatizaciones previas o crea dos capturas por comando.

5. **Fábula de TestFlight/App Review:** build Release fresh install en Watch de tester.  
   - Probar: signing, entitlements, onboarding, privacy prompts, notifications, crash logs, ATS, privacy manifest/App Privacy.  
   - Falla típica: funciona en Debug/simulador pero falla por provisioning, entitlement o permiso en Release.

## 5) Checklist de investigación/prototipo

### App Intents / Shortcuts
- [ ] Crear intent mínimo `CaptureToHermesIntent` en watchOS target.
- [ ] Crear `AppShortcutsProvider` con frases ES/EN sin PII/secrets.
- [ ] Ejecutar desde Shortcuts app, Siri y, si aplica, Action button/complication.
- [ ] Probar locked/unlocked y iPhone presente/ausente.
- [ ] Registrar qué APIs requieren watchOS/iOS nuevos (`supportedModes`, `allowedExecutionTargets`, extension targets).

### Dictado / input rápido
- [ ] Prototipo con `presentTextInputController(withSuggestions:allowedInputMode:)`.
- [ ] Prototipo SwiftUI equivalente si el target usa SwiftUI puro.
- [ ] Medir cancelación, strings vacíos, idioma, emojis, latencia.
- [ ] Probar `UNTextInputNotificationAction` como reply/capture desde notificación.

### HTTPS / Hermes webhook
- [ ] Definir contrato Hermes: endpoint, auth header, idempotency key, payload mínimo, timeout, response.
- [ ] Validar ATS/TLS del dominio Hermes desde Watch físico.
- [ ] Probar red por iPhone proxy, Wi‑Fi, celular, sin iPhone, mala conectividad.
- [ ] Implementar cola local: `pending/sent/failed`, retry backoff, dedupe por UUID.

### Background / reliability
- [ ] Probar foreground `URLSession` y transición a background.
- [ ] Probar background session con payload representativo; confirmar si upload POST JSON requiere file upload.
- [ ] Manejar `WKURLSessionRefreshBackgroundTask` y app activation retry.
- [ ] No depender de background refresh periódico para captura crítica.

### WatchConnectivity / companion iOS
- [ ] Implementar `isReachable` + `sendMessage` para bootstrap rápido.
- [ ] Implementar `transferUserInfo` para sync garantizado/oportunista.
- [ ] Implementar `updateApplicationContext` para estado latest.
- [ ] Probar iPhone apagado, app iOS no instalada, companion no abierto, Watch fuera de rango.

### Credenciales / entitlements
- [ ] Keychain access en watch app e iOS app.
- [ ] Validar App Groups/Keychain Sharing por target y extension.
- [ ] Token bootstrap seguro: QR/code pairing/deep link desde iPhone.
- [ ] Rotación y revocación de token; no guardar en UserDefaults ni logs.

### Notifications / widgets
- [ ] Solicitar permisos en contexto.
- [ ] Local notification para “pendiente/falló/enviado”.
- [ ] APNs para confirmación servidor si Hermes procesa async.
- [ ] WidgetKit complication mínima: estado cola + tap-to-open.
- [ ] Validar update budget y behavior en Smart Stack/watch face.

### Privacy / release
- [ ] `PrivacyInfo.xcprivacy` y required reason APIs.
- [ ] App Privacy labels: user content, identifiers, diagnostics, endpoint Hermes.
- [ ] Release archive + TestFlight internal.
- [ ] Probar fresh install, upgrade, logout/revoke, crash log symbolication.

## 6) Arquitectura MVP sugerida

- **Watch app:** pantalla principal con botones “Nota”, “Tarea”, “Idea”, “Comando”; dictado/sugerencias; cola local; envío HTTPS directo a Hermes; haptic/estado.
- **App Intents:** wrappers de captura que reutilizan el mismo servicio local/cola; frases App Shortcuts para Siri/Shortcuts.
- **iOS companion:** configuración cómoda, pairing token Hermes, edición de presets, logs/estado; mantiene Shortcuts existentes como fallback.
- **Hermes side:** webhook idempotente con `capture_id`, auth por token scoped, response rápido; procesamiento largo async con notificación/estado.
- **Widget/complication:** muestra cola/último envío y abre captura; interactividad directa solo si el target watchOS lo soporta tras validación.
