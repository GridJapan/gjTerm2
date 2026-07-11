"""gjterm-tabs — an MCP server that exposes gjTerm2 tabs/sessions.

This lets an MCP client (Claude Code, etc.) discover the sessions open in
gjTerm2 and drive them: send text into any session and read its screen. An
agent running in one tab can therefore talk to, drive, and read the output of
agents/sessions in *other* tabs — the inter-tab conversation feature.

It is built on gjTerm2's iTerm2-compatible Python API (the API server that is
on by default). Connection targeting is done with two environment variables
that the launcher (run.sh) sets, so we connect to gjTerm2 and not to a stock
iTerm2 that may also be running:

  IT2_SUITE=gjTerm2    -> ~/Library/Application Support/gjTerm2/private/socket
  IT2_APP_PATH=<path>  -> the AppleScript cookie request targets gjTerm2

Transport: stdio. Launch via tools/gjterm-mcp/run.sh, or:
  IT2_SUITE=gjTerm2 python -m gjterm_mcp.server

Every tool returns a plain dict (or {"error": "..."} on failure). Give a tab a
handle with name_tab, then address it by that name (or by the session_id from
list_tabs) in the other tools. Names live on the session, not the visual tab,
so they work whether sessions are grouped as tabs or split into lone windows.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

import iterm2
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gjterm-tabs")

# Custom session variable that holds a tab's human-readable name. Stored under
# the reserved "user." namespace so it survives title/escape-sequence churn and
# is never clobbered by the shell. This is the authoritative handle that the
# resolver matches against; we also mirror it to the display name so the user
# can see it on the tab.
NAME_VAR = "user.gjname"

# One shared connection to gjTerm2, created lazily on first use and reused.
_conn: Optional["iterm2.Connection"] = None
_conn_lock = asyncio.Lock()


async def _connection() -> "iterm2.Connection":
    global _conn
    async with _conn_lock:
        if _conn is None:
            _conn = await iterm2.Connection.async_create()
        return _conn


async def _reset_connection() -> None:
    global _conn
    async with _conn_lock:
        old, _conn = _conn, None
    # Drop the library's cached App singleton. async_get_app() reuses
    # App.instance whenever it is non-None and merely calls refresh() on it — but
    # that instance is still bound to the DEAD connection, so refresh() raises and
    # a brand-new connection never gets used. The disconnect callback that would
    # clear it (invalidate_app) does not fire when the read loop is wedged after a
    # gjTerm2 force-quit, so clear it ourselves. This is the actual fix for
    # "server can't reconnect after the app restarts."
    try:
        iterm2.app.App.instance = None
    except Exception:
        pass
    # Close the dead websocket outside the lock so its background read loop
    # tears down instead of lingering.
    if old is not None:
        try:
            ws = getattr(old, "websocket", None)
            if ws is not None:
                await ws.close()
        except Exception:
            pass


async def _app() -> "iterm2.App":
    """Fetch the App model, reconnecting from scratch if the link died.

    gjTerm2 gets force-quit/rebuilt during development, which kills the cached
    connection. Retry a few times, fully dropping and recreating the connection
    between tries, so the server heals itself instead of needing a restart.
    """
    last: Optional[Exception] = None
    for _attempt in range(3):
        try:
            return await iterm2.async_get_app(await _connection())
        except Exception as exc:
            last = exc
            await _reset_connection()
    assert last is not None
    raise last


async def _title(session) -> str:
    try:
        name = await session.async_get_variable("session.name")
        if name:
            return name
    except Exception:
        pass
    return getattr(session, "name", "") or ""


async def _gjname(session) -> str:
    """The tab's assigned handle (user.gjname), or "" if unnamed."""
    try:
        return (await session.async_get_variable(NAME_VAR)) or ""
    except Exception:
        return ""


async def _apply_visible_name(session, name: str) -> None:
    """Surface (or clear) NAME on the session's visible title chrome.

    user.gjname is the authoritative handle, but the shell rewrites the plain
    session name via OSC title sequences, so a mirror there is fleeting. To
    keep the handle visible we drive surfaces the shell does not touch:

      - custom WINDOW title -> the interpolated "\\(user.gjname)". This is the
        only name-bearing chrome for a lone single-tab window (no tab bar), and
        enabling a custom window title also stops OSC-2 from overwriting it.
      - subtitle -> same interpolation, shown under the tab title when sessions
        are grouped as tabs.

    Both reference the variable rather than a literal, so they track future
    renames. Clearing (empty NAME) turns the custom window title back off and
    blanks the subtitle. Best-effort: failures are swallowed by the caller.
    """
    prof = iterm2.LocalWriteOnlyProfile()
    if name:
        prof.set_use_custom_window_title(True)
        prof.set_custom_window_title(f"\\({NAME_VAR})")
        prof.set_subtitle(f"\\({NAME_VAR})")
    else:
        prof.set_use_custom_window_title(False)
        prof.set_subtitle("")
    await session.async_set_profile_properties(prof)


def _err(where: str, exc: Exception) -> dict:
    return {"error": f"{where}: {type(exc).__name__}: {exc}"}


def _active_session_id(app) -> Optional[str]:
    try:
        win = app.current_terminal_window
        if win is None:
            return None
        tab = win.current_tab
        if tab is None:
            return None
        sess = tab.current_session
        return sess.session_id if sess is not None else None
    except Exception:
        return None


def _self_session_id() -> Optional[str]:
    """The session hosting this MCP server, if it runs inside a gjTerm2 tab.

    gjTerm2 exports ITERM_SESSION_ID (form "w0t1p0:UUID") into each session's
    shell; a Claude Code / agent process launched there inherits it, so an
    in-terminal agent can name and address its own tab. Empty when the server
    runs outside gjTerm2 (e.g. an external orchestrator) — callers fall back to
    the active session in that case.
    """
    raw = os.environ.get("ITERM_SESSION_ID", "")
    if not raw:
        return None
    return raw.rsplit(":", 1)[-1] or None


async def _all_sessions(app):
    """Every session, paired with its window/tab for context in results."""
    for window in app.terminal_windows:
        for tab in window.tabs:
            for session in tab.sessions:
                yield window, tab, session


async def _resolve(app, target: str):
    """Find one session from TARGET, which may be a name or a session_id.

    Match order (first non-ambiguous hit wins):
      1. exact session_id
      2. session_id prefix (unique)
      3. user.gjname, case-insensitive (unique)
      4. display title, case-insensitive (unique)

    Returns (session, None) on success, or (None, error_dict) if nothing
    matched or the name/prefix was ambiguous. Naming is the intended way to
    address a tab; the id paths keep older callers working.
    """
    target = (target or "").strip()
    if not target:
        return None, {"error": "empty target: pass a tab name or session_id"}

    # 1. exact id — cheapest, and unambiguous by construction.
    direct = app.get_session_by_id(target)
    if direct is not None:
        return direct, None

    lowered = target.lower()
    id_prefix, by_name, by_title = [], [], []
    for _w, _t, session in [x async for x in _all_sessions(app)]:
        sid = session.session_id
        if sid.startswith(target):
            id_prefix.append(session)
        if (await _gjname(session)).lower() == lowered:
            by_name.append(session)
        if (await _title(session)).lower() == lowered:
            by_title.append(session)

    for label, hits in (("name", by_name), ("id-prefix", id_prefix), ("title", by_title)):
        if len(hits) == 1:
            return hits[0], None
        if len(hits) > 1:
            ids = [s.session_id for s in hits]
            return None, {"error": f"ambiguous {label} {target!r} matches {ids}; use a session_id"}

    return None, {"error": f"no tab named or identified by {target!r}"}


# ------------------------------------------------------------- discovery


@mcp.tool()
async def list_tabs() -> Any:
    """List every session across all gjTerm2 windows and tabs.

    Returns {"count": N, "sessions": [{window_id, tab_id, session_id, name,
    title, is_active, is_self}]}. "name" is the handle set by name_tab (""
    if unnamed); prefer it as the target for the other tools, falling back to
    session_id. "is_self" flags the tab hosting this server, if any. This is
    how one tab discovers the others before messaging them.
    """
    try:
        app = await _app()
        active_id = _active_session_id(app)
        self_id = _self_session_id()
        out = []
        async for window, tab, session in _all_sessions(app):
            sid = session.session_id
            out.append({
                "window_id": window.window_id,
                "tab_id": tab.tab_id,
                "session_id": sid,
                "name": await _gjname(session),
                "title": await _title(session),
                "is_active": sid == active_id,
                "is_self": sid == self_id,
            })
        return {"count": len(out), "sessions": out}
    except Exception as exc:
        return _err("list_tabs", exc)


@mcp.tool()
async def whoami() -> Any:
    """Report which session hosts this server (the caller's own tab).

    Returns {in_terminal, session_id, name, title} when the server runs inside
    a gjTerm2 tab (ITERM_SESSION_ID is present); otherwise {in_terminal:
    False} — the server is an external orchestrator and "this tab" defaults to
    the active session for name_tab.
    """
    try:
        app = await _app()
        self_id = _self_session_id()
        if self_id is None:
            return {"in_terminal": False}
        session = app.get_session_by_id(self_id)
        if session is None:
            return {"in_terminal": True, "session_id": self_id,
                    "detail": "ITERM_SESSION_ID set but session not found via API"}
        return {
            "in_terminal": True,
            "session_id": self_id,
            "name": await _gjname(session),
            "title": await _title(session),
        }
    except Exception as exc:
        return _err("whoami", exc)


@mcp.tool()
async def name_tab(name: str, target: str = "") -> Any:
    """Assign NAME to a tab so other tabs can address it by that handle.

    The name is stored on the session's user.gjname variable (stable, survives
    title changes) and mirrored to the session's display name so it shows in
    the tab bar — or, when the tab is its own single-tab window, in that
    window's title bar. This is what makes naming work whether the sessions are
    grouped as tabs or split into separate windows.

    TARGET selects which tab to name; omit it to name "this tab" — the session
    hosting this server if it runs inside gjTerm2 (ITERM_SESSION_ID), else the
    currently active session. Pass an empty NAME to clear the handle.
    """
    try:
        app = await _app()
        if target.strip():
            session, err = await _resolve(app, target)
            if err is not None:
                return err
        else:
            sid = _self_session_id() or _active_session_id(app)
            session = app.get_session_by_id(sid) if sid else None
            if session is None:
                return {"error": "no target: pass a name/session_id (no self or active session found)"}

        name = name.strip()
        await session.async_set_variable(NAME_VAR, name)
        # Make the handle visible on chrome the shell can't clobber (custom
        # window title + subtitle). Best-effort: user.gjname, the authoritative
        # handle used for addressing, is already set regardless of this.
        visible = True
        try:
            await _apply_visible_name(session, name)
        except Exception:
            visible = False
        return {"ok": True, "session_id": session.session_id, "name": name,
                "cleared": name == "", "visible": visible}
    except Exception as exc:
        return _err("name_tab", exc)


@mcp.tool()
async def ping() -> Any:
    """Check that gjTerm2's API is reachable. Returns {ok, windows, sessions}."""
    try:
        app = await _app()
        sessions = sum(len(t.sessions) for w in app.terminal_windows for t in w.tabs)
        return {"ok": True, "windows": len(app.terminal_windows), "sessions": sessions}
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


# ------------------------------------------------------------- input / read


@mcp.tool()
async def send_text(target: str, text: str, newline: bool = True) -> Any:
    """Inject TEXT into a tab's terminal as if typed.

    TARGET is a tab name (from name_tab) or a session_id. Works on any session,
    including inactive/unfocused tabs and split-off windows. newline=True
    appends a carriage return so the current line is submitted (e.g. to run a
    command or send a chat message to an agent in that tab).
    """
    try:
        app = await _app()
        session, err = await _resolve(app, target)
        if err is not None:
            return err
        payload = text + ("\n" if newline else "")
        await session.async_send_text(payload)
        return {"ok": True, "session_id": session.session_id, "bytes": len(payload)}
    except Exception as exc:
        return _err("send_text", exc)


@mcp.tool()
async def get_screen_contents(target: str, max_lines: int = 100) -> Any:
    """Return the visible screen lines of a tab (latest last, up to max_lines).

    TARGET is a tab name (from name_tab) or a session_id. Use this after
    send_text to read another tab's reply/output.
    """
    try:
        app = await _app()
        session, err = await _resolve(app, target)
        if err is not None:
            return err
        contents = await session.async_get_screen_contents()
        total = contents.number_of_lines
        # Drop trailing blank lines: on a not-yet-full screen the prompt sits
        # near the top and the fixed-height screen pads the bottom with blanks,
        # which would otherwise bury a short reply under empty lines.
        last = total
        while last > 0 and not contents.line(last - 1).string.strip():
            last -= 1
        start = max(0, last - max(1, max_lines))
        lines = [contents.line(i).string for i in range(start, last)]
        return {"session_id": session.session_id, "lines": lines, "screen_lines": total}
    except Exception as exc:
        return _err("get_screen_contents", exc)


@mcp.tool()
async def get_selection(target: str) -> Any:
    """Return the text currently selected/highlighted in a tab (read-only).

    TARGET is a tab name (from name_tab) or a session_id.
    """
    try:
        app = await _app()
        session, err = await _resolve(app, target)
        if err is not None:
            return err
        selection = await session.async_get_selection()
        text = await session.async_get_selection_text(selection)
        return {"session_id": session.session_id, "text": text}
    except Exception as exc:
        return _err("get_selection", exc)


# ------------------------------------------------------------- appearance


def _parse_hex(value: str):
    """'#RRGGBB' or 'RRGGBB' -> iterm2.Color, or None if unparseable."""
    s = (value or "").strip().lstrip("#")
    if len(s) != 6:
        return None
    try:
        r, g, b = (int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None
    return iterm2.Color(r, g, b)


@mcp.tool()
async def set_colors(target: str, background: str = "", foreground: str = "") -> Any:
    """Set a tab's background and/or foreground color, so tabs can be color-coded.

    TARGET is a tab name (from name_tab) or a session_id. BACKGROUND and
    FOREGROUND are hex strings like "#1e2050" (empty = leave unchanged). The
    change is session-local and takes effect immediately. Use set_color_preset
    for a whole built-in theme instead.
    """
    try:
        app = await _app()
        session, err = await _resolve(app, target)
        if err is not None:
            return err
        prof = iterm2.LocalWriteOnlyProfile()
        applied = {}
        if background:
            c = _parse_hex(background)
            if c is None:
                return {"error": f"bad background color {background!r}; use #RRGGBB"}
            prof.set_background_color(c)
            applied["background"] = background
        if foreground:
            c = _parse_hex(foreground)
            if c is None:
                return {"error": f"bad foreground color {foreground!r}; use #RRGGBB"}
            prof.set_foreground_color(c)
            applied["foreground"] = foreground
        if not applied:
            return {"error": "pass background and/or foreground as #RRGGBB"}
        await session.async_set_profile_properties(prof)
        return {"ok": True, "session_id": session.session_id, "applied": applied}
    except Exception as exc:
        return _err("set_colors", exc)


@mcp.tool()
async def set_color_preset(target: str, preset: str) -> Any:
    """Apply a built-in color preset (whole theme) to a tab.

    TARGET is a tab name (from name_tab) or a session_id. PRESET is a preset
    name such as "Solarized Dark", "Tango Dark", "Light Background", or
    "Pastel (Dark Background)". Unknown names return an error.
    """
    try:
        app = await _app()
        session, err = await _resolve(app, target)
        if err is not None:
            return err
        try:
            colorpreset = await iterm2.ColorPreset.async_get(await _connection(), preset)
        except Exception:
            return {"error": f"no color preset named {preset!r}"}
        profile = await session.async_get_profile()
        await profile.async_set_color_preset(colorpreset)
        return {"ok": True, "session_id": session.session_id, "preset": preset}
    except Exception as exc:
        return _err("set_color_preset", exc)


# ------------------------------------------------------------- tabs / focus


@mcp.tool()
async def new_tab(window_id: str = "") -> Any:
    """Open a new tab. In WINDOW_ID if given, else in the current window.

    Returns {ok, window_id, tab_id, session_id} for the created tab.
    """
    try:
        app = await _app()
        window = None
        if window_id:
            window = next((w for w in app.terminal_windows if w.window_id == window_id), None)
            if window is None:
                return {"error": f"no window with id {window_id}"}
        else:
            window = app.current_terminal_window
            if window is None:
                return {"error": "no current window"}
        tab = await window.async_create_tab()
        session = tab.current_session or (tab.sessions[0] if tab.sessions else None)
        return {
            "ok": True,
            "window_id": window.window_id,
            "tab_id": tab.tab_id,
            "session_id": session.session_id if session else None,
        }
    except Exception as exc:
        return _err("new_tab", exc)


@mcp.tool()
async def focus_session(target: str) -> Any:
    """Select and bring a tab to the foreground (switch to its tab/window).

    TARGET is a tab name (from name_tab) or a session_id.
    """
    try:
        app = await _app()
        session, err = await _resolve(app, target)
        if err is not None:
            return err
        await session.async_activate()
        return {"ok": True, "session_id": session.session_id}
    except Exception as exc:
        return _err("focus_session", exc)


def main() -> None:
    """Console entry point: run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
