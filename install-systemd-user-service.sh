#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DST="$HOME/.config/systemd/user/hollywood.service"
DB_PATH="${HOLLYWOOD_DB:-$HOME/.hollywood/hollywood.db}"

mkdir -p "$HOME/.config/systemd/user"
cat >"$UNIT_DST" <<EOF
[Unit]
Description=Hollywood local agent chat service
After=network.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
ExecStart=$SCRIPT_DIR/hollywood serve --host 127.0.0.1 --port 8765 --db $DB_PATH
Restart=on-failure
RestartSec=1

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now hollywood.service

echo "Installed and started hollywood.service"
echo "Unit path: $UNIT_DST"
echo "Manage with: $SCRIPT_DIR/hollywoodctl"
