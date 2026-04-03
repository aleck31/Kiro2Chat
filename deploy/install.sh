#!/bin/bash
# Install kiro2chat as a systemd user service
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_FILE="$SCRIPT_DIR/kiro2chat.service"
DEST="$HOME/.config/systemd/user/kiro2chat.service"

mkdir -p "$(dirname "$DEST")"
cp "$SERVICE_FILE" "$DEST"
systemctl --user daemon-reload
systemctl --user enable kiro2chat
loginctl enable-linger "$(whoami)"

echo "Installed. Usage:"
echo "  systemctl --user start kiro2chat"
echo "  systemctl --user stop kiro2chat"
echo "  systemctl --user status kiro2chat"
echo "  journalctl --user -u kiro2chat -f"
