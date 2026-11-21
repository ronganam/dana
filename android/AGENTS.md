# AGENTS.md

Android clients of **Dana** (the assistant). The server is `../server` (its `AGENTS.md` / `docs/` cover the APIs); the repository map is `../AGENTS.md`. This file is the map — read the matching `docs/` file before changing an area. Your own server, devices (serials), TV defaults, Firebase config and publish token are not in the repository: they live in your site (`../site/android.env`, `tv-defaults.json`, `google-services.json`; `../docs/site.md`), and `../site/AGENTS.md` describes the deployment when it exists.

## Modules and variants

| Variant | Package | Source sets | Built on | Build / deploy |
|---|---|---|---|---|
| `:app` **phone** flavor | `app.ganam.assistant` | `main` + `phone` | Galaxy Z Fold | `bin/deploy-app.sh` publishes; install `adb -s <phone> install -r app/build/outputs/apk/phone/release/app-phone-release.apk` |
| `:app` **kiosk** flavor ("Dana Kiosk") | `app.ganam.assistant.kiosk` | `main` + `kiosk` + `wake` + `nopush` | Galaxy Tab S4 | `bin/deploy-kiosk.sh` |
| `:app` **tv** flavor ("Dana TV") | `app.ganam.assistant.tv` | `main` + `tv` + `wake` + `nopush` | TCL 75C7K (Google TV) | `bin/deploy-tv.sh` |
| `:wear` | `app.ganam.assistant` (same id + **same signing key** as phone) | `wear/` | OnePlus Watch 2 | `bin/deploy-app.sh --watch-ip <ip:port \| serial>` |
| `:watchface` | — | no committed Kotlin sources | | not deployed |

- Flavor rules: shared code in `main`; phone-only code with its own dependencies in `src/phone` (Firebase push), with a no-op twin in `src/nopush` for kiosk/TV; wake word + hands-free session in `src/wake` (kiosk + TV). Phone-only manifest entries stay in `main` and the kiosk/TV manifests drop them with `tools:node="remove"` — add the removal when you add a phone-only component. `BuildConfig.KIOSK` / `BuildConfig.TV` for small runtime differences.
- An emulator (`emulator-5554`) is often attached: always pass `-s <serial>`.

## Build, test, publish

> [!WARNING]
> Never run bare `./gradlew installDebug`: it runs for every module and can put the Wear APK on the phone. Always name the module and flavor.

**Fast loop** (edit → build → run, emulators only — the script refuses any serial that isn't `emulator-*`):

```bash
bin/dev-install.sh phone --boot    # :app phone debug → emulator-5554 (AVD "ganam"); --boot starts the AVD if needed
bin/dev-install.sh watch --boot    # :wear debug → emulator-5556 (AVD "dana_watch"; booted alone by other means it is emulator-5554: pass -s)
bin/dev-install.sh kiosk|tv        # the other flavors, same emulator-5554
# by hand: ./gradlew :app:assemblePhoneDebug && adb -s emulator-5554 install -r -t app/build/outputs/apk/phone/debug/app-phone-debug.apk \
#   && adb -s emulator-5554 shell am start -n app.ganam.assistant/.MainActivity
# watch:   ./gradlew :wear:assembleDebug && adb -s emulator-5556 install -r -t wear/build/outputs/apk/debug/wear-debug.apk \
#   && adb -s emulator-5556 shell am start -n app.ganam.assistant/app.ganam.assistant.wear.MainActivity
```

Build only the variant you need (`:app:assemblePhoneDebug`, not `assembleDebug`). With a warm daemon a one-line change rebuilds the phone APK in ~3 s and a no-change build is <1 s (configuration cache); a clean phone debug build ~30 s. Boot one emulator at a time and stop it after (`adb -s <serial> emu kill`); `./gradlew --stop` frees the daemons (Gradle `-Xmx4g` + Kotlin daemon `-Xmx3g` in `gradle.properties`: measured peak RSS ~7.5 GB for the phone + watch release build, ~9 GB building all eight variants at once). The first compile after the Kotlin daemon starts is slower (~45 s clean) than with a warm one (~26 s).

```bash
./gradlew :app:compilePhoneDebugKotlin :app:compileKioskDebugKotlin :app:compileTvDebugKotlin   # compile all flavors
./gradlew :app:testPhoneDebugUnitTest      # JVM unit tests (app/src/test); no lint, no CI, no instrumentation tests
./gradlew :wear:testDebugUnitTest          # the watch's JVM unit tests (wear/src/test: the shopping model)

# Publish phone + watch APKs to the server (bumps versionName/Code of :app and :wear; notes without semicolons).
# SERVER_URL and the publish token (or APP_PUBLISH_TOKEN_CMD) come from ../site/android.env.
bin/deploy-app.sh --bump X.Y.Z --notes "What changed"
```

The phone also offers published versions in Settings → App Update. Commit the version bump (`app/` and `wear/build.gradle.kts`) with the change.

**Critical rules** (details in `docs/release.md`):
- `:app` and `:wear` must be signed with the **same key**, or every watch→phone message is silently dropped.
- Release builds run R8 (phone flavor: pass-through via `proguard-phone.pro`); kiosk/TV deploy scripts also force AOT compilation.
- The Gradle daemon runs on JDK 21 (`gradle/gradle-daemon-jvm.properties`; a JDK outside the usual places: `org.gradle.java.installations.paths` in `~/.gradle/gradle.properties`); `gradle.properties` turns on the build cache, configuration cache and parallel builds; keep build logic configuration-cache safe (no `project` access at execution time).
- A deployment's setup lives in its site, which the build reads when present: `../site/android.env` (`SERVER_URL`, `LAN_DNS_SERVERS`), `tv-defaults.json`, `google-services.json` (direct push). Release signing stays in the gitignored `keystore.properties` — `docs/release.md` → Your site.

## Architecture (shared)

- **ViewModel + controllers**: `MainViewModel` (an `AndroidViewModel`) is a thin composition root. Feature logic lives in plain controller classes in `ui/viewmodels/`: `ChatController` (the shared thread / sessions, SSE stream, push-to-talk voice, undo), `AboutController`, `TasksController`, `MemoryController`, `ProjectsController`, `CallsController` (call recordings/upload), `NotificationsController` (Inbox), `RealtimeCallController`, `ShellController` (main-screen place, picks, thread open/closed, ask-bar draft), `DayController` (today's calendar events for the day strip), `NowController` (`/api/now` queue, act + Undo, disk cache), `NotificationOverviewController` (the Notifications place), `SearchController` (ask-bar search), `MoneyController` (Money under More: core, with `MoneyDashState` / `MoneyLedgerState` for its views; `data/money/`), `ShoppingController` (Shopping under More: live stream, adds, editor, invitations, sharing; its data lives in `data/shopping/`). `MainViewModel` re-exposes their state/actions so screens keep calling `viewModel.x`. New feature code goes in a controller, not in `MainViewModel`. No DI.
- **Errors**: controllers run mutations through `ControllerContext.action("Couldn't …") { … }`. Failures go to `MainViewModel.messages` and show as a Snackbar. Don't add empty `catch {}` blocks. Background loads use `ctx.load(...)` (logged, not shown).
- **Networking**: Raw OkHttp with manual `org.json` parsing (no Retrofit, no Gson/Moshi). All clients come from `data/network/HttpClients.kt` (one shared pool). Don't build new `OkHttpClient`s. `ApiService` calls go through `fetch()`/`send()`, which close responses and throw `ApiException` (HTTP status) or `IOException` (unreachable).
- **Server reachability**: `data/network/ServerStatusMonitor` probes `GET /api/health` (server ≥ 6.3.0; older servers answer 404, which still counts as reachable). It probes on network changes (VPN up/down) and while the UI is visible (`start()`/`stop()` in `MainActivity` and the voice session). `ServerStatusBanner` shows it. Sending and push-to-talk refuse to start while it's OFFLINE.
- **Data layer**: `data/network/ApiService.kt` wraps all HTTP calls. `data/models/Models.kt` has all data classes.
- **Chat cache**: `data/chat/ChatHistoryStore` keeps one JSON file per session in `filesDir/chat_history/` (newest 40). It's only a fallback; the server is the source of truth. Don't put transcripts in SharedPreferences.
- **Main screen (phone)**: `ui/shell/DanaShell` — places Now, Reminders, Notifications, Projects (with `knowledge`), More (Shopping and Money there, each with its integration in `/api/me`), the ask bar, one shared thread with Dana (`session_id: "main"`, server `docs/agent-actions.md`; sheet when folded, right pane unfolded; Undo under replies, "On screen" context) and a day strip; About you lives under More; each place is a `Destination` (list + stage) in `ui/shell/destinations/`. Settings → "Previous layout" (`LayoutSettings`, pref `previous_layout`) brings back the old tabbed screen (`PreviousMainScreen` in `MainActivity`; the old screens are kept). Server pings that should refresh what's on screen go through `data/sync/LiveRefresh`. Details in `docs/phone.md`, layout in `DESIGN.md`.
- **UI**: design system is "Ink & Paper" — see `DESIGN.md` (tokens in `ui/theme/`, shared bits in `ui/components/DesignSystem.kt`, window tiers/panes in `ui/adaptive/`). Screens adapt COMPACT/MEDIUM/EXPANDED via `windowTier()` (the owner's Fold at 360 dpi: cover ≈ 555×876 dp = COMPACT, inner ≈ 1088×821 dp, 821×1088 rotated = EXPANDED with the rail — see `docs/phone.md`); the nav rail only appears from 720dp. Three panes need ~900dp of content width, so the rotated inner screen gets two (Calls, Reminders). The power-button assistant sheet is `ui/components/FloatingAssistantOverlay.kt` + `ui/components/assistant/`. Its opt-in **Screen** chip (screen as context for one message, off by default, `data/screen/` + `ScreenShareController`) is described in `docs/phone.md`. Debug builds allow cleartext HTTP (mock servers at `http://10.0.2.2:PORT`). Compose screens in `ui/screens/` (the Projects screen is split across `ui/screens/projects/`), shared components in `ui/components/`. One legacy XML layout remains (`assistant_bottom_sheet.xml`).
- **State**: Kotlin `StateFlow` / `MutableStateFlow` throughout.
- **Prefs**: `SharedPreferences` file `GanamPrefs`: server config, current session id (previous layout only; the new layout, the overlay, the watch, kiosk and TV all use the thread `main`), small flags only.
- **Name & icon**: the assistant is **Dana** in every user-facing string (internal ids, package names and the `"Assistant"` chat sender stay as they are). The launcher icon (phone + watch, adaptive with monochrome) is the italic Instrument Serif "d" in paper (#F2EBDD) with a vermilion full stop (#FF6A3D) on ink (#13100E). Its vector paths are generated from `res/font/instrument_serif_italic.ttf` with fontTools; don't hand-edit them.
- **Offline queues** (`data/sync/`): reminder Complete/Snooze (phone alarm and watch) go through `TaskActionQueue`. It tries once (6s), queues to `filesDir/pending_task_actions.json` if the server is unreachable or answers 5xx, and `PendingSyncWorker` (WorkManager) or the server coming back ONLINE flushes it. Shopping changes have their own ordered queue (`data/shopping/ShoppingStore`, `filesDir/pending_shopping.json`) flushed the same ways (`docs/shopping.md`). The watch keeps its own shopping requests while the phone is out of reach (`GanamWearPrefs` `shopping_pending`, sent when a snapshot arrives or the screen opens; `docs/wear.md` → Shopping). Call-recording uploads run in `CallUploadWorker` (network constraint, backoff, foreground `dataSync` job, Retry action), with handled recordings remembered in `filesDir/call_uploads.json`. WorkManager is the only library that brings Room, and only internally. **WorkManager starts on demand** from `GanamApp` (`Configuration.Provider`, default process = the package; the androidx.startup initializer is removed in the manifest) so it also works in the assistant sheet's `:session` process — content providers only run in the main process, and the sheet's `MainViewModel.initialize` → Inbox sync crashed there with "WorkManager is not initialized" (7.4.1–7.8.0). The TV flavor's `DanaTvApplication` extends `GanamApp`. Still no codegen in this project.

## Key services

| Service | Purpose |
|---|---|
| `GanamVoiceInteractionService` | System-level voice interaction (BIND_VOICE_INTERACTION) |
| `GanamVoiceInteractionSessionService` | Voice session handling, runs in `:session` process |
| `GanamRecognitionService` | Custom speech recognition service |
| `CallRecordingObserverService` | Foreground service for call recording detection/upload |
| `DanaNotificationListener` | Notification access → Dana's Inbox, plus bank sign-in codes / bank and card notifications for the `money` kind (`data/money/`; phone flavor only) |
| `DanaMessagingService` | FCM wake-up pings → `PhoneEvents.catchUp` (phone flavor only; see `docs/notifications.md`) |
| `SuggestionsPopupActivity` | New suggestions pop-up (server push → `SuggestionsReceiver.show`) |
| `CallDebriefActivity` / `ShortCallActivity` | Native call screens opened from notifications (Debrief; short-call upload prompt) |

## Conventions

- Toolchain: Gradle 9.9.1, AGP 9.5.1 (built-in Kotlin: modules apply no `kotlin-android` plugin; versions in the root `build.gradle.kts`), Kotlin 2.4.21 (K2) with the Compose compiler Gradle plugin (strong skipping kept **off** in both modules — the flag is gone in Kotlin 2.5, so turning it on and testing the screens comes first). JVM target 1.8, compileSdk/targetSdk 36, minSdk 26; Compose BOM 2024.04.00, Material3, Navigation 2.7.7. `android.r8.strictFullModeForKeepRules=false` keeps the AGP 8 meaning of the libraries' keep rules.
- Extra source dirs in `app/build.gradle.kts` go in `kotlin.directories` (AGP 9: Kotlin files added to `java.directories` are not compiled).
- No codegen, no annotation processing, no Hilt/Dagger/Room, no Retrofit/Gson; `org.json` + OkHttp.
- Packages: `app.ganam.assistant.{data,ui}.{models,network,screens,components,theme,viewmodels}`, plus `push/`, `wake/`, `tv/`, `kiosk/`.
- Hebrew/RTL: text direction is decided per message/line by script, never assumed. Mixed " · " kicker lines go through `isolatedParts()` (each part isolated, LTR dots) so a Hebrew name can't reorder them.
- Server URL and API token come from Settings (`GanamPrefs`); background code uses `ApiService.fromPrefs(context)`.

## Gotchas

- No early `return` inside a `@Composable` (crashed the app twice): branch into a child composable.

- The assistant sheet runs in the `:session` process: no content providers there (WorkManager is initialised on demand in `GanamApp`; push setup only runs in the main process). It has its own `MainViewModel`, so it re-reads `previous_layout` and the thread from the server; it never creates a session.
- Voice services need the app to be the device's assist app (`BIND_VOICE_INTERACTION`).
- `CallRecordingObserverService` is a `specialUse` foreground service and also keeps the process alive (so "app start" can be weeks apart).

## Docs index

| Doc | Read before touching |
|---|---|
| `docs/phone.md` | Main screen (shell, thread, Reminders editor, day strip), chat stream / voice, offline queues, reminders + alarm screen, call recordings + Debrief, foldable sizes |
| `docs/projects.md` | Projects place: the new model (sections, decisions compared, files, budget), its data layer `data/projects/`, panes folded / unfolded |
| `docs/money.md` | Money (More → Money): `MoneyController` core, dashboards, Transactions / Accounts / Save, the panes |
| `docs/shopping.md` | Shopping (under More), offline store + queue, live stream / push, widget + quick add, the watch's shopping data and actions (the watch screen itself: `docs/wear.md`) |
| `docs/notifications.md` | Notification Inbox, direct push (FCM), urgent alerts, suggestion pop-up, buttons, snooze |
| `docs/people.md` | Caller card, People / weekly review screens, briefing / meeting / review pushes, home-screen widgets |
| `docs/wear.md` | Watch app: performance rules, hands-free turns, phone bridge, alarm, tiles / complications, the Shopping screen (and its debug fake phone) |
| `docs/kiosk.md` | Wall-tablet kiosk, wake word, voice turn tuning |
| `docs/tv.md` | Dana TV: home override, key service, camera PiP, TCL/MTK quirks |
| `docs/release.md` | Signing, in-app updates, R8/AOT, JDK, local defaults (Firebase config, server URL, TV defaults) |
| `DESIGN.md` | "Ink & Paper" design system |

Keep docs current in the same commit as the code; add new areas as a doc + one line here rather than growing this file.
