#!/usr/bin/env python3
"""Ad-hoc experiment harness for the window/tab merge goal.

Reuses the same iterm2 connection the MCP server uses. Run subcommands to
inspect and manipulate gjTerm2 windows/tabs programmatically so we can
ground-truth what already works before touching app code.

Usage (from tools/gjterm-mcp, with the venv):
  .venv/bin/python experiment.py list
  .venv/bin/python experiment.py newwin
  .venv/bin/python experiment.py movetab <tab_id> <dest_window_id>
"""
import asyncio
import sys
import iterm2


async def dump(app):
    lines = []
    for w in app.terminal_windows:
        lines.append(f"WINDOW {w.window_id}  tabs={len(w.tabs)}")
        for t in w.tabs:
            for s in t.sessions:
                try:
                    gjname = await s.async_get_variable("user.gjname")
                except Exception:
                    gjname = None
                try:
                    sname = await s.async_get_variable("session.name")
                except Exception:
                    sname = None
                lines.append(
                    f"    tab={t.tab_id} session={s.session_id} "
                    f"gjname={gjname!r} session.name={sname!r}"
                )
    return "\n".join(lines) if lines else "(no terminal windows)"


async def main():
    conn = await iterm2.Connection.async_create()
    app = await iterm2.async_get_app(conn)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"

    if cmd == "list":
        print(await dump(app))

    elif cmd == "newwin":
        w = await iterm2.Window.async_create(conn)
        print(f"created window {w.window_id}")
        print(await dump(app))

    elif cmd == "split":
        # Move a tab out into its own new window (the reverse of a merge).
        tab_id = sys.argv[2]
        tab = None
        for w in app.terminal_windows:
            for t in w.tabs:
                if t.tab_id == tab_id:
                    tab = t
        if tab is None:
            print(f"tab {tab_id} not found")
            return
        try:
            neww = await tab.async_move_to_window()
            print(f"async_move_to_window OK -> {neww.window_id}")
        except Exception as e:
            print(f"async_move_to_window failed: {type(e).__name__}: {e}")
        await app.async_refresh()
        print(await dump(app))

    elif cmd == "movetab":
        tab_id = sys.argv[2]
        dest_win_id = sys.argv[3]
        # Find the tab and destination window.
        tab = None
        dest = None
        for w in app.terminal_windows:
            if w.window_id == dest_win_id:
                dest = w
            for t in w.tabs:
                if t.tab_id == tab_id:
                    tab = t
        if tab is None:
            print(f"tab {tab_id} not found")
            return
        if dest is None:
            print(f"dest window {dest_win_id} not found")
            return
        # Try the API's tab-move affordance.
        try:
            await dest.async_set_tabs(list(dest.tabs) + [tab])
            print("async_set_tabs OK")
        except Exception as e:
            print(f"async_set_tabs failed: {type(e).__name__}: {e}")
        await app.async_refresh()
        print(await dump(app))

    elif cmd == "frame":
        # frame <window_id> <x> <y> <w> <h>
        wid = sys.argv[2]
        x, y, w_, h = (int(v) for v in sys.argv[3:7])
        for win in app.terminal_windows:
            if win.window_id == wid:
                f = iterm2.Frame(
                    origin=iterm2.Point(x, y),
                    size=iterm2.Size(w_, h),
                )
                await win.async_set_frame(f)
                await win.async_activate()
                print(f"framed {wid}")
        await app.async_refresh()

    elif cmd == "closetab":
        tab_id = sys.argv[2]
        for w in app.terminal_windows:
            for t in w.tabs:
                if t.tab_id == tab_id:
                    await t.async_close(force=True)
                    print(f"closed tab {tab_id}")
        await app.async_refresh()
        print(await dump(app))

    elif cmd == "name":
        # Set user.gjname AND a stable custom tab title override on a session,
        # by session_id.
        sid = sys.argv[2]
        nm = sys.argv[3]
        for w in app.terminal_windows:
            for t in w.tabs:
                for s in t.sessions:
                    if s.session_id == sid:
                        await s.async_set_variable("user.gjname", nm)
                        await t.async_set_title(nm)
                        print(f"named {sid} -> {nm}")
        await app.async_refresh()
        print(await dump(app))

    elif cmd in ("preset", "bg"):
        # Change a named tab's colors. `preset <PresetName> <tabname>` applies a
        # built-in color preset; `bg <tabname> <r> <g> <b>` sets just the
        # background color (0-255).
        if cmd == "preset":
            preset_name = sys.argv[2]
            target = sys.argv[3]
        else:
            target = sys.argv[2]
        lowered = target.lower()
        found = None
        for w in app.terminal_windows:
            for t in w.tabs:
                for s in t.sessions:
                    if s.session_id == target:
                        found = s
                    gj = await s.async_get_variable("user.gjname")
                    if (gj or "").lower() == lowered:
                        found = s
        if found is None:
            print(f"no tab named {target!r}")
            return
        if cmd == "preset":
            preset = await iterm2.ColorPreset.async_get(conn, preset_name)
            profile = await found.async_get_profile()
            await profile.async_set_color_preset(preset)
            print(f"applied preset {preset_name!r} to {target!r}")
        else:
            r, g, b = (int(v) for v in sys.argv[3:6])
            prof = iterm2.LocalWriteOnlyProfile()
            prof.set_background_color(iterm2.Color(r, g, b))
            await found.async_set_profile_properties(prof)
            print(f"set background of {target!r} to rgb({r},{g},{b})")

    elif cmd in ("send", "read"):
        # Resolve TARGET by user.gjname (case-insensitive), then session_id,
        # mirroring the MCP server's _resolve name-first behavior.
        target = sys.argv[2]
        lowered = target.lower()
        found = None
        for w in app.terminal_windows:
            for t in w.tabs:
                for s in t.sessions:
                    if s.session_id == target:
                        found = s
                    gj = await s.async_get_variable("user.gjname")
                    if (gj or "").lower() == lowered:
                        found = s
        if found is None:
            print(f"no tab named {target!r}")
            return
        if cmd == "send":
            text = " ".join(sys.argv[3:])
            await found.async_send_text(text + "\n")
            print(f"sent to {target!r} (session {found.session_id}): {text!r}")
        else:  # read
            contents = await found.async_get_screen_contents()
            total = contents.number_of_lines
            last = total
            while last > 0 and not contents.line(last - 1).string.strip():
                last -= 1
            start = max(0, last - 40)
            for i in range(start, last):
                print(contents.line(i).string)

    else:
        print(f"unknown cmd {cmd!r}")


if __name__ == "__main__":
    asyncio.run(main())
