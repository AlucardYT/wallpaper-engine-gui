"""Preferências do app (~/.config/wallpaper-engine-gui/settings.json)."""

import json
import os

from gi.repository import GLib, GObject

PATH = os.path.join(GLib.get_user_config_dir(), "wallpaper-engine-gui", "settings.json")

DEFAULTS = {
    "gif_mode": 1,                 # 0 sempre animados, 1 ao passar o mouse, 2 nunca
    "gifs_pause_unfocused": True,  # para os GIFs com a janela em segundo plano
    "app_background": True,        # wallpaper selecionado desfocado atrás do app
    "background_blur": 32,         # px
    "background_strength": 60,     # % de opacidade da imagem de fundo
    "acrylic": 55,                 # % de opacidade das superfícies (menos = mais vidro)
    "card_size": 1,                # 0 pequeno, 1 médio, 2 grande
    "start_page": 0,               # 0 Biblioteca, 1 Oficina
    "library_filter": 0,           # índice em app.FILTERS
    "library_sort": "recent",      # chave em app.SORTS
    "default_rating": 0,           # índice em steam.RATINGS
    "hidden": [],                  # desinscritos esperando a Steam apagar a pasta
}

CARD_WIDTHS = (200, 260, 340)


class Settings(GObject.Object):
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self):
        super().__init__()
        try:
            with open(PATH) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        if "gif_mode" not in data and "gifs_always" in data:  # opção antiga (liga/desliga)
            data["gif_mode"] = 0 if data["gifs_always"] else 1
        self.data = {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}

    def __getitem__(self, key):
        return self.data[key]

    def set(self, key, value):
        if self.data.get(key) == value:
            return
        self.data[key] = value
        self.save()
        self.emit("changed", key)

    def save(self):
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        tmp = PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, PATH)

    @property
    def card_width(self):
        return CARD_WIDTHS[max(0, min(2, int(self["card_size"])))]
