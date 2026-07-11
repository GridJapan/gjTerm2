#!/bin/bash
# Launcher for the gjterm-tabs MCP server.
# Registered from the repo-root .mcp.json; resolves its own directory so cwd
# does not matter. Targets gjTerm2 (never a stock iTerm2) via IT2_SUITE +
# IT2_APP_PATH, then runs the server over stdio out of the local venv.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

# Socket lives at ~/Library/Application Support/<IT2_SUITE>/private/socket
export IT2_SUITE="${IT2_SUITE:-gjTerm2}"

# Point the AppleScript authorization/cookie request at gjTerm2 specifically,
# so it works even when stock iTerm2 is also running.
if [ -z "${IT2_APP_PATH:-}" ]; then
  for candidate in \
    "/Applications/gjTerm2.app" \
    "$HOME/Applications/gjTerm2.app" \
    "$HOME"/Library/Developer/Xcode/DerivedData/iTerm2-*/Build/Products/Development/gjTerm2.app; do
    if [ -d "$candidate" ]; then
      export IT2_APP_PATH="$candidate"
      break
    fi
  done
fi

VENV_PY="$HERE/.venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "gjterm-mcp: virtualenv not found at $HERE/.venv" >&2
  echo "Set it up once with:" >&2
  echo "  python3.12 -m venv \"$HERE/.venv\" && \"$HERE/.venv/bin/pip\" install -r \"$HERE/requirements.txt\"" >&2
  exit 1
fi

exec env PYTHONPATH="$HERE" "$VENV_PY" -m gjterm_mcp.server
