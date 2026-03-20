#!/usr/bin/env bash
set -euo pipefail

export HOLLYWOOD_AUTO_ATTACH=1
export HOLLYWOOD_URL="${HOLLYWOOD_URL:-http://127.0.0.1:8765}"
export HOLLYWOOD_ROOM="${HOLLYWOOD_ROOM:-main}"
export HOLLYWOOD_ATTENTION_MODE="${HOLLYWOOD_ATTENTION_MODE:-focused}"

echo "Hollywood environment configured:"
echo "  HOLLYWOOD_AUTO_ATTACH=$HOLLYWOOD_AUTO_ATTACH"
echo "  HOLLYWOOD_URL=$HOLLYWOOD_URL"
echo "  HOLLYWOOD_ROOM=$HOLLYWOOD_ROOM"
echo "  HOLLYWOOD_ATTENTION_MODE=$HOLLYWOOD_ATTENTION_MODE"
echo
echo "Next step:"
echo "  cd /path/to/losangelex/codex-rs"
echo "  cargo run --bin codex"
