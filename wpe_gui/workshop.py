"""Aba Oficina: navega pela Steam Workshop do Wallpaper Engine com cards nativos,
uma página (30 itens) por vez.

A busca não precisa de conta. Se inscrever usa o login da Steam (feito uma vez numa
janelinha WebKit, cookies salvos); quem baixa é o cliente da Steam, que joga os arquivos
na pasta do workshop, e a Biblioteca percebe sozinha."""

import gc
import os
import re
import subprocess
import threading
from collections import OrderedDict
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Adw, GLib, GObject, Gtk, WebKit  # noqa: E402

from . import steam  # noqa: E402
from .widgets import Animator, PreviewCard, fetch, load_thumb, trim_memory  # noqa: E402

DATA = os.path.join(GLib.get_user_data_dir(), "wallpaper-engine-gui", "webkit")
CACHE = os.path.join(GLib.get_user_cache_dir(), "wallpaper-engine-gui", "webkit")
STEAM_PID = os.path.expanduser("~/.steam/steam.pid")
LOGIN_URL = "https://steamcommunity.com/login/home/?goto=app%2F431960%2Fworkshop%2F"
ALL_GENRES = "Todos os gêneros"
PAGE_CACHE = 40


def steam_running():
    try:
        with open(STEAM_PID) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def in_thread(work, done):
    """Roda work() fora da interface e entrega (resultado, erro) pro done()."""
    def run():
        try:
            res, err = work(), None
        except Exception as e:  # noqa: BLE001 - qualquer falha vira mensagem
            res, err = None, e
        GLib.idle_add(lambda: done(res, err) and False)
    threading.Thread(target=run, daemon=True).start()


def stars(n):
    return "★" * n + "☆" * (5 - n)


def plain(text):
    """As descrições da Workshop vêm em BBCode; tira as tags."""
    return re.sub(r"\[/?[a-z0-9*]+(=[^\]]*)?\]", "", text, flags=re.I).strip()


# -- conta da Steam -------------------------------------------------------------

class Account(GObject.Object):
    """Login da Steam guardado no WebKit. O token vem do cookie steamLoginSecure;
    quando expira, abrir a comunidade num WebView escondido renova sozinho."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self):
        super().__init__()
        os.makedirs(DATA, exist_ok=True)
        self.session = WebKit.NetworkSession.new(DATA, CACHE)
        self.cookies = self.session.get_cookie_manager()
        self.cookies.set_persistent_storage(os.path.join(DATA, "cookies.sqlite"),
                                            WebKit.CookiePersistentStorage.SQLITE)
        self.steamid = self.token = None
        self.name = ""
        self.checking = False
        self.checked = False
        self._hidden = None
        self._waiting = []

    @property
    def logged_in(self):
        return bool(self.token) and bool(steam.parse_login_cookie(f"{self.steamid}||{self.token}"))

    def check(self, then=None):
        """Lê o cookie; se não tiver ou expirou, deixa a Steam renovar num WebView oculto.
        then(logado) é chamado no fim."""
        if then:
            self._waiting.append(then)
        if self.checking:
            return
        if self.logged_in:
            self._done()
            return
        self.checking = True
        self.emit("changed")
        self.read_cookie(self._after_read)

    def _after_read(self, ok):
        if ok or self.checked:
            self._done()
            return
        view = WebKit.WebView(network_session=self.session)
        self._hidden = view

        def finished(_v, event):
            if event == WebKit.LoadEvent.FINISHED:
                self.read_cookie(lambda _ok: self._done())
        view.connect("load-changed", finished)
        view.load_uri("https://steamcommunity.com/")

    def _done(self):
        self._hidden = None
        self.checking = False
        self.checked = True
        self.emit("changed")
        waiting, self._waiting = self._waiting, []
        for cb in waiting:
            cb(bool(self.logged_in))

    def read_cookie(self, callback):
        def got(manager, result):
            try:
                cookies = manager.get_cookies_finish(result)
            except GLib.Error:
                cookies = []
            for c in cookies:
                if c.get_name() == "steamLoginSecure":
                    parsed = steam.parse_login_cookie(c.get_value())
                    if parsed:
                        self._set(*parsed[:2])
                        callback(True)
                        return
            callback(False)
        self.cookies.get_cookies("https://steamcommunity.com/", None, got)

    def _set(self, steamid, token):
        changed = steamid != self.steamid
        self.steamid, self.token = steamid, token
        if changed:
            in_thread(lambda: steam.profile(steamid), self._profile)
        self.emit("changed")

    def _profile(self, res, _err):
        if res:
            self.name = res[0]
            self.emit("changed")

    def expire(self):
        self.token = None
        self.checked = False
        self.emit("changed")

    def logout(self):
        self.steamid = self.token = None
        self.name = ""
        self.session.get_website_data_manager().clear(WebKit.WebsiteDataTypes.ALL, 0,
                                                      None, None)
        self.emit("changed")


class LoginDialog(Adw.Dialog):
    def __init__(self, account, then=None):
        super().__init__(title="Entrar na Steam", content_width=520, content_height=720)
        self.account = account
        self.then = then
        view = WebKit.WebView(network_session=account.session, vexpand=True)
        view.connect("load-changed", self._on_load)
        view.connect("create", lambda v, action: v.load_uri(action.get_request().get_uri()))
        page = Adw.ToolbarView(content=view)
        page.add_top_bar(Adw.HeaderBar())
        self.set_child(page)
        view.load_uri(LOGIN_URL)

    def _on_load(self, _view, event):
        if event == WebKit.LoadEvent.FINISHED:
            self.account.read_cookie(self._got)

    def _got(self, ok):
        if ok:
            self.close()
            if self.then:
                self.then(True)


def ensure_login(account, parent, then):
    """Chama then(True) quando tiver token válido (renovando ou pedindo login)."""
    def checked(ok):
        if ok:
            then(True)
        else:
            LoginDialog(account, then).present(parent)
    account.check(checked)


# -- cards e detalhes -------------------------------------------------------------

class RemoteCard(PreviewCard):
    def __init__(self, result):
        super().__init__(result.title,
                         f"{stars(result.stars)}  ·  {steam.compact(result.subscriptions)} inscritos")
        self.result = result
        self.top_end.append(self.badge(result.type_name))
        if result.rating and result.rating != "Livre":
            self.bottom_end.append(self.badge(result.rating, "bad"))
        self.status = self.badge("", "now")
        self.status.set_visible(False)
        self.top_start.append(self.status)
        if result.preview_url:
            fetch(result.preview_url, self.set_image)

    def set_status(self, text):
        self.status.set_label(text or "")
        self.status.set_visible(bool(text))


class RemoteDetails(Gtk.ScrolledWindow):
    def __init__(self, page):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.page = page
        self.result = None

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, css_classes=["details"])
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True,
                                   overflow=Gtk.Overflow.HIDDEN, css_classes=["detail-preview"])
        self.picture.set_size_request(-1, 180)
        self.animator = Animator(self.picture)
        box.append(self.picture)

        self.title = Gtk.Label(xalign=0, wrap=True, selectable=True, css_classes=["detail-title"])
        box.append(self.title)
        self.author = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label"])
        box.append(self.author)
        self.stats = Gtk.Label(xalign=0, wrap=True, css_classes=["caption"])
        box.append(self.stats)

        self.main_btn = Gtk.Button(css_classes=["e-btn", "primary"])
        self.main_btn.connect("clicked", lambda *_: page.main_action(self.result))
        box.append(self.main_btn)
        self.note = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label", "caption"],
                              visible=False)
        box.append(self.note)

        links = Gtk.Box(spacing=8, homogeneous=True)
        self.apply_btn = Gtk.Button(label="Aplicar", css_classes=["e-btn", "tonal"])
        self.apply_btn.connect("clicked", lambda *_: page.window.apply_id(self.result.id))
        web = Gtk.Button(label="Abrir na Steam", css_classes=["e-btn"])
        web.connect("clicked", lambda *_: Gtk.UriLauncher.new(self.result.url).launch(
            page.get_root(), None, None, None))
        links.append(self.apply_btn)
        links.append(web)
        box.append(links)

        self.tags = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=6,
                                row_spacing=6, max_children_per_line=20)
        box.append(self.tags)
        self.description = Gtk.Label(xalign=0, yalign=0, wrap=True, selectable=True)
        box.append(self.description)

        self.content = box
        self.set_child(box)

    def show(self, result):
        self.result = result
        if not result:
            self.animator.stop(None)
            return
        self.get_vadjustment().set_value(0)
        self.title.set_label(result.title)
        self.author.set_label(f"por {result.author}" if result.author else "")
        self.author.set_visible(bool(result.author))
        updated = datetime.fromtimestamp(result.updated).strftime("%d/%m/%Y") if result.updated else "?"
        rating = f"{result.rating} · " if result.rating else ""
        self.stats.set_label(f"{rating}{stars(result.stars)} ({steam.compact(result.votes)} votos)\n"
                             f"{steam.compact(result.subscriptions)} inscritos · "
                             f"{steam.size(result.size)} · atualizado em {updated}")
        while child := self.tags.get_first_child():
            self.tags.remove(child)
        tags = [t for t in result.tags if t not in steam.HIDDEN_TAGS]
        for tag in tags:
            self.tags.append(Gtk.Label(label=tag, css_classes=["tag-chip"]))
        self.tags.set_visible(bool(tags))

        self.description.set_label(result.summary)
        in_thread(lambda: steam.description(result.id),
                  lambda text, _e, r=result: self.result is r and text and
                  self.description.set_label(plain(text)))

        self.picture.set_paintable(None)
        self.animator.stop(None)

        def fetched(path, r=result):
            if self.result is r and path:
                load_thumb(path, lambda t: self.result is r and not self.animator.playing
                           and self.picture.set_paintable(t))
                self.animator.play(path)
        if result.preview_url:
            fetch(result.preview_url, fetched)
        self.refresh()

    def refresh(self):
        r = self.result
        if not r:
            return
        state = self.page.state(r.id)
        account = self.page.account
        self.apply_btn.set_visible(state == "installed")
        self.note.set_visible(False)
        self.main_btn.remove_css_class("danger")
        self.main_btn.add_css_class("primary")
        if r.id in self.page.window.busy_ids:
            self.main_btn.set_label("Aguarde…")
            self.main_btn.set_sensitive(False)
            return
        self.main_btn.set_sensitive(True)
        if state in ("installed", "subscribed", "downloading"):
            self.main_btn.set_label("Cancelar inscrição")
            self.main_btn.remove_css_class("primary")
            self.main_btn.add_css_class("danger")
        elif not account.logged_in:
            self.main_btn.set_label("Entrar na Steam pra se inscrever")
        else:
            self.main_btn.set_label("Inscrever-se")
        if state == "downloading" or (state == "subscribed" and steam_running()):
            self.note.set_label("A Steam está baixando; ele aparece na Biblioteca quando terminar.")
            self.note.set_visible(True)
        elif state == "subscribed":
            self.note.set_label("Inscrito. Abra a Steam pra ela baixar.")
            self.note.set_visible(True)


# -- a aba ------------------------------------------------------------------------

class Workshop(Gtk.Box):
    BANNER = "A Steam está fechada: o que você se inscrever só baixa com ela aberta"

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.account = window.account
        self.cards = {}
        self.subscribed = set()        # inscritos nesta sessão (ainda não baixados)
        self.page_no = 1
        self.pages = 0
        self.generation = 0
        self.started = False
        self.cache = OrderedDict()     # (consulta, página) -> (resultados, páginas)
        self._search_source = None

        # -- cabeçalho do painel
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, css_classes=["panel-head"])
        head.append(Gtk.Label(label="Oficina da Steam", xalign=0, css_classes=["panel-title"]))
        self.subtitle = Gtk.Label(label="Wallpapers da Workshop do Wallpaper Engine", xalign=0,
                                  css_classes=["muted"])
        head.append(self.subtitle)
        self.append(head)

        # -- filtros
        bar = Gtk.Box(spacing=8, css_classes=["filters"], margin_start=22, margin_end=22,
                      margin_top=10, margin_bottom=6)
        self.search = Gtk.SearchEntry(placeholder_text="Buscar na Oficina", width_chars=22)
        self.search.connect("search-changed", self._on_search_changed)
        self.search.connect("activate", lambda *_: self.reset())
        bar.append(self.search)
        self.kind = steam.TYPES[0][0]
        seg = Gtk.Box(spacing=4, css_classes=["seg"])
        group = None
        for key, label in steam.TYPES:
            btn = Gtk.ToggleButton(label=label)
            if group:
                btn.set_group(group)
            group = group or btn
            btn.set_active(key == self.kind)
            btn.connect("toggled", self._on_kind, key)
            seg.append(btn)
        bar.append(seg)
        bar.append(Gtk.Box(hexpand=True))
        self.genre = Gtk.DropDown.new_from_strings([ALL_GENRES] + steam.GENRES)
        self.genre.connect("notify::selected", lambda *_: self.reset())
        bar.append(self.genre)
        self.rating = Gtk.DropDown.new_from_strings([label for _, label in steam.RATINGS])
        self.rating.set_selected(window.settings["default_rating"])
        self.rating.connect("notify::selected", lambda *_: self.reset())
        bar.append(self.rating)
        self.sort = Gtk.DropDown.new_from_strings([label for _, label in steam.SORTS])
        self.sort.connect("notify::selected", self._on_sort)
        bar.append(self.sort)
        self.period = Gtk.DropDown.new_from_strings([label for _, label in steam.PERIODS])
        self.period.set_selected(1)
        self.period.connect("notify::selected", lambda *_: self.reset())
        bar.append(self.period)
        self.append(Gtk.ScrolledWindow(child=bar, propagate_natural_height=True,
                                       hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                       vscrollbar_policy=Gtk.PolicyType.NEVER))

        self.banner = Adw.Banner(title=self.BANNER, button_label="Abrir Steam",
                                 revealed=not steam_running(), margin_start=16, margin_end=16)
        self.banner.connect("button-clicked", lambda *_: self._start_steam())
        self.append(self.banner)

        # -- grade + paginação
        self.grid = Gtk.FlowBox(css_classes=["wall-grid"], valign=Gtk.Align.START,
                                homogeneous=True, column_spacing=10, row_spacing=10,
                                min_children_per_line=1, max_children_per_line=12,
                                selection_mode=Gtk.SelectionMode.SINGLE)
        self.grid.connect("selected-children-changed", self._on_select)
        self.message = Adw.StatusPage(vexpand=True, css_classes=["compact"])
        self.retry = Gtk.Button(label="Tentar de novo", css_classes=["pill"],
                                halign=Gtk.Align.CENTER)
        self.retry.connect("clicked", lambda *_: self.go(self.page_no))
        self.message.set_child(self.retry)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                           vexpand=True, child=self.grid)
        self.body = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                              vexpand=True)
        self.body.add_named(self.scroller, "grid")
        self.body.add_named(Adw.Spinner(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                        width_request=48, height_request=48), "loading")
        self.body.add_named(self.message, "message")

        self.pager = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER, css_classes=["pager"])
        self.append(self.body)
        self.append(Gtk.ScrolledWindow(child=self.pager, propagate_natural_height=True,
                                       hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                       vscrollbar_policy=Gtk.PolicyType.NEVER))

        self.account.connect("changed", lambda *_: self.details.refresh())
        window.gifs.add(self.scroller, self.grid,
                        lambda: window.stack.get_visible_child_name() == "workshop")
        GLib.timeout_add_seconds(3, self._tick)

    @property
    def details(self):
        """Os detalhes ficam na coluna lateral da janela."""
        return self.window.side.remote

    # -- ciclo de vida
    def ensure(self):
        """Primeira abertura da aba: verifica o login e carrega a primeira página."""
        if self.started:
            return
        self.started = True
        self.account.check()
        self.go(1)

    def _tick(self):
        running = steam_running()
        if running:
            self.banner.set_title(self.BANNER)
        self.banner.set_revealed(not running)
        if self.subscribed and self.started:
            self.refresh_status()
        return GLib.SOURCE_CONTINUE

    # -- busca
    def _on_search_changed(self, _entry):
        if self._search_source:
            GLib.source_remove(self._search_source)

        def go():
            self._search_source = None
            self.reset()
            return False
        self._search_source = GLib.timeout_add(450, go)

    def _on_kind(self, btn, key):
        if btn.get_active():
            self.kind = key
            self.reset()

    def _on_sort(self, *_):
        self.period.set_visible(steam.SORTS[self.sort.get_selected()][0] == "trend")
        self.reset()

    def query(self):
        genre = self.genre.get_selected()
        return (self.search.get_text().strip(),
                steam.SORTS[self.sort.get_selected()][0],
                steam.PERIODS[self.period.get_selected()][0],
                self.kind,
                steam.GENRES[genre - 1] if genre > 0 else None,
                steam.RATINGS[self.rating.get_selected()][0])

    def reset(self):
        if not self.started:
            return
        if self._search_source:
            GLib.source_remove(self._search_source)
            self._search_source = None
        self.go(1)

    def _request(self, query, page, done):
        key = (query, page)
        if key in self.cache:
            self.cache.move_to_end(key)
            done(self.cache[key], None)
            return
        text, sort, days, kind, genre, rating = query

        def store(res, err):
            if res:
                self.cache[key] = res
                while len(self.cache) > PAGE_CACHE:
                    self.cache.popitem(last=False)
            done(res, err)
        in_thread(lambda: steam.browse(text=text, sort=sort, days=days, kind=kind,
                                       genre=genre, rating=rating, page=page), store)

    def go(self, page):
        self.generation += 1
        gen = self.generation
        query = self.query()
        self.page_no = page
        if (query, page) not in self.cache:
            self.body.set_visible_child_name("loading")
        self._request(query, page, lambda res, err: self._loaded(gen, query, page, res, err))

    def _loaded(self, gen, query, page, res, err):
        if gen != self.generation:
            return
        self.grid.remove_all()
        self.cards = {}
        # os cards têm ciclos de referência (sinais com lambda); sem coletar, as
        # miniaturas de todas as páginas já vistas ficavam na memória
        gc.collect()
        self.window.side.show_remote(None)
        if err:
            self.message.set_icon_name("network-offline-symbolic")
            self.message.set_title("Não deu pra carregar a Oficina")
            self.message.set_description(str(err))
            self.retry.set_visible(True)
            self.body.set_visible_child_name("message")
            self._build_pager()
            return
        results, pages = res
        self.pages = pages
        self._subtitle()
        for r in results:
            if r.id in self.cards:
                continue
            card = RemoteCard(r)
            self.cards[r.id] = card
            card.set_status(self._status_text(r.id))
            self.grid.append(card)
        if not self.cards:
            self.message.set_icon_name("edit-find-symbolic")
            self.message.set_title("Nada encontrado")
            self.message.set_description(None)
            self.retry.set_visible(False)
            self.body.set_visible_child_name("message")
        else:
            self.body.set_visible_child_name("grid")
        self.scroller.get_vadjustment().set_value(0)
        self._build_pager()
        self.window.gifs.update()
        # depois que as miniaturas da página nova carregam, solta o que sobrou da antiga
        GLib.timeout_add_seconds(4, lambda: trim_memory() and False)
        # a próxima página já fica pronta no cache, então "Próxima" é instantâneo
        if page < pages:
            self._request(query, page + 1, lambda *_: None)

    def _subtitle(self):
        sort = steam.SORTS[self.sort.get_selected()][1]
        if steam.SORTS[self.sort.get_selected()][0] == "trend":
            sort += f" · {steam.PERIODS[self.period.get_selected()][1].lower()}"
        text = self.search.get_text().strip()
        bits = [f"“{text}”" if text else None,
                f"Página {self.page_no} de {self.pages}" if self.pages else "Nada encontrado", sort]
        self.subtitle.set_label("  ·  ".join(b for b in bits if b))

    def _build_pager(self):
        while child := self.pager.get_first_child():
            self.pager.remove(child)
        page, pages = self.page_no, self.pages
        self.pager.set_visible(pages > 1)
        if pages <= 1:
            return

        def button(label, target, current=False, icon=None):
            b = Gtk.Button(css_classes=["flat", "page"] + (["current"] if current else []))
            if icon:
                b.set_icon_name(icon)
            else:
                b.set_label(label)
            b.set_sensitive(1 <= target <= pages and not current)
            b.connect("clicked", lambda *_: self.go(target))
            self.pager.append(b)

        button(None, page - 1, icon="go-previous-symbolic")
        shown = sorted({1, pages, *range(max(1, page - 2), min(pages, page + 2) + 1)})
        last = 0
        for n in shown:
            if n - last > 1:
                self.pager.append(Gtk.Label(label="…", css_classes=["dim-label"]))
            button(str(n), n, current=n == page)
            last = n
        button(None, page + 1, icon="go-next-symbolic")

        jump = Gtk.SpinButton.new_with_range(1, pages, 1)
        jump.set_value(page)
        jump.set_tooltip_text("Ir pra página")
        jump.set_margin_start(12)
        jump.connect("activate", lambda s: self.go(int(s.get_value())))
        self.pager.append(jump)

    def _on_select(self, grid):
        sel = grid.get_selected_children()
        result = sel[0].result if sel else None
        self.window.side.show_remote(result)
        self.window.backdrop_for(result.id if result else None)

    # -- estado de cada item
    def state(self, item_id):
        if item_id in self.window.cards and item_id not in self.window.hidden:
            return "installed"
        if steam.downloading(item_id):
            return "downloading"
        if item_id in self.subscribed:
            return "subscribed"
        return None

    def _status_text(self, item_id):
        return {"installed": "Instalado", "downloading": "Baixando…",
                "subscribed": "Inscrito"}.get(self.state(item_id))

    def refresh_status(self):
        """Chamado quando a Biblioteca muda (algo baixou ou foi removido)."""
        self.subscribed -= set(self.window.cards)
        for item_id, card in self.cards.items():
            card.set_status(self._status_text(item_id))
        self.details.refresh()

    # -- inscrição
    def main_action(self, result):
        if self.state(result.id) is not None:
            self.window.unsubscribe([(result.id, result.title)])
            return
        self.window.subscribe(result.id, result.title)

    def _start_steam(self):
        subprocess.Popen(["steam", "-silent"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        self.banner.set_title("Abrindo a Steam…")
