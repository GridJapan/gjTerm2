# gjterm-tabs — MCP server for gjTerm2 inter-tab messaging

An MCP server that exposes gjTerm2's open sessions to an MCP client (Claude
Code, etc.), so an agent running in one tab can **discover, message, drive, and
read** the other tabs. This is the macOS/gjTerm2 counterpart of GridWorld's
`gj-terminal` control server — but instead of a bespoke TCP JSON-RPC layer it
rides gjTerm2's built-in iTerm2-compatible **Python API**.

## Tools

| Tool | What it does |
|---|---|
| `list_tabs` | Every session across all windows/tabs: `window_id`, `tab_id`, `session_id`, `title`, `is_active`. Discovery. |
| `ping` | Reachability check: `{ok, windows, sessions}`. |
| `send_text(session_id, text, newline=True)` | Inject text into any session (even background), submitting the line by default. |
| `get_screen_contents(session_id, max_lines=100)` | Read a session's visible screen — use it to read another tab's reply. |
| `get_selection(session_id)` | The text currently selected in a session. |
| `new_tab(window_id="")` | Open a tab; returns its ids. |
| `focus_session(session_id)` | Switch to a session and raise its window. |

Typical inter-tab conversation: `list_tabs` → `send_text(B, "…")` → `get_screen_contents(B)`.

## How it targets gjTerm2 (not stock iTerm2)

The `iterm2` library picks the app by two env vars, both set by `run.sh`:

- `IT2_SUITE=gjTerm2` → the API socket `~/Library/Application Support/gjTerm2/private/socket`.
- `IT2_APP_PATH=<gjTerm2.app>` → the AppleScript authorization/cookie request targets gjTerm2.

So it connects to gjTerm2 correctly even when a stock iTerm2 is running too.

## One-time setup

gjTerm2 must have the Python API enabled (Settings → General → Magic → *Enable
Python API*; it is on by default in this build). Then create the venv:

```sh
python3.12 -m venv tools/gjterm-mcp/.venv
tools/gjterm-mcp/.venv/bin/pip install -r tools/gjterm-mcp/requirements.txt
```

(The `.venv/` is git-ignored — it is per-machine.)

## Registration

The repo-root `.mcp.json` already registers this server for Claude Code:

```json
{ "mcpServers": { "gjterm-tabs": { "command": "tools/gjterm-mcp/run.sh" } } }
```

**Restart Claude Code** (or reconnect MCP) to load it, and approve the
`gjterm-tabs` project server when prompted. The **first** time the server
connects, gjTerm2 shows an authorization prompt (“an app wants to control
gjTerm2”) — approve it once.

## Run it standalone (debug)

```sh
IT2_SUITE=gjTerm2 IT2_APP_PATH=/path/to/gjTerm2.app \
  PYTHONPATH=tools/gjterm-mcp tools/gjterm-mcp/.venv/bin/python -m gjterm_mcp.server
```
