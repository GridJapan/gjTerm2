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

Every tool returns a plain dict (or {"error": "..."} on failure). Address a
session by the session_id reported by list_tabs.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import iterm2
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gjterm-tabs")

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
        _conn = None


async def _app() -> "iterm2.App":
    """Fetch the App model, transparently reconnecting once if the link died."""
    try:
        return await iterm2.async_get_app(await _connection())
    except Exception:
        await _reset_connection()
        return await iterm2.async_get_app(await _connection())


async def _title(session) -> str:
    try:
        name = await session.async_get_variable("session.name")
        if name:
            return name
    except Exception:
        pass
    return getattr(session, "name", "") or ""


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


# ------------------------------------------------------------- discovery


@mcp.tool()
async def list_tabs() -> Any:
    """List every session across all gjTerm2 windows and tabs.

    Returns {"count": N, "sessions": [{window_id, tab_id, session_id, title,
    is_active}]}. Use a session_id with the other tools. This is how one tab
    discovers the others before messaging them.
    """
    try:
        app = await _app()
        active_id = _active_session_id(app)
        out = []
        for window in app.terminal_windows:
            for tab in window.tabs:
                for session in tab.sessions:
                    sid = session.session_id
                    out.append({
                        "window_id": window.window_id,
                        "tab_id": tab.tab_id,
                        "session_id": sid,
                        "title": await _title(session),
                        "is_active": sid == active_id,
                    })
        return {"count": len(out), "sessions": out}
    except Exception as exc:
        return _err("list_tabs", exc)


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
async def send_text(session_id: str, text: str, newline: bool = True) -> Any:
    """Inject TEXT into a session's terminal as if typed.

    Works on any session, including inactive/unfocused tabs. newline=True
    appends a carriage return so the current line is submitted (e.g. to run a
    command or send a chat message to an agent in that tab).
    """
    try:
        app = await _app()
        session = app.get_session_by_id(session_id)
        if session is None:
            return {"error": f"no session with id {session_id}"}
        payload = text + ("\n" if newline else "")
        await session.async_send_text(payload)
        return {"ok": True, "session_id": session_id, "bytes": len(payload)}
    except Exception as exc:
        return _err("send_text", exc)


@mcp.tool()
async def get_screen_contents(session_id: str, max_lines: int = 100) -> Any:
    """Return the visible screen lines of a session (latest last, up to max_lines).

    Use this after send_text to read another tab's reply/output.
    """
    try:
        app = await _app()
        session = app.get_session_by_id(session_id)
        if session is None:
            return {"error": f"no session with id {session_id}"}
        contents = await session.async_get_screen_contents()
        total = contents.number_of_lines
        start = max(0, total - max(1, max_lines))
        lines = [contents.line(i).string for i in range(start, total)]
        return {"session_id": session_id, "lines": lines, "screen_lines": total}
    except Exception as exc:
        return _err("get_screen_contents", exc)


@mcp.tool()
async def get_selection(session_id: str) -> Any:
    """Return the text currently selected/highlighted in a session (read-only)."""
    try:
        app = await _app()
        session = app.get_session_by_id(session_id)
        if session is None:
            return {"error": f"no session with id {session_id}"}
        selection = await session.async_get_selection()
        text = await session.async_get_selection_text(selection)
        return {"session_id": session_id, "text": text}
    except Exception as exc:
        return _err("get_selection", exc)


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
async def focus_session(session_id: str) -> Any:
    """Select and bring a session to the foreground (switch to its tab/window)."""
    try:
        app = await _app()
        session = app.get_session_by_id(session_id)
        if session is None:
            return {"error": f"no session with id {session_id}"}
        await session.async_activate()
        return {"ok": True, "session_id": session_id}
    except Exception as exc:
        return _err("focus_session", exc)


def main() -> None:
    """Console entry point: run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
