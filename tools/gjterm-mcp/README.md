# gjterm-tabs — MCP server for gjTerm2 inter-tab messaging

An MCP server that exposes gjTerm2's open sessions to an MCP client (Claude
Code, etc.), so an agent running in one tab can **discover, message, drive, and
read** the other tabs. This is the macOS/gjTerm2 counterpart of GridWorld's
`gj-terminal` control server — but instead of a bespoke TCP JSON-RPC layer it
rides gjTerm2's built-in iTerm2-compatible **Python API**.

## Addressing tabs by name

Tabs are addressed by a **name** you assign with `name_tab`, or by the raw
`session_id`. Anywhere a tool takes `target`, either works — a name is resolved
case-insensitively (falling back to session-id / id-prefix / title match).

The name lives on the **session** (`user.gjname`), not on the visual tab, so it
survives the macOS quirk that a tab split into its own single-tab window loses
its tab-bar label: `name_tab` also drives a custom **window title** (and a
subtitle) so the handle stays visible — and addressable — in either layout.
Because these surfaces are not the plain session name, the shell's OSC title
sequences don't clobber them.

## Tools

| Tool | What it does |
|---|---|
| `list_tabs` | Every session across all windows/tabs: `window_id`, `tab_id`, `session_id`, `name`, `title`, `is_active`, `is_self`. Discovery. |
| `whoami` | Which session hosts this server (the caller's own tab), or `{in_terminal: False}` when run as an external orchestrator. |
| `name_tab(name, target="")` | Assign `name` to a tab so others can address it by that handle; omit `target` to name *this* tab (self if inside gjTerm2, else the active session). Empty `name` clears it. |
| `ping` | Reachability check: `{ok, windows, sessions}`. |
| `send_text(target, text, newline=True)` | Inject text into any tab (even background), submitting the line by default. |
| `get_screen_contents(target, max_lines=100)` | Read a tab's visible screen (trailing blanks trimmed) — use it to read another tab's reply. |
| `get_selection(target)` | The text currently selected in a tab. |
| `new_tab(window_id="")` | Open a tab; returns its ids. |
| `focus_session(target)` | Switch to a tab and raise its window. |
| `set_colors(target, background="", foreground="")` | Set a tab's background/foreground color (hex `#RRGGBB`) to color-code it. Session-local. |
| `set_color_preset(target, preset)` | Apply a built-in color preset (whole theme) such as `Solarized Dark` or `Pastel (Dark Background)`. |

Typical inter-tab conversation: `name_tab("tester", …)` once, then
`list_tabs` → `send_text("tester", "…")` → `get_screen_contents("tester")`.

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
