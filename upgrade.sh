#!/usr/bin/env bash
# One-click upgrade for a kiro2chat deployment:
#   git pull -> uv sync -> restart the systemd user service -> health check.
#
# Safe to run from anywhere; it operates on its own repo directory.
# Works on hosts where uv/kiro-cli live in ~/.local/bin and the service runs
# as a systemd --user unit.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

export PATH="$HOME/.local/bin:$PATH"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

PORT="$(grep -E '^\s*port' "$HOME/.config/kiro2chat/config.toml" 2>/dev/null | grep -oE '[0-9]+' | head -1 || true)"
PORT="${PORT:-7860}"

echo "▶ kiro2chat upgrade @ $REPO_DIR"

echo "── git pull (--autostash) ──"
git pull --rebase --autostash

echo "── uv sync ──"
uv sync

if [ -f "$HOME/.config/systemd/user/kiro2chat.service" ]; then
    echo "── restart ──"
    uv run kiro2chat restart
else
    echo "── service not installed: install + start ──"
    uv run kiro2chat install
    uv run kiro2chat start
fi

echo "── health check ──"
sleep 5
code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/" || echo 000)"
echo "http://127.0.0.1:${PORT}/ -> ${code}"
if [ "$code" = "200" ] || [ "$code" = "307" ]; then
    echo "✅ upgrade complete"
else
    echo "⚠️  unexpected status ${code} — check: journalctl --user -u kiro2chat -n 30 --no-pager"
    exit 1
fi
