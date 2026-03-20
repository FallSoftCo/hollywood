#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_SRC="$SCRIPT_DIR/hollywood.service"
UNIT_DST="$HOME/.config/systemd/user/hollywood.service"

mkdir -p "$HOME/.config/systemd/user"
cp "$UNIT_SRC" "$UNIT_DST"

systemctl --user daemon-reload
systemctl --user enable --now hollywood.service

echo "Installed and started hollywood.service"
echo "Manage with: $SCRIPT_DIR/hollywoodctl"
