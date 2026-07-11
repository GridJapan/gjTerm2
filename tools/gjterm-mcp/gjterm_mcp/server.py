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


# ------------------------------------------------------------- windows


def _find_window(app, window_id: str):
    return next((w for w in app.terminal_windows if w.window_id == window_id), None)


def _locate(app, session):
    """Return (window, tab) that own SESSION, or (None, None)."""
    sid = session.session_id
    for w in app.terminal_windows:
        for t in w.tabs:
            if any(s.session_id == sid for s in t.sessions):
                return w, t
    return None, None


@mcp.tool()
async def new_window() -> Any:
    """Open a new terminal window. Returns {ok, window_id, tab_id, session_id}."""
    try:
        win = await iterm2.Window.async_create(await _connection())
        if win is None:
            return {"error": "window creation failed"}
        tab = win.current_tab or (win.tabs[0] if win.tabs else None)
        session = tab.current_session if tab else None
        return {"ok": True, "window_id": win.window_id,
                "tab_id": tab.tab_id if tab else None,
                "session_id": session.session_id if session else None}
    except Exception as exc:
        return _err("new_window", exc)


@mcp.tool()
async def close_window(window_id: str, force: bool = False) -> Any:
    """Close a whole window (and all its tabs) by window_id."""
    try:
        app = await _app()
        win = _find_window(app, window_id)
        if win is None:
            return {"error": f"no window with id {window_id}"}
        await win.async_close(force=force)
        return {"ok": True, "window_id": window_id}
    except Exception as exc:
        return _err("close_window", exc)


@mcp.tool()
async def get_window_frame(window_id: str = "") -> Any:
    """Get a window's position and size (screen points, bottom-left origin).

    Omit WINDOW_ID for the current window. Returns {window_id, x, y, width, height}.
    """
    try:
        app = await _app()
        win = _find_window(app, window_id) if window_id else app.current_terminal_window
        if win is None:
            return {"error": f"no window {window_id or '(current)'}"}
        f = await win.async_get_frame()
        return {"window_id": win.window_id, "x": f.origin.x, "y": f.origin.y,
                "width": f.size.width, "height": f.size.height}
    except Exception as exc:
        return _err("get_window_frame", exc)


@mcp.tool()
async def set_window_frame(window_id: str, x: int, y: int, width: int, height: int) -> Any:
    """Move/resize a window (screen points, bottom-left origin).

    This is the building block for tiling windows: call it per window with the
    frames you want. Use get_window_frame to read current geometry first.
    """
    try:
        app = await _app()
        win = _find_window(app, window_id)
        if win is None:
            return {"error": f"no window with id {window_id}"}
        frame = iterm2.Frame(origin=iterm2.Point(x, y), size=iterm2.Size(width, height))
        await win.async_set_frame(frame)
        return {"ok": True, "window_id": window_id,
                "frame": {"x": x, "y": y, "width": width, "height": height}}
    except Exception as exc:
        return _err("set_window_frame", exc)


@mcp.tool()
async def set_window_fullscreen(window_id: str, fullscreen: bool = True) -> Any:
    """Enter or leave fullscreen for a window."""
    try:
        app = await _app()
        win = _find_window(app, window_id)
        if win is None:
            return {"error": f"no window with id {window_id}"}
        await win.async_set_fullscreen(fullscreen)
        return {"ok": True, "window_id": window_id, "fullscreen": fullscreen}
    except Exception as exc:
        return _err("set_window_fullscreen", exc)


# ------------------------------------------------------------- arrangements


@mcp.tool()
async def list_arrangements() -> Any:
    """List saved window arrangements by name."""
    try:
        names = await iterm2.Arrangement.async_list(await _connection())
        return {"arrangements": list(names)}
    except Exception as exc:
        return _err("list_arrangements", exc)


@mcp.tool()
async def save_arrangement(name: str) -> Any:
    """Save the current windows/tabs/panes layout under NAME (overwrites)."""
    try:
        await iterm2.Arrangement.async_save(await _connection(), name)
        return {"ok": True, "name": name}
    except Exception as exc:
        return _err("save_arrangement", exc)


@mcp.tool()
async def restore_arrangement(name: str, window_id: str = "") -> Any:
    """Restore a saved arrangement by NAME (optionally into WINDOW_ID)."""
    try:
        await iterm2.Arrangement.async_restore(await _connection(), name,
                                               window_id or None)
        return {"ok": True, "name": name}
    except Exception as exc:
        return _err("restore_arrangement", exc)


# ------------------------------------------------------------- tabs / panes


@mcp.tool()
async def close_tab(target: str, force: bool = False) -> Any:
    """Close the tab that owns TARGET (a tab name or session_id)."""
    try:
        app = await _app()
        session, err = await _resolve(app, target)
        if err is not None:
            return err
        _w, tab = _locate(app, session)
        if tab is None:
            return {"error": f"no tab found for {target!r}"}
        await tab.async_close(force=force)
        return {"ok": True, "tab_id": tab.tab_id}
    except Exception as exc:
        return _err("close_tab", exc)


@mcp.tool()
async def move_tab_to_window(target: str, window_id: str = "") -> Any:
    """Reparent TARGET's tab. With WINDOW_ID, merge into that window; without,
    split it off into a new window. This is the programmatic form of dragging a
    tab between windows.
    """
    try:
        app = await _app()
        session, err = await _resolve(app, target)
        if err is not None:
            return err
        _w, tab = _locate(app, session)
        if tab is None:
            return {"error": f"no tab found for {target!r}"}
        if window_id:
            dest = _find_window(app, window_id)
            if dest is None:
                return {"error": f"no window with id {window_id}"}
            await dest.async_set_tabs(list(dest.tabs) + [tab])
            return {"ok": True, "tab_id": tab.tab_id, "merged_into": window_id}
        new_wid = await tab.async_move_to_window()
        return {"ok": True, "tab_id": tab.tab_id, "new_window_id": new_wid}
    except Exception as exc:
        return _err("move_tab_to_window", exc)


@mcp.tool()
async def set_tab_title(target: str, title: str) -> Any:
    """Set a fixed title on TARGET's tab (stable override; empty clears it)."""
    try:
        app = await _app()
        session, err = await _resolve(app, target)
        if err is not None:
            return err
        _w, tab = _locate(app, session)
        if tab is None:
            return {"error": f"no tab found for {target!r}"}
        await tab.async_set_title(title)
        return {"ok": True, "tab_id": tab.tab_id, "title": title}
    except Exception as exc:
        return _err("set_tab_title", exc)


@mcp.tool()
async def split_pane(target: str, vertical: bool = True, before: bool = False) -> Any:
    """Split TARGET into a new pane. vertical=True stacks side-by-side.

    Returns the new pane's {session_id, window_id, tab_id}.
    """
    try:
        app = await _app()
        session, err = await _resolve(app, target)
        if err is not None:
            return err
        new_session = await session.async_split_pane(vertical=vertical, before=before)
        await app.async_refresh()
        w, t = _locate(app, new_session)
        return {"ok": True, "session_id": new_session.session_id,
                "window_id": w.window_id if w else None,
                "tab_id": t.tab_id if t else None}
    except Exception as exc:
        return _err("split_pane", exc)


@mcp.tool()
async def select_pane(target: str, direction: str) -> Any:
    """Move pane focus within TARGET's tab. DIRECTION is above/below/left/right."""
    try:
        app = await _app()
        session, err = await _resolve(app, target)
        if err is not None:
            return err
        _w, tab = _locate(app, session)
        if tab is None:
            return {"error": f"no tab found for {target!r}"}
        dirs = {"above": iterm2.NavigationDirection.ABOVE,
                "below": iterm2.NavigationDirection.BELOW,
                "left": iterm2.NavigationDirection.LEFT,
                "right": iterm2.NavigationDirection.RIGHT}
        d = dirs.get(direction.lower())
        if d is None:
            return {"error": f"bad direction {direction!r}; use above/below/left/right"}
        sid = await tab.async_select_pane_in_direction(d)
        return {"ok": True, "selected_session_id": sid}
    except Exception as exc:
        return _err("select_pane", exc)


# ------------------------------------------------------------- session ops


@mcp.tool()
async def close_session(target: str, force: bool = False) -> Any:
    """Close a single session/pane (TARGET is a tab name or session_id)."""
    try:
        app = await _app()
        session, err = await _resolve(app, target)
        if err is not None:
            return err
        await session.async_close(force=force)
        return {"ok": True, "session_id": session.session_id}
    except Exception as exc:
        return _err("close_session", exc)


@mcp.tool()
async def restart_session(target: str, only_if_exited: bool = False) -> Any:
    """Restart the program in TARGET (relaunch the shell/command)."""
    try:
        app = await _app()
        session, err = await _resolve(app, target)
        if err is not None:
            return err
        await session.async_restart(only_if_exited=only_if_exited)
        return {"ok": True, "session_id": session.session_id}
    except Exception as exc:
        return _err("restart_session", exc)


@mcp.tool()
async def get_history(target: str, max_lines: int = 200) -> Any:
    """Return the last max_lines of TARGET's scrollback history (not just the
    visible screen, unlike get_screen_contents). Latest last.
    """
    try:
        app = await _app()
        session, err = await _resolve(app, target)
        if err is not None:
            return err
        info = await session.async_get_line_info()
        total = info.scrollback_buffer_height + info.mutable_area_height
        last_abs = info.overflow + total
        n = min(max(1, max_lines), total)
        first = last_abs - n
        contents = await session.async_get_contents(first, n)
        lines = [c.string for c in contents]
        # Trim trailing blanks.
        while lines and not lines[-1].strip():
            lines.pop()
        return {"session_id": session.session_id, "lines": lines,
                "history_lines": total}
    except Exception as exc:
        return _err("get_history", exc)


@mcp.tool()
async def set_grid_size(target: str, columns: int, rows: int) -> Any:
    """Resize TARGET's grid to columns x rows character cells."""
    try:
        app = await _app()
        session, err = await _resolve(app, target)
        if err is not None:
            return err
        await session.async_set_grid_size(iterm2.Size(columns, rows))
        return {"ok": True, "session_id": session.session_id,
                "columns": columns, "rows": rows}
    except Exception as exc:
        return _err("set_grid_size", exc)


@mcp.tool()
async def set_buried(target: str, buried: bool = True) -> Any:
    """Bury (hide) or unbury a session. Buried sessions leave the tab bar but
    stay alive and addressable.
    """
    try:
        app = await _app()
        session, err = await _resolve(app, target)
        if err is not None:
            return err
        await session.async_set_buried(buried)
        return {"ok": True, "session_id": session.session_id, "buried": buried}
    except Exception as exc:
        return _err("set_buried", exc)


@mcp.tool()
async def load_url(target: str, url: str) -> Any:
    """Open URL in TARGET (uses the session's semantic-history/URL handling)."""
    try:
        app = await _app()
        session, err = await _resolve(app, target)
        if err is not None:
            return err
        await session.async_load_url(url)
        return {"ok": True, "session_id": session.session_id, "url": url}
    except Exception as exc:
        return _err("load_url", exc)


@mcp.tool()
async def inject(target: str, text: str) -> Any:
    """Inject TEXT into TARGET as if the running program had emitted it (parsed
    as terminal output, including escape sequences) — distinct from send_text,
    which simulates typing.
    """
    try:
        app = await _app()
        session, err = await _resolve(app, target)
        if err is not None:
            return err
        await session.async_inject(text.encode("utf-8"))
        return {"ok": True, "session_id": session.session_id, "bytes": len(text)}
    except Exception as exc:
        return _err("inject", exc)


# --------------------------------------------------- generic escape hatches


async def _resolve_or_app(app, target: str):
    """Resolve TARGET to a session, or return (None, None) for target 'app'."""
    if target.strip().lower() == "app":
        return None, None
    return await _resolve(app, target)


@mcp.tool()
async def get_variable(target: str, name: str) -> Any:
    """Read any iTerm2 variable. TARGET is a tab name/session_id, or "app" for
    application-scope variables. NAME is like "session.name" or "user.foo".
    """
    try:
        app = await _app()
        if target.strip().lower() == "app":
            value = await app.async_get_variable(name)
            return {"scope": "app", "name": name, "value": value}
        session, err = await _resolve(app, target)
        if err is not None:
            return err
        value = await session.async_get_variable(name)
        return {"session_id": session.session_id, "name": name, "value": value}
    except Exception as exc:
        return _err("get_variable", exc)


@mcp.tool()
async def set_variable(target: str, name: str, value: Any) -> Any:
    """Write any iTerm2 variable. TARGET is a tab name/session_id, or "app".
    User variables must be under the "user." namespace.
    """
    try:
        app = await _app()
        if target.strip().lower() == "app":
            await app.async_set_variable(name, value)
            return {"ok": True, "scope": "app", "name": name}
        session, err = await _resolve(app, target)
        if err is not None:
            return err
        await session.async_set_variable(name, value)
        return {"ok": True, "session_id": session.session_id, "name": name}
    except Exception as exc:
        return _err("set_variable", exc)


@mcp.tool()
async def invoke_function(invocation: str, target: str = "") -> Any:
    """Call any registered iTerm2 function in a session's context. INVOCATION is
    like 'iterm2.get_string_value(key: "x")'. TARGET selects the session (self
    or active if omitted). This is the escape hatch for API surface not wrapped
    by a dedicated tool.
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
                return {"error": "no target session for invoke_function"}
        result = await session.async_invoke_function(invocation)
        return {"ok": True, "session_id": session.session_id, "result": result}
    except Exception as exc:
        return _err("invoke_function", exc)


@mcp.tool()
async def select_menu_item(identifier: str) -> Any:
    """Trigger any main-menu command by its identifier (e.g. "Split Vertically
    with Current Profile"). This reaches features that have no dedicated tool.
    """
    try:
        await iterm2.MainMenu.async_select_menu_item(await _connection(), identifier)
        return {"ok": True, "identifier": identifier}
    except Exception as exc:
        return _err("select_menu_item", exc)


@mcp.tool()
async def set_profile_property(target: str, property: str, value: Any) -> Any:
    """Set any profile property on TARGET's session (session-local override).

    PROPERTY is a profile setter name without the async_set_ prefix, e.g.
    "transparency", "blur", "cursor_type", "normal_font", "use_bold_font",
    "badge_text", "scrollback_lines". Any "*_color" value is parsed as #RRGGBB.
    This is the escape hatch covering the ~200 profile settings.
    """
    try:
        app = await _app()
        session, err = await _resolve(app, target)
        if err is not None:
            return err
        profile = await session.async_get_profile()
        setter = getattr(profile, f"async_set_{property}", None)
        if setter is None:
            return {"error": f"unknown profile property {property!r}"}
        val = value
        if property.endswith("_color") and isinstance(value, str):
            parsed = _parse_hex(value)
            if parsed is not None:
                val = parsed
        await setter(val)
        return {"ok": True, "session_id": session.session_id, "property": property}
    except Exception as exc:
        return _err("set_profile_property", exc)


@mcp.tool()
async def get_theme() -> Any:
    """Return the app's current theme attributes (e.g. ["dark"] / ["light"])."""
    try:
        app = await _app()
        return {"theme": list(await app.async_get_theme())}
    except Exception as exc:
        return _err("get_theme", exc)


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
