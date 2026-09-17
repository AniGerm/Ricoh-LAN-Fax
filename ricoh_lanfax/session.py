"""Find the printing user's X11 session so the backend can open a popup."""

from __future__ import annotations

import os
import pwd
from pathlib import Path


def session_env(user: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in {"DISPLAY", "WAYLAND_DISPLAY"}}
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        return env
    env["HOME"] = pw.pw_dir
    env["USER"] = user
    env["LOGNAME"] = user
    env["SHELL"] = pw.pw_shell or "/bin/bash"
    env["XDG_RUNTIME_DIR"] = f"/run/user/{pw.pw_uid}"
    env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{pw.pw_uid}/bus"
    env.setdefault("LANG", "de_DE.UTF-8")
    env.setdefault("PATH", "/usr/bin:/bin")
    xauth = Path(pw.pw_dir) / ".Xauthority"
    if xauth.exists():
        env["XAUTHORITY"] = str(xauth)
    display = None
    xdir = Path("/tmp/.X11-unix")
    for n in (1, 0, 2, 3):
        if (xdir / f"X{n}").exists():
            display = f":{n}"
            break
    if display:
        env["DISPLAY"] = display
    return env


def apply_session_env(user: str) -> dict[str, str]:
    env = session_env(user)
    os.environ.update(env)
    return env


def drop_privs(user: str) -> None:
    if os.geteuid() != 0:
        return
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        return
    os.setgid(pw.pw_gid)
    os.setuid(pw.pw_uid)
    os.environ["HOME"] = pw.pw_dir
