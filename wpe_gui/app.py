"""Janela do app: biblioteca local, aba Oficina, som e configurações.

Só escolhe e configura; quem roda o linux-wallpaperengine é o Caelestia."""

import gc
import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from . import caelestia, library, steam, theme  # noqa: E402
from .settings import Settings  # noqa: E402
from .widgets import (Animator, Backdrop, FixedPicture, GifDriver, Glow, PreviewCard,  # noqa: E402
                      Thumb, cache_size, clear_cache, load_thumb, trim_memory)
from .workshop import (Account, RemoteDetails, Workshop, ensure_login, in_thread,  # noqa: E402
                       plain, steam_running)

APP_ID = "io.github.alucky.WallpaperEngine"
FILTERS = [("compat", "Compatíveis"), ("scene", "Cenas"), ("video", "Vídeos"),
           ("bad", "Incompatíveis"), ("all", "Todos")]
# ordem da Biblioteca: (chave, rótulo, função que dá a chave de ordenação, decrescente?)
SORTS = [
    ("recent", "Inscritos recentemente", lambda i: i.added, True),
    ("oldest", "Inscritos há mais tempo", lambda i: i.added, False),
    ("az", "Nome (A–Z)", lambda i: i.title.casefold(), False),
    ("za", "Nome (Z–A)", lambda i: i.title.casefold(), True),
    ("big", "Maior tamanho", lambda i: i.size, True),
    ("small", "Menor tamanho", lambda i: i.size, False),
    ("updated", "Atualizados pelo autor", lambda i: i.updated, True),
    ("type", "Tipo (cenas, vídeos…)", lambda i: (i.type_name, i.title.casefold()), False),
]
ALL_TAGS = "Todas as tags"


def _mtime(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


class Card(PreviewCard):
    def __init__(self, window, item):
        super().__init__(item.title)
        self.item = item
        # texto pra busca, montado uma vez só
        self.haystack = " ".join([item.title, item.id, *item.tags]).casefold()
        if item.compatible:
            self.top_end.append(self.badge(item.type_name))
        else:
            self.top_end.append(self.badge(f"{item.type_name} · não roda", "bad"))
            self.box.add_css_class("incompatible")
            trash = Gtk.Button(icon_name="user-trash-symbolic", css_classes=["card-action"],
                               tooltip_text="Cancelar inscrição")
            trash.connect("clicked", lambda *_: window.unsubscribe([(item.id, item.title)]))
            self.bottom_end.append(trash)
        self.now = self.badge("Atual", "now")
        self.now.set_visible(False)
        self.top_start.append(self.now)
        if item.preview:
            self.set_image(item.preview)

    def set_current(self, current):
        self.now.set_visible(current)
        (self.box.add_css_class if current else self.box.remove_css_class)("current")


class Details(Gtk.ScrolledWindow):
    def __init__(self, window):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.window = window
        self.item = None

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, css_classes=["details"])
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True,
                                   overflow=Gtk.Overflow.HIDDEN, css_classes=["detail-preview"])
        self.picture.set_size_request(-1, 180)
        self.animator = Animator(self.picture)
        box.append(self.picture)

        self.title = Gtk.Label(xalign=0, wrap=True, selectable=True, css_classes=["detail-title"])
        box.append(self.title)
        self.info = Gtk.Label(xalign=0, wrap=True, css_classes=["muted"])
        box.append(self.info)

        self.apply_btn = Gtk.Button(label="Aplicar", css_classes=["e-btn", "primary"])
        self.apply_btn.connect("clicked", lambda *_: self._main())
        box.append(self.apply_btn)

        links = Gtk.Box(spacing=8, homogeneous=True)
        web = Gtk.Button(label="Abrir na Steam", css_classes=["e-btn"])
        web.connect("clicked", lambda *_: self._open(steam.ITEM_URL.format(self.item.id)))
        folder = Gtk.Button(label="Abrir pasta", css_classes=["e-btn"])
        folder.connect("clicked", lambda *_: self._open(
            Gio.File.new_for_path(self.item.dir).get_uri()))
        links.append(web)
        links.append(folder)
        box.append(links)
        self.unsub_btn = Gtk.Button(label="Cancelar inscrição", css_classes=["e-btn", "danger"])
        self.unsub_btn.connect("clicked", lambda *_: window.unsubscribe(
            [(self.item.id, self.item.title)]))
        box.append(self.unsub_btn)

        self.tags = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=6,
                                row_spacing=6, max_children_per_line=20)
        box.append(self.tags)
        self.description = Gtk.Label(xalign=0, yalign=0, wrap=True, selectable=True)
        box.append(self.description)

        self.set_child(box)

    def show(self, item, current):
        self.item = item
        if not item:
            self.animator.stop(None)
            return
        self.get_vadjustment().set_value(0)
        self.title.set_label(item.title)
        bits = [item.type_name, item.rating, steam.size(item.size) if item.size else None,
                f"ID {item.id}"]
        self.info.set_label(" · ".join(b for b in bits if b))
        self.refresh_current(current)

        while child := self.tags.get_first_child():
            self.tags.remove(child)
        for tag in item.tags:
            self.tags.append(Gtk.Label(label=tag, css_classes=["tag-chip"]))
        self.tags.set_visible(bool(item.tags))
        self.description.set_label(plain(item.description))
        self.description.set_visible(bool(item.description))

        self.animator.stop(None)
        self.picture.set_paintable(None)
        if item.preview:
            load_thumb(item.preview, lambda t, it=item: self.item is it and
                       not self.animator.playing and self.picture.set_paintable(t))
            self.animator.play(item.preview)

    def refresh_current(self, current):
        item = self.item
        if not item:
            return
        busy = item.id in self.window.busy_ids
        self.apply_btn.remove_css_class("danger")
        self.apply_btn.add_css_class("primary")
        self.unsub_btn.set_visible(item.compatible)
        self.unsub_btn.set_sensitive(not busy)
        if not item.compatible:
            # incompatível: a ação principal vira tirar ele da conta
            self.apply_btn.set_label("Aguarde…" if busy else "Cancelar inscrição")
            self.apply_btn.set_tooltip_text("O linux-wallpaperengine não roda esse tipo")
            self.apply_btn.remove_css_class("primary")
            self.apply_btn.add_css_class("danger")
            self.apply_btn.set_sensitive(not busy)
            return
        self.apply_btn.set_label("Aplicado" if current else "Aplicar")
        self.apply_btn.set_tooltip_text(None)
        self.apply_btn.set_sensitive(not current)

    def _main(self):
        if self.item.compatible:
            self.window.apply(self.item)
        else:
            self.window.unsubscribe([(self.item.id, self.item.title)])

    def _open(self, uri):
        Gtk.UriLauncher.new(uri).launch(self.window, None, None, None)


class SettingsPanel(Gtk.Box):
    """Configurações numa gaveta lateral (à direita), por cima do conteúdo."""

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["settings-panel"])
        self.window = window
        head = Gtk.Box(spacing=8, margin_top=14, margin_bottom=4, margin_start=20, margin_end=12)
        head.append(Gtk.Label(label="Configurações", xalign=0, hexpand=True,
                              css_classes=["panel-title"]))
        close = Gtk.Button(icon_name="window-close-symbolic", css_classes=["icon-btn"],
                           tooltip_text="Fechar", valign=Gtk.Align.CENTER)
        close.connect("clicked", lambda *_: window.close_settings())
        head.append(close)
        self.append(head)
        page = Adw.PreferencesPage(vexpand=True)
        self.append(page)

        look = Adw.PreferencesGroup(title="Aparência")
        page.add(look)
        look.add(self._switch("app_background", "Wallpaper como fundo do app",
                              "Mostra o wallpaper instalado selecionado, desfocado, atrás da janela"))
        look.add(self._spin("background_blur", "Desfoque do fundo", 0, 80, 2, "px"))
        look.add(self._spin("background_strength", "Intensidade do fundo", 10, 100, 5, "%"))
        look.add(self._spin("acrylic", "Opacidade do vidro",
                            0, 100, 5, "%", "Menos = mais transparente"))
        look.add(self._combo("card_size", "Tamanho dos cards", ["Pequeno", "Médio", "Grande"]))

        sound = Adw.PreferencesGroup(title="Som do wallpaper",
                                     description="Mudar isso reinicia o wallpaper que está rodando")
        page.add(sound)
        automute = Adw.SwitchRow(
            title="Calar quando outro app tocar som",
            subtitle="Padrão do linux-wallpaperengine. Com Discord ou navegador aberto, "
                     "o wallpaper fica sempre mudo",
            active=bool(caelestia.settings()["automute"]))
        automute.connect("notify::active",
                         lambda r, _: caelestia.update_settings(automute=r.get_active()))
        sound.add(automute)

        gifs = Adw.PreferencesGroup(
            title="GIFs", description="“Sempre” anima os cards que estão na tela (usa mais CPU); "
                                      "“Nunca” deixa tudo parado, inclusive ao passar o mouse")
        page.add(gifs)
        gifs.add(self._combo("gif_mode", "Animação", ["Sempre", "Ao passar o mouse", "Nunca"]))
        gifs.add(self._switch("gifs_pause_unfocused", "Pausar com a janela em segundo plano"))

        general = Adw.PreferencesGroup(title="Geral")
        page.add(general)
        general.add(self._combo("start_page", "Abrir na aba", ["Biblioteca", "Oficina"]))
        general.add(self._combo("library_filter", "Filtro inicial da Biblioteca",
                                [label for _, label in FILTERS]))
        general.add(self._combo("default_rating", "Classificação padrão na Oficina",
                                [label for _, label in steam.RATINGS]))

        account = Adw.PreferencesGroup(title="Conta da Steam")
        page.add(account)
        self.account_row = Adw.ActionRow()
        self.account_btn = Gtk.Button(valign=Gtk.Align.CENTER)
        self.account_btn.connect("clicked", lambda *_: self._account_action())
        self.account_row.add_suffix(self.account_btn)
        account.add(self.account_row)
        window.account.connect("changed", lambda *_: self._account_refresh())
        self._account_refresh()

        cache = Adw.PreferencesGroup(title="Cache")
        page.add(cache)
        self.cache_row = Adw.ActionRow(title="Previews e miniaturas salvas")
        clear = Gtk.Button(label="Limpar", valign=Gtk.Align.CENTER)
        clear.connect("clicked", lambda *_: self._clear_cache())
        self.cache_row.add_suffix(clear)
        cache.add(self.cache_row)

    def refresh_cache(self):
        in_thread(cache_size, lambda n, _e: self.cache_row.set_subtitle(steam.size(n or 0)))

    def _switch(self, key, title, subtitle=None):
        row = Adw.SwitchRow(title=title, subtitle=subtitle or "", active=self.window.settings[key])
        row.connect("notify::active", lambda r, _: self.window.settings.set(key, r.get_active()))
        return row

    def _spin(self, key, title, lo, hi, step, unit, subtitle=None):
        row = Adw.SpinRow.new_with_range(lo, hi, step)
        row.set_title(f"{title} ({unit})")
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_value(self.window.settings[key])
        row.connect("notify::value", lambda r, _: self.window.settings.set(key, int(r.get_value())))
        return row

    def _combo(self, key, title, options, subtitle=None):
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new(options), subtitle=subtitle or "")
        row.set_selected(self.window.settings[key])
        row.connect("notify::selected", lambda r, _: self.window.settings.set(key, r.get_selected()))
        return row

    def _account_refresh(self):
        acc = self.window.account
        if acc.logged_in:
            self.account_row.set_title(acc.name or "Conectado")
            self.account_row.set_subtitle("Pode se inscrever e cancelar inscrições pelo app")
            self.account_btn.set_label("Sair")
        else:
            self.account_row.set_title("Não conectado")
            self.account_row.set_subtitle("Precisa entrar pra se inscrever")
            self.account_btn.set_label("Entrar")

    def _account_action(self):
        acc = self.window.account
        if acc.logged_in:
            acc.logout()
        else:
            ensure_login(acc, self.window, lambda _ok: None)

    def _clear_cache(self):
        clear_cache()
        self.cache_row.set_subtitle(steam.size(cache_size()))


class NowPlaying(Gtk.Box):
    """Cartão "Tocando agora" da coluna lateral (o cartão do celular no Elo): o wallpaper
    atual, se está rodando, o volume e as ações rápidas."""

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                         css_classes=["e-card", "now-card"])
        self.window = window
        self.key = None
        self._volume_source = None

        row = Gtk.Box(spacing=16)
        self.picture = FixedPicture(136, 77, overflow=Gtk.Overflow.HIDDEN,
                                    css_classes=["now-preview"], valign=Gtk.Align.CENTER)
        self.animator = Animator(self.picture)
        row.append(self.picture)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER,
                       hexpand=True)
        self.title = Gtk.Label(xalign=0, ellipsize=3, max_width_chars=1, lines=2, wrap=True,
                               css_classes=["now-title"])
        info.append(self.title)
        status = Gtk.Box(spacing=6, css_classes=["status-line"])
        self.dot = Gtk.Box(css_classes=["dot"], valign=Gtk.Align.CENTER)
        status.append(self.dot)
        self.status = Gtk.Label(xalign=0, ellipsize=3, max_width_chars=1, hexpand=True)
        status.append(self.status)
        info.append(status)
        row.append(info)
        self.append(row)

        # volume do wallpaper (o Caelestia reinicia o WE a cada mudança, então espera soltar)
        s = caelestia.settings()
        vol = Gtk.Box(spacing=8)
        self.vol_icon = Gtk.Image(css_classes=["muted"])
        vol.append(self.vol_icon)
        self.volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.volume.set_hexpand(True)
        self.volume.set_value(round(float(s["volume"]) * 100))
        self.volume.connect("value-changed", self._on_volume)
        vol.append(self.volume)
        self.vol_label = Gtk.Label(width_chars=4, xalign=1, css_classes=["muted", "numeric"])
        vol.append(self.vol_label)
        self.append(vol)

        quick = Gtk.Box(spacing=8, homogeneous=True, css_classes=["quick"])
        self.static_btn = self._quick(quick, "image-x-generic-symbolic", "Estático",
                                      window.restore_static)
        self._quick(quick, "media-playlist-shuffle-symbolic", "Aleatório", window.apply_random)
        self.mute_btn = self._quick(quick, "audio-volume-muted-symbolic", "Mudo", self._toggle_mute)
        self.append(quick)

        self.silent = bool(s["silent"])
        self._sound_ui()

    @staticmethod
    def _quick(box, icon, label, action):
        btn = Gtk.Button()
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.append(Gtk.Image(icon_name=icon))
        inner.append(Gtk.Label(label=label))
        btn.set_child(inner)
        btn.connect("clicked", lambda *_: action())
        box.append(btn)
        return btn

    def update(self):
        """Chamado quando o wallpaper atual muda (path.txt)."""
        w = self.window
        card = w.cards.get(w.current)
        path = caelestia.current()
        if card:
            key, image, title = card.item.id, card.item.preview, card.item.title
            running = _we_running()
            monitors = _monitor_names()
            self.status.set_label((f"Rodando em {monitors}" if running else "Parado") +
                                  f" · {card.item.type_name}")
            self.dot.set_css_classes(["dot", "on"] if running else ["dot"])
        else:
            key, image = path, path
            title = os.path.splitext(os.path.basename(path))[0] if path else "Nenhum wallpaper"
            self.status.set_label("Wallpaper estático")
            self.dot.set_css_classes(["dot"])
        self.title.set_label(title)
        self.title.set_tooltip_text(title)
        self.static_btn.set_sensitive(card is not None)
        if key == self.key:
            return
        self.key = key
        _we_pid[1] = 0  # wallpaper trocou: procura o processo de novo no próximo tique
        self.animator.stop(None)
        self.picture.set_paintable(None)
        if image and os.path.exists(image):
            load_thumb(image, lambda t, k=key: self.key == k and not self.animator.playing
                       and self.picture.set_paintable(t))
            self.animator.play(image)

    # -- som
    def _sound_ui(self):
        muted = self.silent or self.volume.get_value() == 0
        self.vol_icon.set_from_icon_name("audio-volume-muted-symbolic" if muted
                                         else "audio-volume-high-symbolic")
        self.vol_label.set_label("mudo" if self.silent else f"{int(self.volume.get_value())}%")
        self.volume.set_sensitive(not self.silent)
        (self.mute_btn.add_css_class if self.silent else self.mute_btn.remove_css_class)("on")

    def _on_volume(self, scale):
        self._sound_ui()
        if self._volume_source:
            GLib.source_remove(self._volume_source)

        def save():
            self._volume_source = None
            caelestia.update_settings(volume=round(scale.get_value()) / 100)
            return False
        self._volume_source = GLib.timeout_add(400, save)

    def _toggle_mute(self):
        self.silent = not self.silent
        caelestia.update_settings(silent=self.silent)
        self._sound_ui()


_we_pid = [None, 0.0]


def _we_running():
    """Procura o processo do WE no /proc. Com o PID achado, só confere se ele continua
    vivo; a varredura completa roda no máximo a cada 5s."""
    pid, last = _we_pid
    if pid and os.path.exists(f"/proc/{pid}"):
        return True
    now = GLib.get_monotonic_time() / 1e6
    if now - last < 5:
        return False
    _we_pid[1] = now
    _we_pid[0] = None
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            with open(f"/proc/{p}/comm") as f:
                if f.read().startswith("linux-wallpaper"):
                    _we_pid[0] = p
                    return True
        except OSError:
            pass
    return False


def _monitor_names():
    from gi.repository import Gdk
    mons = Gdk.Display.get_default().get_monitors()
    names = [mons.get_item(i).get_connector() for i in range(mons.get_n_items())]
    return ", ".join(n for n in names if n) or "tela"


class SidePanel(Gtk.Box):
    """Coluna da esquerda: marca, "Tocando agora" e os detalhes do que está selecionado
    (da Biblioteca ou da Oficina, cada aba lembra a sua seleção)."""

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14, css_classes=["side-col"])
        self.window = window

        brand = Gtk.Box(spacing=10, css_classes=["brand"])
        logo = Gtk.Image(icon_name="preferences-desktop-wallpaper-symbolic", css_classes=["logo"],
                         pixel_size=20)
        brand.append(logo)
        brand.append(Gtk.Label(label="Wallpaper Engine"))
        self.append(Gtk.WindowHandle(child=brand))

        self.now = NowPlaying(window)
        self.append(self.now)

        self.append(Gtk.Label(label="Detalhes", xalign=0, css_classes=["sec-head"]))
        self.local = Details(window)
        self.remote = RemoteDetails(window.workshop)
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.CENTER,
                        margin_start=24, margin_end=24)
        empty.append(Gtk.Image(icon_name="preferences-desktop-wallpaper-symbolic",
                               css_classes=["empty-icon"], halign=Gtk.Align.CENTER))
        empty.append(Gtk.Label(label="Escolha um wallpaper", css_classes=["detail-title"]))
        self.empty_hint = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER,
                                    css_classes=["muted"])
        empty.append(self.empty_hint)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True,
                               css_classes=["e-card"], overflow=Gtk.Overflow.HIDDEN)
        self.stack.add_named(empty, "empty")
        self.stack.add_named(self.local, "local")
        self.stack.add_named(self.remote, "remote")
        self.append(self.stack)

    def _page(self):
        return self.window.stack.get_visible_child_name()

    def show_local(self, item, current=False):
        self.local.show(item, current)
        if self._page() == "library":
            self.sync()
        if item:
            self.window.reveal_side()

    def show_remote(self, result):
        self.remote.show(result)
        if self._page() == "workshop":
            self.sync()
        if result:
            self.window.reveal_side()

    def sync(self):
        """Mostra os detalhes da aba visível."""
        if self._page() == "library":
            has = self.local.item is not None
            self.empty_hint.set_label("Clique duas vezes ou Enter pra aplicar")
            self.stack.set_visible_child_name("local" if has else "empty")
        else:
            has = self.remote.result is not None
            self.empty_hint.set_label("Escolha um wallpaper da Oficina pra ver os detalhes "
                                      "e se inscrever")
            self.stack.set_visible_child_name("remote" if has else "empty")


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Wallpaper Engine")
        self.set_default_size(1280, 820)
        self.set_size_request(360, 480)

        self.settings = app.settings
        Animator.enabled = self.settings["gif_mode"] != 2
        self.account = Account()
        self.gifs = GifDriver(self, self.settings)
        Thumb.width = self.settings.card_width
        self.items = []
        self.cards = {}
        self.current = None
        self.filter = FILTERS[self.settings["library_filter"]][0]
        self.tag = None
        self.applying = False
        self.busy_ids = set()
        self.hidden = set(self.settings["hidden"])
        self._search_source = None
        self._query = ""
        self._watch = (None, None)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=180, vexpand=True)

        # -- Biblioteca: cabeçalho, filtros e grade
        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        title = Gtk.Box(spacing=10)
        title.append(Gtk.Label(label="Biblioteca", xalign=0, css_classes=["panel-title"]))
        self.count = Gtk.Label(css_classes=["count"], valign=Gtk.Align.CENTER)
        title.append(self.count)
        heading.append(title)
        self.lib_subtitle = Gtk.Label(xalign=0, css_classes=["muted"])
        heading.append(self.lib_subtitle)
        # a ordem fica no cabeçalho, sempre à vista (a linha de filtros rola de lado)
        head = Gtk.Box(spacing=12, css_classes=["panel-head"])
        head.append(heading)
        head.append(self._sort_button())

        self.search = Gtk.SearchEntry(placeholder_text="Buscar", width_chars=20)
        self.search.connect("search-changed", self._on_search)
        filters = Gtk.Box(spacing=8, css_classes=["filters"], margin_start=22, margin_end=22,
                          margin_top=10, margin_bottom=6)
        filters.append(self.search)
        seg = Gtk.Box(spacing=4, css_classes=["seg"])
        group = None
        for key, label in FILTERS:
            btn = Gtk.ToggleButton(label=label)
            if group:
                btn.set_group(group)
            group = group or btn
            btn.set_active(key == self.filter)
            btn.connect("toggled", self._on_filter, key)
            seg.append(btn)
        filters.append(seg)
        filters.append(Gtk.Box(hexpand=True))
        self.tag_drop = Gtk.DropDown.new_from_strings([ALL_TAGS])
        self.tag_drop.connect("notify::selected", self._on_tag)
        filters.append(self.tag_drop)

        # barra que aparece no filtro "Incompatíveis"
        self.bulk_label = Gtk.Label(xalign=0, hexpand=True, wrap=True, css_classes=["muted"])
        bulk_btn = Gtk.Button(label="Cancelar inscrição de todos", css_classes=["e-btn", "danger"],
                              valign=Gtk.Align.CENTER)
        bulk_btn.connect("clicked", lambda *_: self._unsubscribe_incompatible())
        self.bulk = Gtk.Box(spacing=12, css_classes=["bulk-bar"], visible=False)
        self.bulk.append(self.bulk_label)
        self.bulk.append(bulk_btn)

        self.grid = Gtk.FlowBox(css_classes=["wall-grid"], valign=Gtk.Align.START,
                                homogeneous=True, column_spacing=10, row_spacing=10,
                                min_children_per_line=1, max_children_per_line=12,
                                selection_mode=Gtk.SelectionMode.SINGLE,
                                activate_on_single_click=False)
        self.grid.set_filter_func(self._visible)
        self.grid.set_sort_func(self._compare)
        self.grid.connect("selected-children-changed", self._on_select)
        self.grid.connect("child-activated", lambda _, card: card.item.compatible and
                          self.apply(card.item))
        self.nothing = Adw.StatusPage(icon_name="edit-find-symbolic", title="Nada encontrado",
                                      vexpand=True, visible=False, css_classes=["compact"])
        gallery = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        gallery.append(self.grid)
        gallery.append(self.nothing)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                      child=gallery)

        library_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        library_page.append(head)
        # os filtros rolam de lado em janelas estreitas em vez de forçar largura mínima
        library_page.append(Gtk.ScrolledWindow(child=filters, propagate_natural_height=True,
                                               hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                               vscrollbar_policy=Gtk.PolicyType.NEVER))
        library_page.append(self.bulk)
        library_page.append(scroller)
        self.gifs.add(scroller, self.grid, lambda: self.stack.get_visible_child_name() == "library")

        self.workshop = Workshop(self)
        self.stack.add_named(library_page, "library")
        self.stack.add_named(self.workshop, "workshop")

        # -- linha do topo: abas em pílula, conta e configurações (arrastável)
        self.side_btn = Gtk.ToggleButton(icon_name="sidebar-show-symbolic", tooltip_text="Painel",
                                         css_classes=["icon-btn"], visible=False)
        tabs = Gtk.Box(spacing=2, css_classes=["tabs"])
        self.tab_btns = {}
        group = None
        for name, label, icon in (("library", "Biblioteca", "folder-pictures-symbolic"),
                                  ("workshop", "Oficina", "web-browser-symbolic")):
            btn = Gtk.ToggleButton(child=Adw.ButtonContent(icon_name=icon, label=label,
                                                           can_shrink=True))
            if group:
                btn.set_group(group)
            group = group or btn
            btn.connect("toggled", lambda b, n=name: b.get_active() and
                        self.stack.set_visible_child_name(n))
            self.tab_btns[name] = btn
            tabs.append(btn)
        top = Gtk.Box(spacing=8)
        top.append(self.side_btn)
        top.append(tabs)
        top.append(Gtk.Box(hexpand=True))
        top.append(self._account_button())
        prefs = Gtk.Button(icon_name="emblem-system-symbolic", tooltip_text="Configurações",
                           css_classes=["icon-btn"])
        prefs.connect("clicked", lambda *_: self.toggle_settings())
        top.append(prefs)

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["e-card", "panel"],
                        overflow=Gtk.Overflow.HIDDEN, vexpand=True)
        panel.append(self.stack)
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        main.append(Gtk.WindowHandle(child=top))
        main.append(panel)

        # -- coluna lateral + conteúdo
        self.side = SidePanel(self)
        self.details = self.side.local
        self.layout = Adw.OverlaySplitView(sidebar=self.side, content=main,
                                           min_sidebar_width=330, max_sidebar_width=372,
                                           sidebar_width_fraction=0.3, css_classes=["layout"])
        self.layout.bind_property("show-sidebar", self.side_btn, "active",
                                  GObject.BindingFlags.BIDIRECTIONAL |
                                  GObject.BindingFlags.SYNC_CREATE)
        self.layout.connect("notify::collapsed", self._on_collapsed)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 900sp"))
        bp.add_setter(self.layout, "collapsed", True)
        bp.add_setter(self.side_btn, "visible", True)
        bp.add_setter(self.search, "width-chars", 10)
        bp.add_setter(self.workshop.search, "width-chars", 10)
        self.add_breakpoint(bp)

        # configurações: gaveta à direita por cima de tudo (fecha clicando fora ou no X)
        self.settings_panel = SettingsPanel(self)
        self.drawer = Adw.OverlaySplitView(content=self.layout, sidebar=self.settings_panel,
                                           sidebar_position=Gtk.PackType.END, collapsed=True,
                                           show_sidebar=False, min_sidebar_width=360,
                                           max_sidebar_width=440, sidebar_width_fraction=0.4)
        self.toasts = Adw.ToastOverlay(child=self.drawer)
        # fundo: luz ambiente + wallpaper desfocado embaixo de tudo; o conteúdo define o tamanho
        self.backdrop = Backdrop()
        self.backdrop.blur = self.settings["background_blur"]
        root = Gtk.Overlay(child=Glow())
        root.add_overlay(self.backdrop)
        root.add_overlay(self.toasts)
        root.set_measure_overlay(self.toasts, True)
        self.set_content(root)
        self.search.set_key_capture_widget(library_page)

        self.stack.connect("notify::visible-child-name", self._on_page)
        self.settings.connect("changed", self._on_setting)
        self.account.connect("changed", lambda *_: self._account_refresh())
        self.reload()
        # só lê o cookie salvo (sem abrir WebView) pra mostrar o nome da conta já no início
        self.account.read_cookie(lambda _ok: self._account_refresh())
        self._account_refresh()
        start = "workshop" if self.settings["start_page"] == 1 else "library"
        self.tab_btns[start].set_active(True)
        self.stack.set_visible_child_name(start)
        self._on_page(self.stack, None)
        GLib.timeout_add_seconds(1, self._poll)

    # -- abas
    def _on_page(self, stack, _):
        name = stack.get_visible_child_name()
        self.tab_btns[name].set_active(True)
        if name == "workshop":
            self.workshop.ensure()
            sel = self.workshop.grid.get_selected_children()
            self.backdrop_for(sel[0].result.id if sel else None)
        else:
            self.backdrop_for(self.details.item.id if self.details.item else None)
        self.side.sync()
        self.gifs.update()

    def _on_collapsed(self, split, _):
        # recolhida, a lateral flutua por cima do conteúdo e precisa de fundo próprio
        (self.side.add_css_class if split.get_collapsed() else
         self.side.remove_css_class)("floating")

    def open_settings(self):
        self.settings_panel.refresh_cache()
        self.drawer.set_show_sidebar(True)

    def close_settings(self):
        self.drawer.set_show_sidebar(False)

    def toggle_settings(self):
        if self.drawer.get_show_sidebar():
            self.close_settings()
        else:
            self.open_settings()

    def reveal_side(self):
        if self.layout.get_collapsed():
            self.layout.set_show_sidebar(True)

    def apply_id(self, item_id):
        card = self.cards.get(item_id)
        if card:
            self.apply(card.item)

    def apply_random(self):
        import random
        pool = [c.item for c in self.cards.values()
                if c.item.compatible and c.item.id not in self.hidden and c.item.id != self.current]
        if pool:
            self.apply(random.choice(pool))
        else:
            self.toast("Nenhum outro wallpaper compatível instalado")

    # -- configurações
    def _on_setting(self, _settings, key):
        if key == "gif_mode":
            Animator.enabled = self.settings["gif_mode"] != 2
            if not Animator.enabled:
                Animator.stop_all()
        elif key in ("acrylic", "background_strength"):
            self.get_application().apply_theme()
        elif key == "background_blur":
            self.backdrop.blur = self.settings["background_blur"]
            self.backdrop_for(self.backdrop.refresh())
        elif key == "card_size":
            Thumb.width = self.settings.card_width
            for grid in (self.grid, self.workshop.grid):
                child = grid.get_first_child()
                while child:
                    child.picture.queue_resize()
                    child = child.get_next_sibling()
        elif key == "app_background":
            key_now, self.backdrop.key = self.backdrop.key, None
            self.backdrop_for(key_now)

    # -- fundo
    def backdrop_for(self, item_id):
        """Wallpaper instalado selecionado vira o fundo; sem seleção, o wallpaper atual."""
        if not self.settings["app_background"]:
            self.backdrop.show_image(None, None)
            return
        card = self.cards.get(item_id) or self.cards.get(self.current)
        if card and card.item.preview:
            self.backdrop.show_image(card.item.id, card.item.preview)
        else:
            self.backdrop.show_image(None, None)

    # -- conta
    def _account_button(self):
        self.account_content = Adw.ButtonContent(icon_name="avatar-default-symbolic",
                                                 can_shrink=True)
        self.account_btn = Gtk.Button(css_classes=["e-btn"], child=self.account_content,
                                      valign=Gtk.Align.CENTER)
        self.account_btn.connect("clicked", lambda *_: self.open_settings()
                                 if self.account.logged_in else
                                 ensure_login(self.account, self, lambda _ok: None))
        return self.account_btn

    def _account_refresh(self):
        acc = self.account
        if acc.checking:
            label = "Verificando…"
        elif acc.logged_in:
            label = acc.name or "Conta Steam"
        else:
            label = "Entrar na Steam"
        self.account_content.set_label(label)
        self.account_btn.set_tooltip_text("Conta da Steam" if acc.logged_in else None)

    # -- inscrições
    def subscribe(self, item_id, title):
        def go(_ok):
            self._busy(item_id, True)
            token = self.account.token
            in_thread(lambda: steam.subscribe(token, item_id, True),
                      lambda _r, err: self._subscribed(item_id, title, err))
        ensure_login(self.account, self, go)

    def _subscribed(self, item_id, title, err):
        self._busy(item_id, False)
        if self._auth_failed(err):
            return
        if err:
            self.toast(f"Falhou: {err}")
            return
        self.workshop.subscribed.add(item_id)
        self.hidden.discard(item_id)
        self._save_hidden()
        self.toast(f"Inscrito em “{title}”" + ("" if steam_running() else ". Abra a Steam pra baixar"))
        self.workshop.refresh_status()

    def unsubscribe(self, items, confirm=False):
        """items = [(id, título)]. Mais de um pede confirmação."""
        if len(items) > 1 and not confirm:
            dialog = Adw.AlertDialog(
                heading=f"Cancelar inscrição de {len(items)} wallpapers?",
                body="A Steam apaga os arquivos deles. Dá pra se inscrever de novo na Oficina.")
            dialog.add_response("cancel", "Voltar")
            dialog.add_response("ok", "Cancelar inscrições")
            dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.connect("response", lambda _d, r: r == "ok" and self.unsubscribe(items, True))
            dialog.present(self)
            return

        def go(_ok):
            ids = [i for i, _t in items]
            for i in ids:
                self._busy(i, True)
            token = self.account.token

            def work():
                done, errors = [], []
                for item_id in ids:
                    try:
                        steam.subscribe(token, item_id, False)
                        done.append(item_id)
                    except steam.AuthError:
                        raise
                    except steam.SteamError as e:
                        errors.append(e)
                return done, errors
            in_thread(work, lambda res, err: self._unsubscribed(items, res, err))
        ensure_login(self.account, self, go)

    def _unsubscribed(self, items, res, err):
        for item_id, _t in items:
            self._busy(item_id, False)
        if self._auth_failed(err):
            return
        if err:
            self.toast(f"Falhou: {err}")
            return
        done, errors = res
        for item_id in done:
            self.workshop.subscribed.discard(item_id)
            if item_id in self.cards:
                self.hidden.add(item_id)
        self._save_hidden()
        if self.details.item and self.details.item.id in self.hidden:
            self.grid.unselect_all()
        self._refilter()
        self.workshop.refresh_status()
        if len(items) == 1 and done:
            msg = f"Inscrição cancelada: “{items[0][1]}”"
        else:
            msg = f"{len(done)} inscrições canceladas"
        if errors:
            msg += f" ({len(errors)} falharam: {errors[0]})"
        if done and not steam_running():
            msg += ". A Steam apaga os arquivos quando abrir"
        self.toast(msg)

    def _unsubscribe_incompatible(self):
        items = [(c.item.id, c.item.title) for c in self.cards.values()
                 if not c.item.compatible and c.item.id not in self.hidden]
        if items:
            self.unsubscribe(items)

    def _auth_failed(self, err):
        if isinstance(err, steam.AuthError):
            self.account.expire()
            self.toast("O login da Steam expirou, entre de novo")
            ensure_login(self.account, self, lambda _ok: None)
            return True
        return False

    def _busy(self, item_id, on):
        (self.busy_ids.add if on else self.busy_ids.discard)(item_id)
        if self.details.item and self.details.item.id == item_id:
            self.details.refresh_current(item_id == self.current)
        self.workshop.details.refresh()

    def _save_hidden(self):
        self.settings.set("hidden", sorted(self.hidden))

    # -- biblioteca
    def reload(self):
        selected = self.details.item.id if self.details.item else None
        self.items = library.scan()
        self.grid.remove_all()
        self.cards = {}
        gc.collect()  # solta os cards antigos (ver Workshop._loaded)
        GLib.timeout_add_seconds(4, lambda: trim_memory() and False)
        for item in self.items:
            card = Card(self, item)
            self.cards[item.id] = card
            self.grid.append(card)

        # desinscritos somem da lista até a Steam apagar a pasta
        gone = self.hidden - set(self.cards)
        if gone:
            self.hidden -= gone
            self._save_hidden()
        self.workshop.refresh_status()

        tags = sorted({t for i in self.items for t in i.tags}, key=str.casefold)
        self._tags = [None] + tags
        self.tag_drop.set_model(Gtk.StringList.new([ALL_TAGS] + tags))
        self.tag = None

        self.current = None
        self._update_current()
        if selected in self.cards and selected not in self.hidden:
            self.grid.select_child(self.cards[selected])
        else:
            self.side.show_local(None)
        self._refilter()

    def _update_current(self):
        item = library.item_for_path(self.items, caelestia.current())
        new = item.id if item else None
        if new == self.current:
            self.side.now.update()
            return
        for key in (self.current, new):
            if key in self.cards:
                self.cards[key].set_current(key == new)
        self.current = new
        self.side.now.update()
        if self.details.item:
            self.details.refresh_current(self.details.item.id == new)
        if not self.details.item:
            self.backdrop_for(None)

    def _poll(self):
        watch = (_mtime(caelestia.CURRENT), _mtime(library.WORKSHOP))
        if watch != self._watch:
            new_items = watch[1] != self._watch[1] and self._watch[1] is not None
            self._watch = watch
            if new_items:
                old = set(self.cards)
                self.reload()
                for key in set(self.cards) - old:
                    self.toast(f"Baixado: {self.cards[key].item.title}")
            else:
                self._update_current()
        # o processo do WE sobe um pouco depois do path.txt mudar (e pode cair)
        self.side.now.update()
        return GLib.SOURCE_CONTINUE

    # -- filtros
    def _on_search(self, _entry):
        if self._search_source:
            GLib.source_remove(self._search_source)

        def run():
            self._search_source = None
            self._query = self.search.get_text().strip().casefold()
            self._refilter()
            return False
        self._search_source = GLib.timeout_add(120, run)

    def _on_filter(self, btn, key):
        if btn.get_active():
            self.filter = key
            self._refilter()

    def _on_tag(self, drop, _):
        i = drop.get_selected()
        self.tag = self._tags[i] if 0 < i < len(self._tags) else None
        self._refilter()

    def _visible(self, card):
        item = card.item
        if item.id in self.hidden:
            return False
        f = self.filter
        if (f == "compat" and not item.compatible) or (f == "bad" and item.compatible) \
                or (f in ("scene", "video") and item.type != f):
            return False
        if self.tag and self.tag not in item.tags:
            return False
        return not self._query or self._query in card.haystack

    def _sort_button(self):
        """Botão de ordem: mostra a ordem atual e abre a lista de opções."""
        current = self.settings["library_sort"]
        if current not in {k for k, *_ in SORTS}:
            current = "recent"
        self.sort_key = current
        self.sort_content = Adw.ButtonContent(icon_name="view-sort-descending-symbolic",
                                              can_shrink=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6,
                      margin_bottom=6, margin_start=6, margin_end=6)
        box.append(Gtk.Label(label="Ordenar por", xalign=0, css_classes=["heading"],
                             margin_start=6, margin_bottom=4))
        group = None
        for key, label, *_ in SORTS:
            check = Gtk.CheckButton(label=label, active=key == current)
            if group:
                check.set_group(group)
            group = group or check
            check.connect("toggled", lambda c, k=key: c.get_active() and self._set_sort(k))
            box.append(check)
        self.sort_btn = Gtk.MenuButton(child=self.sort_content, popover=Gtk.Popover(child=box),
                                       css_classes=["e-btn"], tooltip_text="Ordem",
                                       valign=Gtk.Align.CENTER)
        self._set_sort(current)
        return self.sort_btn

    def _set_sort(self, key):
        self.sort_key = key
        self.sort_content.set_label(next(label for k, label, *_ in SORTS if k == key))
        self.settings.set("library_sort", key)
        self.sort_btn.popdown()
        if hasattr(self, "grid"):  # na montagem da janela a grade ainda não existe
            self.grid.invalidate_sort()
            self.gifs.update()

    def _compare(self, a, b):
        _k, _l, keyfn, desc = next(s for s in SORTS if s[0] == self.sort_key)
        x, y = keyfn(a.item), keyfn(b.item)
        if x == y:
            x, y, desc = a.item.title.casefold(), b.item.title.casefold(), False
        r = (x > y) - (x < y)
        return -r if desc else r

    def _refilter(self):
        self.grid.invalidate_filter()
        shown = sum(1 for c in self.cards.values() if self._visible(c))
        self.nothing.set_visible(not shown)
        self.nothing.set_description(None if self.items else
                                     "Assine wallpapers do Wallpaper Engine na Steam Workshop")
        bad = sum(1 for c in self.cards.values()
                  if not c.item.compatible and c.item.id not in self.hidden)
        self.bulk.set_visible(self.filter == "bad" and bad > 0)
        self.bulk_label.set_label(f"{bad} wallpapers que não rodam no Linux (presets e web)")
        installed = [c for c in self.cards.values() if c.item.id not in self.hidden]
        ok = sum(1 for c in installed if c.item.compatible)
        self.count.set_label(str(len(installed)))
        self.lib_subtitle.set_label(f"{ok} rodam no Linux · {len(installed) - ok} incompatíveis"
                                    + (f" · {shown} no filtro" if shown != len(installed) else ""))
        self.gifs.update()

    def _on_select(self, grid):
        sel = grid.get_selected_children()
        item = sel[0].item if sel else None
        self.side.show_local(item, bool(item) and item.id == self.current)
        self.backdrop_for(item.id if item else None)

    # -- aplicar
    def apply(self, item):
        if not item or self.applying:
            return
        if not item.compatible:
            self.toast("Esse tipo não roda no linux-wallpaperengine")
            return
        self._run(item.project, f"Aplicando “{item.title}”…")

    def restore_static(self):
        path = caelestia.last_static()
        if not path or not os.path.exists(path):
            self.toast("Não achei o último wallpaper estático")
            return
        self._run(path, "Voltando pro wallpaper estático…")

    def _run(self, path, message):
        self.applying = True
        self.toast(message)

        def work():
            try:
                caelestia.set_wallpaper(path)
                err = None
            except Exception as e:  # noqa: BLE001 - qualquer falha vira toast
                err = str(e)
            GLib.idle_add(done, err)

        def done(err):
            self.applying = False
            if err:
                self.toast(f"Falhou: {err}")
            self._update_current()
            return False
        threading.Thread(target=work, daemon=True).start()

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None
        self.settings = Settings()
        self._css = None
        self._scheme_version = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.apply_theme()
        GLib.timeout_add_seconds(1, self._poll_theme)
        quit_ = Gio.SimpleAction.new("quit", None)
        quit_.connect("activate", lambda *_: self.quit())
        self.add_action(quit_)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def do_activate(self):
        if not self.window:
            self.window = Window(self)
        self.window.present()

    def do_command_line(self, command_line):
        # "--oficina" (botão do launcher do Caelestia) abre direto na aba Oficina
        self.activate()
        if "--oficina" in command_line.get_arguments()[1:]:
            self.window.tab_btns["workshop"].set_active(True)
        return 0

    def apply_theme(self):
        self._scheme_version = theme.version()
        style = Adw.StyleManager.get_default()
        dark = theme.scheme().get("mode", "dark") != "light"
        style.set_color_scheme(Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
        self._css = theme.apply(self._css, self.settings)

    def _poll_theme(self):
        if theme.version() != self._scheme_version:
            self.apply_theme()
        return GLib.SOURCE_CONTINUE


def main(argv=None):
    import sys

    from . import watchdog
    watchdog.start()
    return App().run(argv if argv is not None else sys.argv)
