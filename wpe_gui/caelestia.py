"""Ponte com o Caelestia: aplica pelo IPC do shell e mexe nos mesmos JSONs que ele
observa. Nada aqui inicia o linux-wallpaperengine, quem faz isso é o Background.qml."""

import json
import os
import subprocess

STATE = os.path.expanduser("~/.local/state/caelestia/wallpaper")
CURRENT = os.path.join(STATE, "path.txt")
LAST_STATIC = os.path.join(STATE, "last_static.txt")
WE_SETTINGS = os.path.join(STATE, "wallpaper-engine.json")
WE_DEFAULTS = {"volume": 0.15, "silent": False, "automute": False, "fps": 90}


def _ipc(*args):
    return subprocess.run(["qs", "-c", "caelestia", "ipc", "call", "wallpaper", *args],
                          capture_output=True, text=True, timeout=10)


def set_wallpaper(path):
    """Mesmo caminho do launcher do Caelestia: esquema de cores, hooks e o WE em si."""
    res = _ipc("set", path)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout).strip() or "o Caelestia não respondeu")


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def current():
    return _read(CURRENT)


def last_static():
    return _read(LAST_STATIC)


def settings():
    try:
        with open(WE_SETTINGS) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    return {**WE_DEFAULTS, **(data if isinstance(data, dict) else {})}


def update_settings(**changes):
    data = {**settings(), **changes}
    os.makedirs(STATE, exist_ok=True)
    tmp = WE_SETTINGS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=4)
        f.write("\n")
    os.replace(tmp, WE_SETTINGS)
