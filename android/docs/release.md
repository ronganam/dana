# Releases, signing, R8

## Release signing & in-app updates

Release builds of `:app` and `:wear` are signed with a dedicated keystore (e.g. `dana-release.keystore`, gitignored) configured via `keystore.properties` at the repo root (also gitignored — copy `keystore.properties.example` and fill in real values; `keyAlias` defaults to `dana`). When `keystore.properties` is absent, release builds fall back to debug signing so plain `assembleRelease` still works.

**One-time migration note:** the release key differs from `~/.android/debug.keystore`, so the first release build must be installed with an uninstall first on both phone and watch (`adb uninstall io.github.ronganam.dana`), because `INSTALL_FAILED_UPDATE_INCOMPATIBLE` blocks cross-cert upgrades.

Update flow (server side: `server/core/app_updates.py`):

```bash
# bump versions, build signed APKs, publish to the assistant server
bin/deploy-app.sh --bump 6.1.0 --notes "What changed"

# optional adb installs (needs device/watch connectivity)
bin/deploy-app.sh --install-phone
bin/deploy-app.sh --watch-ip <watch-ip>:5555    # wear APK; uses adb wireless debugging
```

- Published APKs live in the server's data dir (`app-updates/` next to the DB) (`/api/app/version`, `/api/app/download/{file}`; publish with `POST /api/app/upload` + `APP_PUBLISH_TOKEN`).
- The phone app checks `/api/app/version` on launch and in **Settings > App Update**, downloads to `cacheDir/updates/`, verifies sha256, then hands off to the system installer (`FileProvider` + `REQUEST_INSTALL_PACKAGES`).
- Version tracking: `app` and `wear` `versionName`/`versionCode` are kept in sync by the script. The phone pushes its `version_code` in the `/dana_config` DataMap; the watch publishes its own version on the `/dana/version` data item (`WearVersionPublisher`). The watch itself cannot self-update — install over adb via the script.

## Wear ↔ Phone companion signing (CRITICAL — do not break)

`:app` and `:wear` share `applicationId = io.github.ronganam.dana`. Wear OS's data layer (Google Play Services `WearableService`) treats same-named packages on phone and watch as the same app and **enforces signature match**: if the two sides are signed with different certificates, all watch→phone messages are silently dropped (`Mismatched certificate ... Failed to deliver message` in the phone's logcat). Symptoms: watch shows normal UI but nothing reaches the phone; `WearMessageListenerService` never logs "Handled message path".

**The rule:** always build and install `:app` and `:wear` with the **same signing key** (same debug keystore / same `signingConfig`). This is not "cert pinning" and there is no config flag to relax the check — the only way to keep the companion working is identical signatures on both sides.

- Never build/install one side on a different machine, different keystore, or a regenerated `~/.android/debug.keystore` while leaving the other side as-is — the mismatch reappears.
- If certs ever diverge, verify with `apksigner verify --print-certs` (both APKs must show the same SHA-1) or `adb -s <serial> shell dumpsys package io.github.ronganam.dana | grep -A2 signatures=`.
- Fixing a mismatch requires uninstalling the stale side first (`INSTALL_FAILED_UPDATE_INCOMPATIBLE` blocks a direct upgrade across certs) — back up its `SharedPreferences` (`run-as ... tar/cp shared_prefs`) first to avoid losing server config.
- If a different-key build installed a side, rebuild it from this repo and `adb install` per-device; never let a bare `installDebug` touch both devices.

## Release builds: R8 and ahead-of-time compilation (CRITICAL for the tablet and the TV)

- Release builds run **R8** (`isMinifyEnabled = true`, rules in `app/proguard-rules.pro`; no obfuscation, so stack traces and log class names stay readable). ONNX Runtime has no consumer rules, so `ai.onnxruntime.**` is kept by hand. The **phone** flavor adds `app/proguard-phone.pro` (`-dontshrink -dontoptimize -dontobfuscate`), which makes R8 a pass-through there until it has been tried on the phone.
- `app/src/main/baseline-prof.txt` marks all of `io.github.ronganam.dana.**` hot, on top of the Compose libraries' own profiles. `bin/deploy-kiosk.sh` and `bin/deploy-tv.sh` then force-install the profile (the `androidx.profileinstaller.action.INSTALL_PROFILE` broadcast) and run `cmd package compile -m speed-profile -f`. A sideloaded app otherwise runs interpreted/JIT until the device's idle dexopt reaches it.
- Measured on the TV (open the sheet → its first frame): no R8, no AOT 1.64 s cold / ~0.45 s warm, median frame 28 ms; R8 + AOT 0.42 s / 0.15–0.20 s, median 13 ms, janky frames 8% → 2%, memory (PSS) 116 → 93 MB.
- **Timing a wake**: `adb logcat -s DanaPerf` (`wake/OpenTimer.kt`) logs each open from "Hey Dana" or a button to: activity created, session open, mic stream opened, sheet first frame, transcript shown, reply text shown.
- Sheet performance rules: read infinite-animation values only in draw/layer lambdas (`Canvas`, `graphicsLayer`), never in composition (`VoiceDial`, the header's live dot), and don't bind services when the sheet opens (`TapToSpeech` binds TTS on the first tap). An empty conversation is reused on the next open, not replaced with a new server session.

## JDK requirement

The Gradle daemon runs on JDK 21 (`gradle/gradle-daemon-jvm.properties`); Gradle finds it in the usual places (`/usr/lib/jvm`, SDKMAN, Android Studio's JBR…). A JDK elsewhere: add `org.gradle.java.installations.paths=/path/to/jdk21` to `~/.gradle/gradle.properties`.

## Your site

What belongs to one deployment lives in its site folder (`../site/` at the repository root, or `$DANA_SITE`; `../docs/site.md`, template `../site.example/`). The build and the scripts read it when present:

| Site file | What |
|---|---|
| `android.env` | `SERVER_URL`: the server a fresh app / kiosk / TV starts with (`BuildConfig.DEFAULT_SERVER_URL`), and where `bin/deploy-app.sh` publishes. `LAN_DNS_SERVERS`: comma-separated LAN resolvers for the TV (`docs/tv.md` → LanDns). For `bin/deploy-*.sh`: `APP_PUBLISH_TOKEN` or `APP_PUBLISH_TOKEN_CMD`, `TV_SERIAL`, `KIOSK_SERIAL`, `WATCH_IP`. |
| `tv-defaults.json` | The TV's first-run home screen (apps, Home Assistant entities, cameras, remote bindings) in `TvConfig`'s JSON shape (`BuildConfig.TV_DEFAULTS_JSON`). |
| `google-services.json` | Your Firebase project's Android config (package `io.github.ronganam.dana`), for direct push (FCM; the server's side: `server/docs/notifications.md`). `app/src/phone/google-services.json` is read when the site has none. Without either, the phone builds and works, with no direct push. |

Secrets stay out of the site, in gitignored files here: `keystore.properties` and the keystore (release signing, above); `local.properties` holds only `sdk.dir`.

