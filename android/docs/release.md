# Releases, signing, R8

## Releases

A pushed tag `v<version>` runs `.github/workflows/release.yml` (or run it by hand with an existing tag). It fails unless the tag equals `server/VERSION` and the `versionName` of `:app` and `:wear` (bump them together; `:watchface` keeps its own version). Then:

- the server image `ghcr.io/ronganam/dana:<version>` and `:latest` (linux/amd64 + linux/arm64);
- the release APKs, built with **no site** (`DANA_SITE` is an empty folder, and no `google-services.json`): no server address, LAN DNS, cleartext permission, TV defaults or Firebase push are built in; each app starts with no server and is paired later;
- signed with the project's release key from the repository secrets `ANDROID_KEYSTORE_BASE64` (the keystore file, base64), `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD` (written into a `keystore.properties` in the runner and removed after the build; a missing secret fails the build rather than signing with a debug key). Every APK must carry the same certificate (the watch app must match the phone's, below);
- the GitHub Release with these assets and the commits since the previous tag as its notes:

| Asset | What |
|---|---|
| `dana-phone-<v>.apk` | `:app` phone flavor |
| `dana-wear-<v>.apk` | `:wear`, the watch app |
| `dana-watchface-<v>.apk` | `:watchface` |
| `dana-tablet-<v>.apk` | `:app` kiosk flavor (wall tablet) |
| `dana-tv-<v>.apk` | `:app` tv flavor |
| `SHA256SUMS` | `sha256sum` of every APK |
| `versions.json` | `{"<artifact>": {"versionName", "versionCode", "file", "sha256", "size"}}` for `phone`, `wear`, `watchface`, `tablet`, `tv` (read from each APK) |

Servers offer the phone and watch APKs of the latest release as app updates by themselves (`APP_RELEASES_REPO`; `server/docs/operations.md` → App updates): no publishing step. The tablet, TV and watch face are installed from the release by hand (`adb install`).

## Release signing & in-app updates (your own builds)

Release builds of `:app` and `:wear` are signed with a dedicated keystore (e.g. `dana-release.keystore`, gitignored) configured via `keystore.properties` at the repo root (also gitignored — copy `keystore.properties.example` and fill in real values; `keyAlias` defaults to `dana`). When `keystore.properties` is absent, release builds fall back to debug signing so plain `assembleRelease` still works.

**One-time migration note:** the release key differs from `~/.android/debug.keystore`, so the first release build must be installed with an uninstall first on both phone and watch (`adb uninstall io.github.ronganam.dana`), because `INSTALL_FAILED_UPDATE_INCOMPATIBLE` blocks cross-cert upgrades.

For apps you build and sign yourself, publish them to your server (server side: `server/core/app_updates.py`; the upload must have a higher `versionCode` than what the server offers, a release's included):

```bash
# bump versions, build signed APKs, publish to the assistant server
bin/deploy-app.sh --bump 6.1.0 --notes "What changed"

# optional adb installs (needs device/watch connectivity)
bin/deploy-app.sh --install-phone
bin/deploy-app.sh --watch-ip <watch-ip>:5555    # wear APK; uses adb wireless debugging
```

- Offered APKs live in the server's data dir (`app-updates/` next to the DB) (`/api/app/version`, `/api/app/download/{file}`; publish with `POST /api/app/upload` + `APP_PUBLISH_TOKEN`, or from GitHub Releases).
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

## Setup

- **Android SDK**: Gradle needs to know where it is — `export ANDROID_HOME=~/Android/Sdk` (Android Studio's default location on Linux; `~/Library/Android/sdk` on macOS), or a gitignored `local.properties` in `android/` with the single line `sdk.dir=/path/to/Android/Sdk` (Android Studio writes it when it opens the project). The build installs missing SDK platforms / build tools itself when the SDK licenses are accepted (`sdkmanager --licenses`).
- **JDK 21**: the Gradle daemon runs on it (`gradle/gradle-daemon-jvm.properties`). Gradle finds an installed one in the usual places (`/usr/lib/jvm`, SDKMAN, Android Studio's JBR…); a JDK elsewhere: `org.gradle.java.installations.paths=/path/to/jdk21` in `~/.gradle/gradle.properties`. With none, Gradle downloads one through the file's `toolchainUrl.*` entries (the foojay resolver in `settings.gradle.kts` does the same for compile toolchains). Regenerate the entries with `./gradlew updateDaemonJvm --jvm-version=21`.
- Any JDK can start `./gradlew`; only the daemon needs 21.

## HTTPS

Release builds talk to the server over HTTPS only (Android's default: cleartext traffic is refused). Debug builds also allow plain http (`app/src/debug/res/xml/network_security_config.xml`), for emulators and local servers (`http://10.0.2.2:8080`).

A self-hosted server on a LAN without TLS: set `ALLOW_CLEARTEXT=true` in your site's `android.env`; your release build of `:app` then sets `android:usesCleartextTraffic="true"` (the `usesCleartextTraffic` manifest placeholder in `app/build.gradle.kts`). The token travels in the clear on that network — prefer TLS (a reverse proxy with a certificate, or a VPN such as Tailscale with HTTPS). The watch's own requests (only its pairing, `docs/wear.md` → Pairing) reach the server directly and are HTTPS-only in a release build; everything else goes through the phone.

## Your site

What belongs to one deployment lives in its site folder (`../site/` at the repository root, or `$DANA_SITE`; `../docs/site.md`, template `../site.example/`). The build and the scripts read it when present:

| Site file | What |
|---|---|
| `android.env` | `SERVER_URL`: where `bin/deploy-app.sh` publishes (no build has a server built in: the phone and the watch pair, `docs/phone.md` → Pairing). `LAN_DNS_SERVERS`: comma-separated LAN resolvers for the TV (`docs/tv.md` → LanDns). `ALLOW_CLEARTEXT=true`: release builds may reach a plain-http server (above, HTTPS). `PHONE_AVD` / `WATCH_AVD`: the emulators `bin/dev-install.sh` boots. For `bin/deploy-*.sh`: `APP_PUBLISH_TOKEN` or `APP_PUBLISH_TOKEN_CMD`, `TV_SERIAL`, `KIOSK_SERIAL`, `WATCH_IP`. |
| `tv-defaults.json` | The TV's first-run home screen (apps, Home Assistant entities, cameras, remote bindings) in `TvConfig`'s JSON shape (`BuildConfig.TV_DEFAULTS_JSON`). |
| `google-services.json` | Your Firebase project's Android config (package `io.github.ronganam.dana`), for direct push (FCM; the server's side: `server/docs/notifications.md`). `app/src/phone/google-services.json` is read when the site has none. Without either, the phone builds and works, with no direct push. |

Secrets stay out of the site, in gitignored files here: `keystore.properties` and the keystore (release signing, above); `local.properties` holds only `sdk.dir`.

