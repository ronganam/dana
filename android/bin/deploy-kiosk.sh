#!/usr/bin/env bash
# Build the wall-tablet kiosk (app.ganam.assistant.kiosk, `kiosk` flavor) and
# install it over adb. It is not published to the server's update channel.
#
# Usage:
#   bin/deploy-kiosk.sh                    # release build → $ANDROID_SERIAL, else $KIOSK_SERIAL (local.env), else the only adb device
#   bin/deploy-kiosk.sh -s <serial>
#   bin/deploy-kiosk.sh --debug            # debug build (allows `run-as` and audio injection)
#   bin/deploy-kiosk.sh --set-home         # also make it the default home app
set -euo pipefail
# Your devices and server: android/local.env (gitignored; local.env.example).
[[ -r "$(dirname "${BASH_SOURCE[0]}")/../local.env" ]] && . "$(dirname "${BASH_SOURCE[0]}")/../local.env"
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SERIAL="${ANDROID_SERIAL:-${KIOSK_SERIAL:-}}"; VARIANT=Release; SET_HOME=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -s) SERIAL="$2"; shift 2 ;;
    --debug) VARIANT=Debug; shift ;;
    --set-home) SET_HOME=1; shift ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done
ADB=(adb); [[ -n "$SERIAL" ]] && ADB=(adb -s "$SERIAL")
lower=$(echo "$VARIANT" | tr '[:upper:]' '[:lower:]')

./gradlew ":app:assembleKiosk$VARIANT"
APK="app/build/outputs/apk/kiosk/$lower/app-kiosk-$lower.apk"
"${ADB[@]}" install -r -g "$APK"

PKG=app.ganam.assistant.kiosk
ACT=$PKG/$PKG.KioskActivity
# Compile ahead of time from the profile shipped in the APK (a sideloaded app
# otherwise runs interpreted/JIT until the tablet's idle dexopt gets to it).
"${ADB[@]}" shell am broadcast -a androidx.profileinstaller.action.INSTALL_PROFILE \
  -n $PKG/androidx.profileinstaller.ProfileInstallReceiver >/dev/null
"${ADB[@]}" shell cmd package compile -m speed-profile -f $PKG >/dev/null
if [[ $SET_HOME -eq 1 ]]; then
  "${ADB[@]}" shell cmd package set-home-activity "$ACT"
fi
"${ADB[@]}" shell am start -n "$ACT" >/dev/null
echo "Kiosk installed ($VARIANT) and started."
