#!/usr/bin/env bash
# Build, publish, and install Dana's Android apps.
#
# Publishes the phone APK to the Dana server (so the app's
# Settings tab offers in-app updates) and installs APKs over adb where
# available. The watch cannot install APKs from the phone, so updating the
# watch app requires adb (wireless debugging) — pass --watch-ip.
#
# Usage:
#   bin/deploy-app.sh --bump 6.1.0 --notes "Bug fixes"
#   bin/deploy-app.sh                       # rebuild + republish current version
#   bin/deploy-app.sh --install-phone       # also adb-install if device connected
#   bin/deploy-app.sh --watch-ip 10.0.0.5:5555   # install wear APK on the watch
#   bin/deploy-app.sh --skip-upload --watch-ip <adb serial>   # watch over USB
#
# Env (or flags):
#   SERVER_URL          base URL of the Dana server
#   APP_PUBLISH_TOKEN   bearer token for POST /api/app/upload (or APP_PUBLISH_TOKEN_CMD:
#                       a command that prints it, run only when publishing)
#   WATCH_IP            default watch adb address (same as --watch-ip)
#
# Requirements: JDK with keytool/gradle JAVA_HOME, adb in PATH for installs,
# keystore.properties at repo root for release signing (see AGENTS.md).

set -euo pipefail
# Your devices and server: your site's android.env (site/android.env at the repository
# root, or $DANA_SITE/android.env; docs/release.md, "Your site").
SITE="${DANA_SITE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/site}"
[[ -r "$SITE/android.env" ]] && . "$SITE/android.env"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

SERVER_URL="${SERVER_URL:-}"
WATCH_IP="${WATCH_IP:-}"
PUBLISH_TOKEN="${APP_PUBLISH_TOKEN:-}"
BUMP_VERSION=""
RELEASE_NOTES=""
SKIP_BUILD=0
SKIP_UPLOAD=0
INSTALL_PHONE=0

usage() { sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bump)        BUMP_VERSION="$2"; shift 2 ;;
    --notes)       RELEASE_NOTES="$2"; shift 2 ;;
    --server)      SERVER_URL="$2"; shift 2 ;;
    --token)       PUBLISH_TOKEN="$2"; shift 2 ;;
    --watch-ip)    WATCH_IP="$2"; shift 2 ;;
    --install-phone) INSTALL_PHONE=1; shift ;;
    --skip-build)  SKIP_BUILD=1; shift ;;
    --skip-upload) SKIP_UPLOAD=1; shift ;;
    -h|--help)     usage 0 ;;
    *) echo "Unknown option: $1" >&2; usage 1 ;;
  esac
done

GRADLE_FILES=(app/build.gradle.kts wear/build.gradle.kts)

get_version_name() { grep -m1 'versionName = ' "$1" | sed 's/.*versionName = "\(.*\)".*/\1/'; }
get_version_code() { grep -m1 'versionCode = ' "$1" | sed 's/.*versionCode = \([0-9]*\).*/\1/'; }

# --- Optional version bump (applied to both phone and wear to keep them in sync) ---
if [[ -n "$BUMP_VERSION" ]]; then
  for f in "${GRADLE_FILES[@]}"; do
    current_code=$(get_version_code "$f")
    new_code=$((current_code + 1))
    sed -i -E "s/versionCode = [0-9]+/versionCode = $new_code/" "$f"
    sed -i -E "s/versionName = \"[^\"]*\"/versionName = \"$BUMP_VERSION\"/" "$f"
    echo "Bumped $f -> $BUMP_VERSION (code $new_code)"
  done
fi

PHONE_VERSION_NAME=$(get_version_name app/build.gradle.kts)
PHONE_VERSION_CODE=$(get_version_code app/build.gradle.kts)
WEAR_VERSION_NAME=$(get_version_name wear/build.gradle.kts)
WEAR_VERSION_CODE=$(get_version_code wear/build.gradle.kts)
echo "Phone: $PHONE_VERSION_NAME (code $PHONE_VERSION_CODE) | Wear: $WEAR_VERSION_NAME (code $WEAR_VERSION_CODE)"

# --- Build ---
PHONE_APK="app/build/outputs/apk/phone/release/app-phone-release.apk"
WEAR_APK="wear/build/outputs/apk/release/wear-release.apk"
FACE_APK="watchface/build/outputs/apk/release/watchface-release.apk"

if [[ $SKIP_BUILD -eq 0 ]]; then
  echo "==> Building release APKs"
  ./gradlew :app:assemblePhoneRelease :wear:assembleRelease :watchface:assembleRelease
fi
[[ -f "$PHONE_APK" ]] || { echo "ERROR: $PHONE_APK not found — build failed?" >&2; exit 1; }
[[ -f "$WEAR_APK" ]] || { echo "ERROR: $WEAR_APK not found — build failed?" >&2; exit 1; }

# --- Publish to the assistant server ---
if [[ $SKIP_UPLOAD -eq 0 ]]; then
  [[ -n "$PUBLISH_TOKEN" || -z "${APP_PUBLISH_TOKEN_CMD:-}" ]] || PUBLISH_TOKEN="$(eval "$APP_PUBLISH_TOKEN_CMD")"
  [[ -n "$SERVER_URL" ]] || { echo "ERROR: SERVER_URL not set (env, site/android.env or --server)" >&2; exit 1; }
  [[ -n "$PUBLISH_TOKEN" ]] || { echo "ERROR: APP_PUBLISH_TOKEN not set (env or --token)" >&2; exit 1; }
  echo "==> Publishing to $SERVER_URL"
  publish() {
    local artifact="$1" apk="$2" version_name="$3" version_code="$4"
    curl -fsS -H "Authorization: Bearer $PUBLISH_TOKEN" \
      -F "artifact=$artifact" \
      -F "version_name=$version_name" \
      -F "version_code=$version_code" \
      -F "release_notes=$RELEASE_NOTES" \
      -F "apk=@$apk;type=application/vnd.android.package-archive" \
      "$SERVER_URL/api/app/upload"
    echo
  }
  publish phone "$PHONE_APK" "$PHONE_VERSION_NAME" "$PHONE_VERSION_CODE"
  publish wear  "$WEAR_APK"  "$WEAR_VERSION_NAME"  "$WEAR_VERSION_CODE"
  echo "==> Published. The phone app will offer $PHONE_VERSION_NAME in Settings > App Update."
fi

# --- Phone install (optional, fast path when a device is USB-connected) ---
if [[ $INSTALL_PHONE -eq 1 ]]; then
  if adb get-state >/dev/null 2>&1; then
    echo "==> Installing phone APK via adb"
    adb install -r "$PHONE_APK"
  else
    echo "==> No adb device connected — skipping phone install (use in-app update instead)"
  fi
fi

# --- Watch install (optional; requires wireless debugging on the watch) ---
if [[ -n "$WATCH_IP" ]]; then
  echo "==> Installing wear APK on watch $WATCH_IP"
  # A serial already listed by adb (USB or a paired TLS device) needs no connect.
  if ! adb devices | awk 'NR>1{print $1}' | grep -qxF "$WATCH_IP" && ! adb connect "$WATCH_IP" >/dev/null 2>&1; then
    echo "ERROR: could not adb-connect to $WATCH_IP." >&2
    echo "Enable wireless debugging on the watch; if pairing is required run:" >&2
    echo "  adb pair <watch-ip>:<pairing-port> <pairing-code>" >&2
    exit 1
  fi
  adb -s "$WATCH_IP" install -r "$WEAR_APK" || {
    echo "ERROR: watch install failed. If it reports UPDATE_INCOMPATIBLE_SIGNATURE," >&2
    echo "uninstall the old watch app first:" >&2
    echo "  adb -s $WATCH_IP uninstall io.github.ronganam.dana" >&2
    exit 1
  }
  # Compile the baseline profile now: a sideloaded app otherwise runs
  # interpreted/JIT until the watch's idle dexopt reaches it (see AGENTS.md
  # "Watch assistant performance").
  adb -s "$WATCH_IP" shell am broadcast -a androidx.profileinstaller.action.INSTALL_PROFILE \
    -n io.github.ronganam.dana/androidx.profileinstaller.ProfileInstallReceiver >/dev/null || true
  adb -s "$WATCH_IP" shell cmd package compile -m speed-profile -f io.github.ronganam.dana >/dev/null || true
  echo "==> Watch app updated to $WEAR_VERSION_NAME"
  if [[ -f "$FACE_APK" ]]; then
    adb -s "$WATCH_IP" install -r "$FACE_APK" >/dev/null && echo "==> Watch face updated"
  fi
fi

echo "==> Done."
