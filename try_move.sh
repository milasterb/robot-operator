#!/usr/bin/env bash
set -e
URL=http://127.0.0.1:8000/mcp
H=(-H "Content-Type: application/json" -H "Accept: application/json, text/event-stream")

call() {
  curl -s -X POST "$URL" "${H[@]}" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"$1\",\"arguments\":$2}}"
  echo
}

echo "--- propose ---"
OUT=$(call propose_move '{"place":"kitchen"}')
echo "$OUT"
TOKEN=$(echo "$OUT" | grep -oP 'token \K[A-Za-z0-9_-]+' | head -1)

echo "--- confirm ---"
call confirm_move "{\"token\":\"$TOKEN\"}"

echo "--- confirm again (must fail) ---"
call confirm_move "{\"token\":\"$TOKEN\"}"

echo "--- status ---"
call get_status '{}'