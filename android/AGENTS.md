# AGENTS.md

Android clients of **Dana** (the assistant). The server is `../server` (its `AGENTS.md` / `docs/` cover the APIs); the repository map is `../AGENTS.md`. This file is the map — read the matching `docs/` file before changing an area. Your own server, devices (serials), TV defaults, Firebase config and publish token are not in the repository: they live in your site (`../site/android.env`, `tv-defaults.json`, `google-services.json`; `../docs/site.md`), and `../site/AGENTS.md` describes the deployment when it exists.

## Modules and variants

| Variant | Package | Source sets | Target devices | Build / deploy |
|---|---|---|---|---|
| `:app` **phone** flavor | `io.github.ronganam.dana` | `main` + `phone` | Android 8+ phones, foldables included | `bin/deploy-app.sh` publishes; install `adb -s <phone> install -r app/build/outputs/apk/phone/release/app-phone-release.apk` |
| `:app` **kiosk** flavor ("Dana Kiosk") | `io.github.ronganam.dana.kiosk` | `main` + `kiosk` + `wake` + `nopush` | a wall-mounted Android tablet | `bin/deploy-kiosk.sh` |
| `:app` **tv** flavor ("Dana TV") | `io.github.ronganam.dana.tv` | `main` + `tv` + `wake` + `nopush` | Google TV / Android TV | `bin/deploy-tv.sh` |
| `:wear` | `io.github.ronganam.dana` (same id + **same signing key** as phone) | `wear/` | Wear OS 3+ watches | `bin/deploy-app.sh --watch-ip <ip:port \| serial>` |
| `:watchface` | `io.github.ronganam.dana.watchface` (same signing key) | `watchface/` | Wear OS 4+ (Watch Face Format, resources only) | `bin/deploy-app.sh --watch-ip …` installs it too; its slots default to `:wear`'s complications (events / tasks 1–4) |
| `:protocol` | Android library `io.github.ronganam.dana.protocol` | `protocol/` | | the phone ↔ watch Data Layer paths (`WearPaths`) and payloads (`WearPayloads.kt`), used by `:app` and `:wear` |

- Flavor rules (by addition): `main` holds what every flavor has — code, and in `src/main/AndroidManifest.xml` only the permissions and components all three use. Phone-only manifest entries (call recordings, watch bridge, system assistant, notification Inbox, pop-ups, push, widgets, in-app updates, the launcher entry) are in `src/phone/AndroidManifest.xml`; the kiosk and TV manifests only add their own. Never add a component to `main` and strip it with `tools:node="remove"`. Phone-only code with its own dependencies or no shared callers goes in `src/phone` (push: FCM and UnifiedPush, widgets, the voice-interaction services), with a no-op twin in `src/nopush` for kiosk/TV when shared code calls it; wake word + hands-free session in `src/wake` (kiosk + TV). `BuildConfig.KIOSK` / `BuildConfig.TV` for small runtime differences. Every permission and why: `docs/permissions.md`.
- `docs/kiosk.md`, `docs/tv.md` and `docs/wear.md` name the device each flavor was tuned on and its quirks; check a change on your own hardware (or an emulator of the same form factor) for each flavor it touches.
- Several devices or emulators may be attached at once: always pass `-s <serial>` to `adb` and the scripts.

## Build, test, publish

**Setup**: the Android SDK (`ANDROID_HOME=~/Android/Sdk`, or `sdk.dir=/path/to/sdk` in a gitignored `local.properties` here) and a JDK 21 for the Gradle daemon; without one, Gradle downloads it (foojay). Details: `docs/release.md` → Setup.

> [!WARNING]
> Never run bare `./gradlew installDebug`: it runs for every module and can put the Wear APK on the phone. Always name the module and flavor.

**Fast loop** (edit → build → run, emulators only — the script refuses any serial that isn't `emulator-*`):

```bash
bin/dev-install.sh phone --boot    # :app phone debug → emulator-5554 (AVD $PHONE_AVD, default "dana_phone"; set it in ../site/android.env); --boot starts the AVD if needed
bin/dev-install.sh watch --boot    # :wear debug → emulator-5556 (AVD "dana_watch"; booted alone by other means it is emulator-5554: pass -s)
bin/dev-install.sh kiosk|tv        # the other flavors, same emulator-5554
# by hand: ./gradlew :app:assemblePhoneDebug && adb -s emulator-5554 install -r -t app/build/outputs/apk/phone/debug/app-phone-debug.apk \
#   && adb -s emulator-5554 shell am start -n io.github.ronganam.dana/.MainActivity
# watch:   ./gradlew :wear:assembleDebug && adb -s emulator-5556 install -r -t wear/build/outputs/apk/debug/wear-debug.apk \
#   && adb -s emulator-5556 shell am start -n io.github.ronganam.dana/io.github.ronganam.dana.wear.MainActivity
```

Build only the variant you need (`:app:assemblePhoneDebug`, not `assembleDebug`). With a warm daemon a one-line change rebuilds the phone APK in ~3 s and a no-change build is <1 s (configuration cache); a clean phone debug build ~30 s. Boot one emulator at a time and stop it after (`adb -s <serial> emu kill`); `./gradlew --stop` frees the daemons (Gradle `-Xmx4g` + Kotlin daemon `-Xmx3g` in `gradle.properties`: measured peak RSS ~7.5 GB for the phone + watch release build, ~9 GB building all eight variants at once). The first compile after the Kotlin daemon starts is slower (~45 s clean) than with a warm one (~26 s).

```bash
./gradlew :app:compilePhoneDebugKotlin :app:compileKioskDebugKotlin :app:compileTvDebugKotlin   # compile all flavors
./gradlew :app:testPhoneDebugUnitTest      # JVM unit tests (app/src/test; contract tests read ../server/tests/fixtures directly); no instrumentation tests
./gradlew :wear:testDebugUnitTest          # the watch's JVM unit tests (wear/src/test: the shopping model)
./gradlew :protocol:testDebugUnitTest      # the phone ↔ watch payloads round-trip
./gradlew :app:lintPhoneDebug :wear:lintDebug   # lint; :app's known issues are in app/lint-baseline.xml, so only new ones fail

# Publish phone + watch APKs to the server (bumps versionName/Code of :app and :wear; notes without semicolons).
# SERVER_URL and the publish token (or APP_PUBLISH_TOKEN_CMD) come from ../site/android.env.
bin/deploy-app.sh --bump X.Y.Z --notes "What changed"
```

CI (`../.github/workflows/ci.yml`, the `android` job, on changes under `android/` or `server/tests/fixtures/`) runs the unit tests, `:app:assemblePhoneDebug`, `:wear:assembleDebug`, both lint tasks and the kiosk / TV compiles, and fails when a copy of a server fixture (`*_web.json`, `golden.sse`) sits anywhere under `android/` and differs from the server's. The phone also offers published versions in Settings → App Update. Commit the version bump (`app/` and `wear/build.gradle.kts`) with the change.

**Critical rules** (details in `docs/release.md`):
- `:app` and `:wear` must be signed with the **same key**, or every watch→phone message is silently dropped.
- Release builds run R8 (phone flavor: pass-through via `proguard-phone.pro`); kiosk/TV deploy scripts also force AOT compilation.
- The Gradle daemon runs on JDK 21 (`gradle/gradle-daemon-jvm.properties`, downloaded through its `toolchainUrl.*` entries when the machine has none; regenerate them with `./gradlew updateDaemonJvm --jvm-version=21`); `gradle.properties` turns on the build cache, configuration cache and parallel builds; keep build logic configuration-cache safe (no `project` access at execution time).
- Release builds reach the server over **HTTPS only**; debug builds also allow plain http (`src/debug`). A LAN server without TLS: `ALLOW_CLEARTEXT=true` in your site's `android.env` (`docs/release.md` → HTTPS).
- A deployment's setup lives in its site, which the build reads when present: `../site/android.env` (`SERVER_URL`, `LAN_DNS_SERVERS`, `ALLOW_CLEARTEXT`), `tv-defaults.json`, `google-services.json` (direct push through FCM; without it the phone uses UnifiedPush). Release signing stays in the gitignored `keystore.properties` — `docs/release.md` → Your site.

## Architecture (shared)

- **ViewModel + controllers**: `MainViewModel` (an `AndroidViewModel`) is a thin composition root. Feature logic lives in plain controller classes in `ui/viewmodels/`: `ChatController` (the shared thread / sessions and the requests that start or follow a run; it composes `ChatTranscript`, `ChatRunRenderer`, `ChatPrompts`, `ThreadView`, `RunStreamClient`, `ChatVoice`, `ChatUndos`, `ChatCache` — `docs/phone.md` → Chat stream), `AboutController`, `TasksController`, `MemoryController`, `ProjectsController`, `CallsController` (call recordings/upload), `NotificationsController` (Inbox), `RealtimeCallController`, `ShellController` (main-screen place, picks, thread open/closed, ask-bar draft), `DayController` (today's calendar events for the day strip), `NowController` (`/api/now` queue, act + Undo, disk cache), `NotificationOverviewController` (the Notifications place), `SearchController` (ask-bar search), `MoneyController` (Money under More: core, with `MoneyDashState` / `MoneyLedgerState` for its views; `data/money/`), `ShoppingController` (Shopping under More: live stream, adds, editor, invitations, sharing; its data lives in `data/shopping/`). Screens use a controller directly (`viewModel.chatController`, `viewModel.tasksController`, …); `MainViewModel` keeps only the wiring between them. New feature code goes in a controller, not in `MainViewModel`. No DI.
- **Errors**: controllers run mutations through `ControllerContext.action("Couldn't …") { … }`. Failures go to `MainViewModel.messages` and show as a Snackbar. Don't add empty `catch {}` blocks. Background loads use `ctx.load(...)` (logged, not shown).
- **Networking**: OkHttp + kotlinx.serialization (no Retrofit, no Gson/Moshi). All clients come from `data/network/HttpClients.kt` (one shared pool). Don't build new `OkHttpClient`s. `ApiService` is one typed client per area (`api.chat`, `api.tasks`, `api.calls`, `api.notifications`, `api.now`, `api.people`, `api.about`, `api.devices`, `api.money`, `api.shopping`) over one `ApiClient` transport, which closes responses and throws `ApiException` (HTTP status) or `IOException` (unreachable). JSON is parsed only in the data layer: wire classes (`@Serializable`, private to their `*Api.kt`) decode with `ApiJson` (snake_case names, unknown keys ignored, lenient values) and map to the models with `toModel()`; screens and controllers never see `JsonObject`. Payloads must match the server exactly: the contract tests decode the server's own fixtures (`server/tests/fixtures/*_web.json`, `golden.sse`, on the unit-test classpath through `app/build.gradle.kts`), so a server change that breaks the app fails here. A 403 `not_connected` names its `addon`; `Me.dropIfNotConnected` forgets it.
- **Server reachability**: `data/network/ServerStatusMonitor` probes `GET /api/health` (server ≥ 6.3.0; older servers answer 404, which still counts as reachable). It probes on network changes (VPN up/down) and while the UI is visible (`start()`/`stop()` in `MainActivity` and the voice session). `ServerStatusBanner` shows it. Sending and push-to-talk refuse to start while it's OFFLINE.
- **Data layer**: `data/network/ApiService.kt` and its `*Api.kt` areas wrap all HTTP calls; `data/models/` has the data classes. `/api/me` (`Me`) gives the user's `addons`, `languages` and `currency`. **Money** is written only through `data/money/MoneyFormat` (the user's currency, the phone's locale; amounts from the server are in minor units).
- **Chat cache**: `data/chat/ChatHistoryStore` keeps one JSON file per session in `filesDir/chat_history/` (newest 40). It's only a fallback; the server is the source of truth. Don't put transcripts in SharedPreferences.
- **Main screen (phone)**: `ui/shell/DanaShell` — places Now, Reminders, Notifications, Projects (with `knowledge`), More (Shopping and Money there, each with its addon in `/api/me`), the ask bar, one shared thread with Dana (`session_id: "main"`, server `docs/agent-actions.md`; sheet when folded, right pane unfolded; Undo under replies, "On screen" context) and a day strip; About you lives under More; each place is a `Destination` (list + stage) in `ui/shell/destinations/`. Server pings that should refresh what's on screen go through `data/sync/LiveRefresh`. Details in `docs/phone.md`, layout in `DESIGN.md`.
- **UI**: design system is "Ink & Paper" — see `DESIGN.md` (tokens in `ui/theme/`, shared bits in `ui/components/DesignSystem.kt`, window tiers/panes in `ui/adaptive/`). Screens adapt COMPACT/MEDIUM/EXPANDED via `windowTier()` (a phone or a foldable's cover screen = COMPACT, a foldable's inner screen or a tablet = EXPANDED with the rail — sizes to test at in `docs/phone.md`); the nav rail only appears from 720dp. Three panes need ~900dp of content width, so a foldable's inner screen in portrait gets two (Calls, Reminders). The power-button assistant sheet is `ui/components/FloatingAssistantOverlay.kt` + `ui/components/assistant/`. Its opt-in **Screen** chip (screen as context for one message, off by default, `data/screen/` + `ScreenShareController`) is described in `docs/phone.md`. Debug builds allow cleartext HTTP (mock servers at `http://10.0.2.2:PORT`). Compose screens in `ui/screens/` (the Projects screen is split across `ui/screens/projects/`), shared components in `ui/components/`. No XML layouts. The reminder alarm is `ReminderActivity` (window, ringing) + `ReminderScreen` (UI) + `SnoozeDial` (the dial's tree and geometry) + `ReminderActions` (complete / snooze through `TaskActionQueue`).
- **State**: Kotlin `StateFlow` / `MutableStateFlow` throughout.
- **Prefs**: one `SharedPreferences` file (`ServerConfig.prefs(context)`, file `DanaPrefs`): server config and small flags only (the phone, the overlay, the watch, kiosk and TV all talk in the thread `main`).
- **Name & icon**: the assistant is **Dana** in every user-facing string (the package is `io.github.ronganam.dana`; the `"Assistant"` chat sender stays as it is). The launcher icon (phone + watch, adaptive with monochrome) is the italic Instrument Serif "d" in paper (#F2EBDD) with a vermilion full stop (#FF6A3D) on ink (#13100E). Its vector paths are generated from `res/font/instrument_serif_italic.ttf` with fontTools; don't hand-edit them.
- **Offline queues** (`data/sync/`): reminder Complete/Snooze (phone alarm and watch) go through `TaskActionQueue`. It tries once (6s), queues to `filesDir/pending_task_actions.json` if the server is unreachable or answers 5xx, and `PendingSyncWorker` (WorkManager) or the server coming back ONLINE flushes it. Shopping changes have their own ordered queue (`data/shopping/ShoppingStore`, `filesDir/pending_shopping.json`) flushed the same ways (`docs/shopping.md`). The watch keeps its own shopping requests while the phone is out of reach (`DanaWearPrefs` `shopping_pending`, sent when a snapshot arrives or the screen opens; `docs/wear.md` → Shopping). Call-recording uploads run in `CallUploadWorker` (network constraint, backoff, foreground `dataSync` job, Retry action), with handled recordings remembered in `filesDir/call_uploads.json`. WorkManager is the only library that brings Room, and only internally. **WorkManager starts on demand** from `DanaApp` (`Configuration.Provider`, default process = the package; the androidx.startup initializer is removed in the manifest) so it also works in the assistant sheet's `:session` process — content providers only run in the main process, and the sheet's `MainViewModel.initialize` → Inbox sync crashed there with "WorkManager is not initialized" (7.4.1–7.8.0). The TV flavor's `DanaTvApplication` extends `DanaApp`. Still no codegen in this project.

## Key services

| Service | Purpose |
|---|---|
| `DanaVoiceInteractionService` | System-level voice interaction (BIND_VOICE_INTERACTION) |
| `DanaVoiceInteractionSessionService` | Voice session handling, runs in `:session` process |
| `DanaRecognitionService` | Custom speech recognition service |
| `CallRecordingObserverService` | Foreground service for call recording detection/upload; runs only once the user turns call recordings on |
| `DanaNotificationListener` | Notification access → Dana's Inbox, plus bank sign-in codes / bank and card notifications for the `money` addon (`data/money/`; phone flavor only) |
| `DanaUnifiedPushService` | UnifiedPush endpoints and wake-up pings from the distributor app (ntfy) → `PushPing` → `PhoneEvents.catchUp` (phone flavor; builds without Firebase, the released APKs among them; see `docs/notifications.md`) |
| `DanaMessagingService` | FCM wake-up pings → `PushPing` → `PhoneEvents.catchUp` (phone flavor, only a build with your own `google-services.json`) |
| `SuggestionsPopupActivity` | New suggestions pop-up (server push → `SuggestionsReceiver.show`) |
| `CallDebriefActivity` / `ShortCallActivity` | Native call screens opened from notifications (Debrief; short-call upload prompt) |

## Conventions

- Toolchain: Gradle 9.9.1, AGP 9.5.1 (built-in Kotlin: modules apply no `kotlin-android` plugin; versions in the root `build.gradle.kts`), Kotlin 2.4.21 (K2) with the Compose compiler Gradle plugin, kotlinx.serialization 1.13. JVM 17, compileSdk 37; app targetSdk 36 / minSdk 26 (Android 8: the kiosk tablet runs Android 10 — guard newer APIs with `SDK_INT` checks rather than raising it), watch targetSdk 34 / minSdk 30; Compose BOM 2026.09.00, Material3, Navigation 2.10. The build has no compiler warnings; keep it so. Lint: `app/lint-baseline.xml` holds the remaining known issues (style and a few deliberate ones); the watch has no baseline. `android.r8.strictFullModeForKeepRules=false` keeps the AGP 8 meaning of the libraries' keep rules.
- Extra source dirs in `app/build.gradle.kts` go in `kotlin.directories` (AGP 9: Kotlin files added to `java.directories` are not compiled).
- No annotation processing, no Hilt/Dagger/Room, no Retrofit/Gson; kotlinx.serialization (its compiler plugin is the only codegen) + OkHttp. `org.json` remains only in `CallUploads`' state file, `RealtimeCallEngine`, and the TV flavor's local config/HA client.
- User-visible text: the watch's strings, and the phone's notification channels, toasts and manifest labels, live in `res/values/strings.xml` (counts in `<plurals>`); new text there goes in resources too. The phone's Compose screens and the pure models their tests pin (`data/money/`, `ProjectsCore`, the watch's `ShopModel`) still hold English literals.
- Packages: `io.github.ronganam.dana.{data,ui}.{models,network,screens,components,theme,viewmodels}`, plus `push/`, `wake/`, `tv/`, `kiosk/`.
- Hebrew/RTL: text direction is decided per message/line by script, never assumed. Mixed " · " kicker lines go through `isolatedParts()` (each part isolated, LTR dots) so a Hebrew name can't reorder them.
- Server URL and API token: only through `data/network/ServerConfig` (`load` / `save`; the URL is normalized — scheme, no trailing slash — and falls back to the build's `DEFAULT_SERVER_URL`). The ViewModel holds one (`MainViewModel.serverConfig`, changed by `updateServerConfig`); background code uses `ServerConfig.load(context).api()`. No composable builds its own API client; Home Assistant zones come from `data/network/Zones`.

## Gotchas

- No early `return` inside a `@Composable` (crashed the app twice): branch into a child composable.

- The assistant sheet runs in the `:session` process: no content providers there (WorkManager is initialised on demand in `DanaApp`; push setup only runs in the main process). It has its own `MainViewModel`, so it re-reads the thread from the server; it never creates a session.
- Voice services need the app to be the device's assist app (`BIND_VOICE_INTERACTION`).
- `CallRecordingObserverService` is a `specialUse` foreground service and also keeps the process alive (so "app start" can be weeks apart).

## Docs index

| Doc | Read before touching |
|---|---|
| `docs/phone.md` | Main screen (shell, thread, Reminders editor, day strip), chat stream / voice, offline queues, reminders + alarm screen, call recordings + Debrief, foldable sizes |
| `docs/projects.md` | Projects place: the new model (sections, decisions compared, files, budget), its data layer `data/projects/`, panes folded / unfolded |
| `docs/money.md` | Money (More → Money): `MoneyController` core, dashboards, Transactions / Accounts / Save, the panes |
| `docs/shopping.md` | Shopping (under More), offline store + queue, live stream / push, widget + quick add, the watch's shopping data and actions (the watch screen itself: `docs/wear.md`) |
| `docs/notifications.md` | Notification Inbox, direct push (UnifiedPush / FCM, Settings → Push), urgent alerts, suggestion pop-up, buttons, snooze |
| `docs/people.md` | Caller card, People / weekly review screens, briefing / meeting / review pushes, home-screen widgets |
| `docs/wear.md` | Watch app: performance rules, hands-free turns, phone bridge, alarm, tiles / complications, the Shopping screen (and its debug fake phone) |
| `docs/kiosk.md` | Wall-tablet kiosk, wake word, voice turn tuning |
| `docs/tv.md` | Dana TV: home override, key service, camera PiP, TCL/MTK quirks |
| `docs/permissions.md` | Every permission and special access, per flavor; distribution (sideload / your server, not Play); call recordings opt-in |
| `docs/release.md` | Releases (the tag workflow, signing secrets, release assets, `versions.json`), setup (SDK, JDK), HTTPS / cleartext, signing, in-app updates, R8/AOT, local defaults (Firebase config, server URL, TV defaults) |
| `DESIGN.md` | "Ink & Paper" design system |

Keep docs current in the same commit as the code; add new areas as a doc + one line here rather than growing this file.
