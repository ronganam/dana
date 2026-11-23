#!/bin/bash
# Install (or update) the Dana plugin into Omarchy's shell, and enable it.
#
#   extensions/omarchy/install.sh            copy, enable, put it on the bar
#   extensions/omarchy/install.sh --keys     also bind Super+D and Super+Alt+D
#   extensions/omarchy/install.sh --restart  restart the shell so an update loads
#
# A copy, not a symlink: the shell watches ~/.config/omarchy/plugins with
# inotify, which doesn't follow links. Run this again after changing the
# plugin. The shell notices the copy, but an already loaded bar widget or
# service keeps its old code until `omarchy restart shell` (--restart).
set -euo pipefail

keys=false restart=false
for arg in "$@"; do
  case $arg in
  --keys) keys=true ;;
  --restart) restart=true ;;
  *) echo "Usage: install.sh [--keys] [--restart]" >&2; exit 1 ;;
  esac
done

src=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
dest="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/dana"
env_file="${XDG_CONFIG_HOME:-$HOME/.config}/dana/omarchy.env"

command -v omarchy-shell >/dev/null || { echo "Omarchy's shell (omarchy-shell) isn't installed." >&2; exit 1; }

[[ -L $dest ]] && rm "$dest"
mkdir -p "$dest"
rsync -a --delete --exclude tests/ --exclude install.sh "$src/" "$dest/"
omarchy plugin validate "$dest" >/dev/null

if ! omarchy-plugin-list --json | jq -e '.[] | select(.id == "dana" and .enabled)' >/dev/null; then
  omarchy-shell shell rescanPlugins >/dev/null
  sleep 2
  omarchy plugin enable dana
fi

if $keys; then
  bindings="${XDG_CONFIG_HOME:-$HOME/.config}/hypr/bindings.lua"
  if ! grep -q "omarchy-shell shell toggle dana" "$bindings" 2>/dev/null; then
    cat >>"$bindings" <<'LUA'

-- Dana (extensions/omarchy): ask from anywhere, or about a part of the screen.
o.bind("SUPER + D", "Ask Dana", "omarchy-shell shell toggle dana")
o.bind("SUPER + ALT + D", "Ask Dana about the screen", "omarchy-shell shell summon dana '{\"capture\":\"region\"}'")
LUA
    echo "Added Super+D and Super+Alt+D to $bindings"
  fi
fi

if [[ ! -s $env_file ]]; then
  echo "Next: put DANA_URL and DANA_TOKEN in $env_file (chmod 600)."
fi
$restart && omarchy restart shell >/dev/null 2>&1
echo "Installed to $dest"
