"""Cores do papel de parede (caelestia scheme.json), igual ao app da AULA F75."""

import json
import os

SCHEME_FILE = os.path.expanduser("~/.local/state/caelestia/scheme.json")
SCHEME_FALLBACK = {
    "surface": "141218", "surfaceContainer": "211f26", "surfaceContainerLow": "1d1b20",
    "surfaceContainerHigh": "2b2930", "onSurface": "e6e0e9", "onSurfaceVariant": "cac4d0",
    "primary": "d0bcff", "onPrimary": "381e72", "primaryContainer": "4f378b",
    "onPrimaryContainer": "eaddff", "tertiary": "efb8c8", "tertiaryContainer": "633b48",
    "secondary": "ccc2dc", "error": "f2b8b5", "onError": "601410", "outline": "938f99",
    "surfaceContainerHighest": "36343b", "outlineVariant": "49454f",
}

# variável do libadwaita -> cor do esquema Material You
THEME_VARS = {
    "accent-bg-color": "primary", "accent-fg-color": "onPrimary", "accent-color": "primary",
    "destructive-bg-color": "error", "destructive-fg-color": "onError",
    "destructive-color": "error",
    "window-bg-color": "surface", "window-fg-color": "onSurface",
    "view-bg-color": "surfaceContainerLow", "view-fg-color": "onSurface",
    "headerbar-bg-color": "surfaceContainer", "headerbar-fg-color": "onSurface",
    "headerbar-backdrop-color": "surface",
    "card-bg-color": "surfaceContainerHigh", "card-fg-color": "onSurface",
    "popover-bg-color": "surfaceContainerHigh", "popover-fg-color": "onSurface",
    "dialog-bg-color": "surfaceContainer", "dialog-fg-color": "onSurface",
    "sidebar-bg-color": "surfaceContainer", "sidebar-fg-color": "onSurface",
}

_cache = (None, SCHEME_FALLBACK)


# gtk.css gerado pelo caelestia: o GTK só lê no início, então recarregamos
GTK_CSS = os.path.expanduser("~/.config/gtk-4.0/gtk.css")


def _mtime(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def version():
    return (_mtime(SCHEME_FILE), _mtime(GTK_CSS))


def apply(old, prefs=None):
    """Troca os providers do tema. O gtk.css novo entra acima do que o GTK
    carregou no início (USER) e as cores do app por cima de tudo."""
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gdk, Gio, Gtk
    display = Gdk.Display.get_default()
    for provider in old or ():
        Gtk.StyleContext.remove_provider_for_display(display, provider)
    providers = []
    if os.path.exists(GTK_CSS):
        user = Gtk.CssProvider()
        user.load_from_file(Gio.File.new_for_path(GTK_CSS))
        providers.append((user, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1))
    own = Gtk.CssProvider()
    own.load_from_string(css(prefs))
    providers.append((own, Gtk.STYLE_PROVIDER_PRIORITY_USER + 2))
    for provider, prio in providers:
        Gtk.StyleContext.add_provider_for_display(display, provider, prio)
    return [p for p, _ in providers]


def scheme():
    global _cache
    v = version()
    if v != _cache[0]:
        try:
            with open(SCHEME_FILE) as f:
                data = json.load(f)
            colours = dict(data["colours"], mode=data.get("mode", "dark"))
        except (OSError, ValueError, KeyError, TypeError):
            colours = {}
        _cache = (v, {**SCHEME_FALLBACK, **colours})
    return _cache[1]


def rgb(name):
    h = scheme().get(name, "ffffff")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# tokens do Elo (~/Projetos/elo/linux/elo/ui/style.css + THEME_MAP do app.js) -> cor do esquema
ELO = {
    "bg": "surface", "panel": "surfaceContainerLow", "card": "surfaceContainer",
    "card-hi": "surfaceContainerHigh", "card-top": "surfaceContainerHighest",
    "text": "onSurface", "muted": "onSurfaceVariant", "outline": "outlineVariant",
    "accent": "primary", "on-accent": "onPrimary", "accent-c": "primaryContainer",
    "on-accent-c": "onPrimaryContainer", "tertiary": "tertiary", "error": "error",
}


def _rgba(name, alpha):
    r, g, b = rgb(name)
    return f"rgba({r}, {g}, {b}, {alpha:.3f})"


def css(prefs=None):
    c = scheme()
    # "acrylic" = opacidade do fundo da janela; os cartões ficam um pouco mais sólidos,
    # como no Elo (fundo 78%, cartão 88%, vidro 72%)
    glass = (prefs["acrylic"] if prefs else 78) / 100
    solid = glass + (1 - glass) * 0.45
    strength = (prefs["background_strength"] if prefs else 60) / 100
    lines = [f"  --{var}: #{c[key]};" for var, key in THEME_VARS.items() if key in c]
    lines += [f"  --e-{var}: #{c[key]};" for var, key in ELO.items() if key in c]
    lines += [
        f"  --window-bg-color: {_rgba('surface', glass)};",
        "  --view-bg-color: transparent;",
        "  --headerbar-bg-color: transparent;",
        f"  --e-glass: {_rgba('surface', glass)};",
        f"  --e-card-a: {_rgba('surfaceContainer', solid)};",
        f"  --e-card-hi-a: {_rgba('surfaceContainerHigh', solid)};",
        f"  --e-outline-a: {_rgba('outlineVariant', 0.55)};",
        f"  --e-accent-glow: {_rgba('primary', 0.30)};",
        f"  --e-glow-a: {_rgba('primary', 0.12)};",
        f"  --e-glow-b: {_rgba('tertiary', 0.10)};",
        f"  --e-accent-soft: {_rgba('primary', 0.12)};",
        f"  --e-dot-ring: {_rgba('primary', 0.20)};",
    ]
    return (":root {\n" + "\n".join(lines) + "\n}\n"
            # o Hyprland só desfoca o que está atrás quando a janela pede (backdrop-filter)
            "window.background { backdrop-filter: blur(24px); background: var(--e-glass);"
            " color: var(--e-text); font-family: Rubik, Inter, 'Google Sans', sans-serif; }\n"
            # luz ambiente com a cor do tema (igual ao .glow do Elo)
            ".glow { background-image:"
            " radial-gradient(ellipse 45% 55% at 88% 8%, var(--e-glow-a), transparent),"
            " radial-gradient(ellipse 40% 45% at 8% 100%, var(--e-glow-b), transparent); }\n"
            # o desfoque já vem pronto (widgets._blurred); aqui é só a intensidade
            f".app-backdrop {{ opacity: {strength:.2f}; }}\n"
            ".layout { padding: 14px; }\n"
            ".side-col { padding-right: 14px; }\n"
            # recolhida, a lateral flutua por cima e ganha fundo de vidro próprio
            ".settings-panel { background: var(--e-card-a); border: 1px solid var(--e-outline-a);"
            " border-radius: 22px; margin: 14px 14px 14px 0;"
            " box-shadow: -8px 0 32px rgba(0,0,0,0.35); }\n"
            ".settings-panel preferencespage, .settings-panel scrolledwindow { background: none; }\n"
                        ".side-col.floating { padding: 14px; background: var(--e-glass); border-radius: 22px; }\n"
            "overlay-split-view > widget.sidebar-pane, overlay-split-view > widget.content-pane"
            " { background: none; box-shadow: none; border: none; }\n"
            # cartões e painéis
            ".e-card { background: var(--e-card-a); border: 1px solid var(--e-outline-a);"
            " border-radius: 22px; }\n"
            ".panel { padding: 0; }\n"
            ".panel-head { padding: 20px 22px 6px; }\n"
            ".panel-title { font-size: 21px; font-weight: 600; }\n"
            ".muted { color: var(--e-muted); }\n"
            ".brand { font-size: 18px; font-weight: 700; padding: 4px 6px 0; }\n"
            ".logo { min-width: 32px; min-height: 32px; border-radius: 10px; color: var(--e-on-accent);"
            " background-image: linear-gradient(135deg, var(--e-accent), var(--e-tertiary));"
            " box-shadow: 0 6px 18px var(--e-accent-glow); }\n"
            ".sec-head { font-size: 15px; font-weight: 600; padding: 4px 6px 0; }\n"
            # cartão "Tocando agora"
            ".now-card { padding: 18px; }\n"
            ".now-preview { border-radius: 16px; box-shadow: 0 14px 30px rgba(0,0,0,0.45); }\n"
            ".now-title { font-size: 18px; font-weight: 600; }\n"
            ".status-line { color: var(--e-muted); font-size: 13px; }\n"
            ".dot { min-width: 8px; min-height: 8px; border-radius: 99px; background: var(--e-muted); }\n"
            ".dot.on { background: #5fd38d; box-shadow: 0 0 0 4px rgba(95,211,141,0.2); }\n"
            ".quick button { border-radius: 16px; padding: 12px 6px; background: var(--e-card-hi);"
            " font-size: 12px; font-weight: 500; transition: background 180ms; }\n"
            ".quick button:hover { background: var(--e-card-top); }\n"
            ".quick button image { color: var(--e-accent); -gtk-icon-size: 22px; }\n"
            ".quick button.on, .quick button:checked { background: var(--e-accent-c);"
            " color: var(--e-on-accent-c); }\n"
            ".quick button.on image, .quick button:checked image { color: var(--e-on-accent-c); }\n"
            # abas em pílula
            ".tabs { padding: 4px; border-radius: 99px; background: var(--e-card-a);"
            " border: 1px solid var(--e-outline-a); }\n"
            ".tabs button { border-radius: 99px; padding: 6px 16px; background: none;"
            " color: var(--e-muted); font-weight: 500; transition: background 250ms, color 200ms; }\n"
            ".tabs button:hover { color: var(--e-text); }\n"
            ".tabs button:checked { background: var(--e-accent-c); color: var(--e-on-accent-c); }\n"
            # botões no estilo do Elo
            "button.e-btn { border-radius: 99px; padding: 8px 16px; background: var(--e-card-hi);"
            " color: var(--e-text); font-weight: 500; box-shadow: none; }\n"
            "button.e-btn:hover { background: var(--e-card-top); }\n"
            "menubutton.e-btn > button { border-radius: 99px; padding: 4px 14px;"
            " background: var(--e-card-hi); color: var(--e-text); }\n"
            "menubutton.e-btn > button:hover, menubutton.e-btn > button:checked"
            " { background: var(--e-card-top); }\n"
            "button.e-btn.primary { background: var(--e-accent); color: var(--e-on-accent);"
            " box-shadow: 0 4px 16px var(--e-accent-glow); }\n"
            "button.e-btn.tonal { background: var(--e-accent-c); color: var(--e-on-accent-c); }\n"
            "button.e-btn.danger { color: var(--e-error); }\n"
            "button.e-btn:disabled { opacity: 0.45; }\n"
            "button.icon-btn { border-radius: 99px; min-width: 40px; min-height: 40px; padding: 0;"
            " background: none; }\n"
            "button.icon-btn:hover { background: var(--e-card-hi); }\n"
            # controle segmentado
            ".seg { padding: 3px; border-radius: 12px; background: var(--e-bg); }\n"
            ".seg button { border-radius: 9px; padding: 5px 12px; min-height: 0; background: none;"
            " color: var(--e-muted); font-size: 13px; }\n"
            ".seg button:hover { color: var(--e-text); }\n"
            ".seg > button:checked, .seg > button:checked label { background: var(--e-accent-c);"
            " color: var(--e-on-accent-c); font-weight: 600; }\n"
            ".seg > button:checked label { background: none; }\n"
            ".tabs > button:checked label, .tabs > button:checked image { color: var(--e-on-accent-c); }\n"
            ".filters entry { border-radius: 99px; background: var(--e-bg); min-height: 32px; }\n"
            ".filters dropdown > button { border-radius: 99px; background: var(--e-card-hi);"
            " padding: 4px 12px; }\n"
            ".filters dropdown > button:hover { background: var(--e-card-top); }\n"
            # grade de cards
            ".wall-grid { padding: 8px 16px 16px; }\n"
            ".wall-grid flowboxchild { padding: 0; border-radius: 16px; background: none; }\n"
            ".wall-card { border-radius: 16px; padding: 6px; background: var(--e-card-hi-a);"
            " border: 1px solid transparent; transition: background 150ms, border-color 150ms; }\n"
            ".wall-grid flowboxchild:hover .wall-card { background: var(--e-card-top); }\n"
            ".wall-grid flowboxchild:selected .wall-card { border-color: var(--e-accent); }\n"
            ".wall-card.current { background: var(--e-accent-c); color: var(--e-on-accent-c); }\n"
            ".wall-card.incompatible picture { opacity: 0.45; }\n"
            ".wall-thumb { border-radius: 11px; }\n"
            ".wall-title { font-weight: 600; padding: 6px 4px 2px; }\n"
            ".wall-meta { padding: 0 4px 4px; }\n"
            ".badge { padding: 2px 8px; border-radius: 99px; font-size: 0.8em; font-weight: 700;"
            " background: rgba(0,0,0,0.55); color: white; }\n"
            ".badge.now { background: var(--e-accent); color: var(--e-on-accent); }\n"
            ".badge.bad { background: var(--e-error); color: var(--destructive-fg-color); }\n"
            ".count { padding: 1px 8px; border-radius: 99px; background: var(--e-card-hi);"
            " font-size: 12px; font-weight: 600; }\n"
            "button.card-action { min-width: 28px; min-height: 28px; padding: 0; border-radius: 99px;"
            " background: rgba(0,0,0,0.6); color: white; }\n"
            "button.card-action:hover { background: var(--e-error); color: var(--destructive-fg-color); }\n"
            # detalhes na lateral
            ".details { padding: 16px; }\n"
            ".detail-preview { border-radius: 16px; }\n"
            ".detail-title { font-size: 18px; font-weight: 600; }\n"
            ".tag-chip { padding: 3px 10px; border-radius: 99px; background: var(--e-accent-soft);"
            " color: var(--e-accent); font-size: 12px; }\n"
            ".empty-icon { min-width: 64px; min-height: 64px; border-radius: 22px;"
            " background: var(--e-card-hi); color: var(--e-accent); -gtk-icon-size: 32px; }\n"
            ".pager { padding: 6px 12px 14px; }\n"
            ".pager button.page { min-width: 34px; border-radius: 99px; padding: 0 6px; }\n"
            ".pager button.page.current, .pager button.page.current:disabled { opacity: 1;"
            " background: var(--e-accent-c); color: var(--e-on-accent-c); }\n"
            ".pager button.page.current label { color: var(--e-on-accent-c); }\n"
            ".bulk-bar { padding: 2px 22px 4px; }\n"
            "scale trough { min-height: 6px; border-radius: 99px; background: var(--e-card-top); }\n"
            "scale highlight { background: var(--e-accent); border-radius: 99px; }\n")
