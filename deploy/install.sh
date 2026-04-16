#!/bin/bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UV_PATH="$(which uv 2>/dev/null || echo "$HOME/.local/bin/uv")"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/kiro2chat.service"

echo "Installing kiro2chat service..."
echo "  Project: $PROJECT_DIR"
echo "  uv:      $UV_PATH"

# Generate service file from template
mkdir -p "$SERVICE_DIR"
sed -e "s|__WORKING_DIR__|$PROJECT_DIR|g" \
    -e "s|__UV_PATH__|$UV_PATH|g" \
    "$PROJECT_DIR/deploy/kiro2chat.service" > "$SERVICE_FILE"

# Reload and enable
systemctl --user daemon-reload
systemctl --user enable kiro2chat

echo "Done. Usage:"
echo "  kiro2chat start     Start daemon"
echo "  kiro2chat stop      Stop daemon"
echo "  kiro2chat status    Show status"
