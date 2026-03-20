#!/usr/bin/env bash
set -euo pipefail

SESSION_ID="${1:-019d0cee-31b5-7133-843c-10d1c562e157}"
ROOM="${HOLLYWOOD_ROOM:-main}"

echo "Starting Hollywood tail for session: $SESSION_ID"
echo "Room: $ROOM"
echo
echo "In another terminal, try:"
echo "  ./hollywood send --sender-id \"$SESSION_ID\" --text \"hello agents\""
echo

./hollywood tail --agent-id "$SESSION_ID" --room "$ROOM" --cursor --from-now
