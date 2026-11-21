#!/usr/bin/env bash
# Build Dana TV (app.ganam.assistant.tv, `tv` flavor) and install it on the
# TV over adb, then grant what it needs to keep itself
# working: the remote-button accessibility service, the PiP overlay, and the
# right to re-enable that service after its own updates (TCL drops it).
#
# Usage:
#   bin/deploy-tv.sh                       # release build → $ANDROID_SERIAL, else $TV_SERIAL (local.env)
#   bin/deploy-tv.sh -s 192.168.1.20:5555
#   bin/deploy-tv.sh --debug               # debug build (logs every remote key)
#   DANA_API_TOKEN=… bin/deploy-tv.sh      # also hand it the server's API token
#   bin/deploy-tv.sh --server https://dana.example.com
#
# adb auth: a TV that already trusts another adb key (e.g. Home Assistant's) needs it:
#   adb kill-server; ADB_VENDOR_KEYS=<that key> adb start-server
set -euo pipefail
# Your devices and server: android/local.env (gitignored; local.env.example).
[[ -r "$(dirname "${BASH_SOURCE[0]}")/../local.env" ]] && . "$(dirname "${BASH_SOURCE[0]}")/../local.env"
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SERIAL="${ANDROID_SERIAL:-${TV_SERIAL:-}}"; VARIANT=Release; SERVER=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -s) SERIAL="$2"; shift 2 ;;
    --debug) VARIANT=Debug; shift ;;
    --server) SERVER="$2"; shift 2 ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done
[[ "$SERIAL" == *:* ]] && adb connect "$SERIAL" >/dev/null
ADB=(adb); [[ -n "$SERIAL" ]] && ADB=(adb -s "$SERIAL")
lower=$(echo "$VARIANT" | tr '[:upper:]' '[:lower:]')
PKG=app.ganam.assistant.tv
HOME_ACT=$PKG/$PKG.TvHomeActivity
KEY_SVC=$PKG/$PKG.DanaKeyService

./gradlew ":app:assembleTv$VARIANT"
APK="app/build/outputs/apk/tv/$lower/app-tv-$lower.apk"
if ! out=$("${ADB[@]}" install -r -g "$APK" 2>&1); then
  echo "$out" >&2
  if grep -q INSTALL_FAILED_UPDATE_INCOMPATIBLE <<<"$out"; then
    echo "Installed build has another signature (debug vs release). Uninstall first:" >&2
    echo "  adb -s $SERIAL uninstall $PKG   # wipes Dana TV's settings and token" >&2
  fi
  exit 1
fi

tvsh() { "${ADB[@]}" shell "$@"; }
tvsh pm grant $PKG android.permission.WRITE_SECURE_SETTINGS
tvsh appops set $PKG SYSTEM_ALERT_WINDOW allow
# TCL's TclAppBoot blocks broadcast starts (boot / package-replaced) without this.
tvsh appops set $PKG AUTO_START allow 2>/dev/null || true

# Compile ahead of time from the profile shipped in the APK. A sideloaded app
# otherwise runs interpreted/JIT until the TV's idle dexopt gets to it, and the
# first "Hey Dana" takes over a second instead of ~0.3 s.
tvsh am broadcast -a androidx.profileinstaller.action.INSTALL_PROFILE \
  -n $PKG/androidx.profileinstaller.ProfileInstallReceiver >/dev/null
tvsh cmd package compile -m speed-profile -f $PKG >/dev/null

# Accessibility: drop ours, wait, re-add — the manager only binds on a real change.
current=$(tvsh settings get secure enabled_accessibility_services | tr -d '\r')
[[ "$current" == "null" ]] && current=""
others=$(tr ':' '\n' <<<"$current" | grep -v "^$PKG/" | grep -v '^$' | paste -sd: - || true)
tvsh settings put secure enabled_accessibility_services "${others:-\"\"}"
sleep 1
tvsh settings put secure enabled_accessibility_services "${others:+$others:}$KEY_SVC"
tvsh settings put secure accessibility_enabled 1

tvsh cmd package set-home-activity "$HOME_ACT" >/dev/null || true

extras=()
[[ -n "$SERVER" ]] && extras+=(--es server "$SERVER")
[[ -n "${DANA_API_TOKEN:-}" ]] && extras+=(--es token "$DANA_API_TOKEN")
tvsh am start -n "$HOME_ACT" "${extras[@]}" >/dev/null
echo "Dana TV installed ($VARIANT) on $SERIAL and started."
