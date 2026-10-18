"""Itens do Wallpaper Engine baixados pela Steam (workshop 431960)."""

import json
import os
import re
from dataclasses import dataclass, field

WORKSHOP = os.path.expanduser("~/.local/share/Steam/steamapps/workshop/content/431960")
# manifesto da Steam: tamanho e data de atualização de cada item instalado
ACF = os.path.expanduser("~/.local/share/Steam/steamapps/workshop/appworkshop_431960.acf")

# o linux-wallpaperengine só roda "scene" e "video" (mesma regra do WallpaperItem.qml)
COMPATIBLE = {"scene", "video"}
TYPE_NAMES = {"scene": "Cena", "video": "Vídeo", "web": "Web", "": "Preset"}


@dataclass
class Item:
    id: str
    dir: str
    title: str
    type: str
    tags: list = field(default_factory=list)
    rating: str = ""
    description: str = ""
    preview: str = ""
    added: float = 0.0       # quando a pasta apareceu (= quando a Steam baixou)
    size: int = 0            # bytes, do manifesto da Steam
    updated: int = 0         # última atualização do autor

    @property
    def project(self):
        return os.path.join(self.dir, "project.json")

    @property
    def compatible(self):
        return self.type in COMPATIBLE

    @property
    def type_name(self):
        return TYPE_NAMES.get(self.type, self.type.capitalize())

    @property
    def animated_preview(self):
        return self.preview.lower().endswith(".gif")


def load(path):
    """Lê uma pasta do workshop; None se não tiver project.json legível."""
    try:
        with open(os.path.join(path, "project.json"), encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    preview = data.get("preview") or ""
    preview = os.path.join(path, preview) if preview else ""
    if preview and not os.path.isfile(preview):
        preview = ""
    tags = data.get("tags") or []
    return Item(
        id=os.path.basename(path),
        dir=path,
        title=str(data.get("title") or os.path.basename(path)).strip(),
        type=str(data.get("type") or "").lower(),
        tags=[str(t) for t in tags] if isinstance(tags, list) else [],
        rating=str(data.get("contentrating") or ""),
        description=str(data.get("description") or "").strip(),
        preview=preview,
        added=os.stat(path).st_mtime,
    )


def manifest(path=ACF):
    """{id: (tamanho, atualizado)} do bloco WorkshopItemsInstalled do .acf (formato VDF)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return {}
    start = text.find('"WorkshopItemsInstalled"')
    if start < 0:
        return {}
    end = text.find('"WorkshopItemDetails"', start)
    out = {}
    for m in re.finditer(r'"(\d+)"\s*\{([^{}]*)\}', text[start:end if end > 0 else None]):
        fields = dict(re.findall(r'"(\w+)"\s*"([^"]*)"', m.group(2)))
        out[m.group(1)] = (int(fields.get("size") or 0), int(fields.get("timeupdated") or 0))
    return out


def scan(root=WORKSHOP):
    try:
        names = os.listdir(root)
    except OSError:
        return []
    info = manifest()
    items = []
    for n in names:
        item = load(os.path.join(root, n))
        if item:
            item.size, item.updated = info.get(item.id, (0, 0))
            items.append(item)
    return items


def item_for_path(items, path):
    """Item do wallpaper atual (o Caelestia guarda o preview, não o project.json)."""
    if not path:
        return None
    path = os.path.realpath(path)
    for item in items:
        d = os.path.realpath(item.dir)
        if path == d or path.startswith(d + os.sep):
            return item
    return None
