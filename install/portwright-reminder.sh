#!/usr/bin/env bash
PW_HOME="${PORTWRIGHT_HOME:-$HOME/developer/tools/portwright}"
BLOCK="$PW_HOME/install/snippets/portwright.block.md"

if [ -f "$BLOCK" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    printf '%s\n' "${line//\{\{PORTWRIGHT_HOME\}\}/$PW_HOME}"
  done < "$BLOCK"
else
  echo "[portwright] reminder unavailable: $BLOCK is missing"
fi

exit 0
