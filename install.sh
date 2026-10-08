#!/usr/bin/env bash
# Installs QuickSnip to ~/.local/bin and binds it to Super+Shift+S in GNOME.
set -euo pipefail

APP_ID="io.github.quicksnip"
BIN="$HOME/.local/bin/quicksnip"
mkdir -p "$(dirname "$BIN")"
install -m 755 "$(dirname "$0")/quicksnip.py" "$BIN"

# Desktop entry + D-Bus service: GNOME needs these to show our notifications
# and to relaunch us when a notification is clicked after we've exited.
mkdir -p "$HOME/.local/share/applications" "$HOME/.local/share/dbus-1/services"
cat > "$HOME/.local/share/applications/$APP_ID.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=QuickSnip
Comment=Drag to capture a screenshot
Exec=$BIN
Icon=applets-screenshooter
Terminal=false
Categories=Utility;Graphics;
DBusActivatable=true
DESKTOP
cat > "$HOME/.local/share/dbus-1/services/$APP_ID.service" <<SERVICE
[D-BUS Service]
Name=$APP_ID
Exec=$BIN --gapplication-service
SERVICE
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

KEY_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/quicksnip/"
SCHEMA="org.gnome.settings-daemon.plugins.media-keys"
CUSTOM="$SCHEMA.custom-keybinding:$KEY_PATH"

# Append our entry to the custom keybinding list without clobbering existing ones.
current=$(gsettings get "$SCHEMA" custom-keybindings)
if [[ "$current" != *"$KEY_PATH"* ]]; then
  if [[ "$current" == "@as []" || "$current" == "[]" ]]; then
    new="['$KEY_PATH']"
  else
    new="${current%]}, '$KEY_PATH']"
  fi
  gsettings set "$SCHEMA" custom-keybindings "$new"
fi

gsettings set "$CUSTOM" name "QuickSnip"
gsettings set "$CUSTOM" command "$BIN"
gsettings set "$CUSTOM" binding "<Shift><Super>s"

# Restart any running instance so the new version is used.
pkill -f "python3 $BIN" 2>/dev/null || true

echo "Installed. Press Super+Shift+S, drag a box, release."
