"""Registra travadas da interface em ~/.cache/wallpaper-engine-gui/stalls.log.

Uma thread confere se o loop principal do GTK continua batendo; se ficar mais de
LIMIT segundos parado, salva a pilha da thread principal (onde ele travou)."""

import os
import sys
import threading
import time
import traceback

from gi.repository import GLib

LIMIT = 1.5
LOG = os.path.join(GLib.get_user_cache_dir(), "wallpaper-engine-gui", "stalls.log")


def start():
    beat = [time.monotonic()]
    main = threading.main_thread().ident

    def tick():
        beat[0] = time.monotonic()
        return True
    GLib.timeout_add(100, tick)

    def watch():
        logged = 0.0
        while True:
            time.sleep(0.25)
            lag = time.monotonic() - beat[0]
            if lag < LIMIT or beat[0] == logged:
                continue
            logged = beat[0]
            frame = sys._current_frames().get(main)
            stack = "".join(traceback.format_stack(frame, limit=12)) if frame else "?\n"
            try:
                os.makedirs(os.path.dirname(LOG), exist_ok=True)
                with open(LOG, "a") as f:
                    f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} travou {lag:.1f}s+\n{stack}")
            except OSError:
                pass
    threading.Thread(target=watch, daemon=True, name="watchdog").start()
