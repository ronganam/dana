#!/usr/bin/env bash
# The inner loop: build one debug app, install it on an EMULATOR and open it.
#
# Usage:
#   bin/dev-install.sh phone                # :app phone debug → emulator-5554 (AVD $PHONE_AVD, default "dana_phone")
#   bin/dev-install.sh watch                # :wear debug      → emulator-5556 (AVD $WATCH_AVD, default "dana_watch")
#   bin/dev-install.sh kiosk|tv -s emulator-5554
#   bin/dev-install.sh phone --boot         # boot the AVD first if it isn't running
#   bin/dev-install.sh phone --no-open      # install only
#
# Only `emulator-*` serials are accepted: real phones, watches, tablets and the
# TV get release builds through bin/deploy-*.sh. Builds one variant only, so
# Gradle never touches the other module or flavors.
set -euo pipefail
# PHONE_AVD / WATCH_AVD may come from your site's android.env (docs/release.md, "Your site").
SITE="${DANA_SITE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/site}"
[[ -r "$SITE/android.env" ]] && . "$SITE/android.env"
PHONE_AVD="${PHONE_AVD:-dana_phone}"
WATCH_AVD="${WATCH_AVD:-dana_watch}"
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

TARGET="${1:-}"; [[ -n "$TARGET" ]] || usage 1; shift
SERIAL=""; BOOT=0; OPEN=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    -s) SERIAL="$2"; shift 2 ;;
    --boot) BOOT=1; shift ;;
    --no-open) OPEN=0; shift ;;
    -h|--help) usage 0 ;;
    *) echo "Unknown option: $1" >&2; usage 1 ;;
  esac
done

case "$TARGET" in
  phone) TASK=:app:assemblePhoneDebug; APK=app/build/outputs/apk/phone/debug/app-phone-debug.apk
         ACT=io.github.ronganam.dana/.MainActivity; AVD=$PHONE_AVD; DEFAULT_SERIAL=emulator-5554 ;;
  kiosk) TASK=:app:assembleKioskDebug; APK=app/build/outputs/apk/kiosk/debug/app-kiosk-debug.apk
         ACT=io.github.ronganam.dana.kiosk/.KioskActivity; AVD=$PHONE_AVD; DEFAULT_SERIAL=emulator-5554 ;;
  tv)    TASK=:app:assembleTvDebug; APK=app/build/outputs/apk/tv/debug/app-tv-debug.apk
         ACT=io.github.ronganam.dana.tv/.TvHomeActivity; AVD=$PHONE_AVD; DEFAULT_SERIAL=emulator-5554 ;;
  watch) TASK=:wear:assembleDebug; APK=wear/build/outputs/apk/debug/wear-debug.apk
         ACT=io.github.ronganam.dana/io.github.ronganam.dana.wear.MainActivity; AVD=$WATCH_AVD; DEFAULT_SERIAL=emulator-5556 ;;
  -h|--help) usage 0 ;;
  *) echo "Unknown target: $TARGET (phone, watch, kiosk, tv)" >&2; usage 1 ;;
esac
SERIAL="${SERIAL:-$DEFAULT_SERIAL}"
if [[ "$SERIAL" != emulator-* ]]; then
  echo "Refusing $SERIAL: dev-install only targets emulators (emulator-*). Use bin/deploy-*.sh for devices." >&2
  exit 1
fi
ADB=(adb -s "$SERIAL")

attached() { adb devices | awk 'NR>1 && $2=="device"{print $1}' | grep -qxF "$SERIAL"; }
if ! attached; then
  if [[ $BOOT -eq 0 ]]; then
    echo "$SERIAL is not attached. Boot it: bin/dev-install.sh $TARGET --boot" >&2
    echo "  (or: \$ANDROID_HOME/emulator/emulator -avd $AVD -port ${SERIAL#emulator-} &)" >&2
    exit 1
  fi
  EMU="${ANDROID_HOME:-$HOME/Android/Sdk}/emulator/emulator"
  echo "==> Booting AVD $AVD as $SERIAL"
  # The host GPU (the workstation's NVIDIA card) renders far faster than SwiftShader; no boot animation, no audio.
  nohup "$EMU" -avd "$AVD" -port "${SERIAL#emulator-}" -gpu host -no-boot-anim -no-audio -no-snapshot-save >/dev/null 2>&1 &
fi

# Build while the emulator boots.
echo "==> ./gradlew $TASK"
./gradlew -q "$TASK"

"${ADB[@]}" wait-for-device
until [[ "$("${ADB[@]}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == 1 ]]; do sleep 1; done
echo "==> Installing on $SERIAL"
"${ADB[@]}" install -r -t "$APK" >/dev/null
if [[ $OPEN -eq 1 ]]; then
  "${ADB[@]}" shell am start -n "$ACT" >/dev/null
  echo "==> Opened $ACT"
fi
