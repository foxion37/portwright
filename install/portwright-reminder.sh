#!/usr/bin/env bash
PACKAGE_HOME="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PW_HOME="${PORTWRIGHT_HOME:-$PACKAGE_HOME}"
BLOCK="$PW_HOME/install/snippets/portwright.block.md"
if [ ! -f "$BLOCK" ]; then
  BLOCK="$PACKAGE_HOME/install/snippets/portwright.block.md"
fi
printf -v CODE_ROOT '%q' "$PACKAGE_HOME"
CONTENT_HOME_FLAG=""
if [ "$PW_HOME" != "$PACKAGE_HOME" ]; then
  printf -v ESCAPED_HOME '%q' "$PW_HOME"
  CONTENT_HOME_FLAG=" --home $ESCAPED_HOME"
fi

if [ -f "$BLOCK" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line//\{\{PORTWRIGHT_HOME\}\}/$CODE_ROOT}"
    printf '%s\n' "${line//\{\{CONTENT_HOME_FLAG\}\}/$CONTENT_HOME_FLAG}"
  done < "$BLOCK"
else
  echo "[portwright] reminder unavailable: $BLOCK is missing"
fi

exit 0
