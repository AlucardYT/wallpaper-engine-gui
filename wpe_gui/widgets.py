"""Peças visuais compartilhadas: cards 16:9, GIFs animados, cache de previews e o
fundo desfocado da janela."""

import hashlib
import os
import urllib.request
import warnings
import weakref
from concurrent.futures import ThreadPoolExecutor

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

# PixbufAnimation é "deprecated" mas é o único jeito de tocar GIF no GTK4 sem GStreamer
warnings.filterwarnings("ignore", category=DeprecationWarning, module=__name__)

THUMB_WIDTH = 360
CACHE_ROOT = os.path.join(GLib.get_user_cache_dir(), "wallpaper-engine-gui")
PREVIEWS = os.path.join(CACHE_ROOT, "previews")
THUMBS = os.path.join(CACHE_ROOT, "thumbs")
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) wallpaper-engine-gui"
EXTENSIONS = {"image/gif": ".gif", "image/png": ".png", "image/webp": ".webp"}

# rede e decodificação separadas: download lento não trava miniatura do disco
_net = ThreadPoolExecutor(max_workers=6)
_decode = ThreadPoolExecutor(max_workers=3)


def trim_memory():
    """Devolve ao sistema a memória que o malloc guardou (decodificar miniaturas em
    várias threads deixa muito espaço livre preso nas arenas)."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def _idle(callback, *args):
    GLib.idle_add(lambda: callback(*args) and False)


# -- arquivos -----------------------------------------------------------------------

def fetch(url, callback):
    """Baixa um preview pro cache (uma vez só) e devolve o caminho local."""
    _net.submit(lambda: _idle(callback, download(url)))


def download(url):
    name = hashlib.sha1(url.encode()).hexdigest()
    for ext in (".gif", ".jpg", ".png", ".webp"):
        if os.path.exists(path := os.path.join(PREVIEWS, name + ext)):
            return path
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as res:
            kind = res.headers.get_content_type()
            data = res.read()
    except OSError:
        return None
    os.makedirs(PREVIEWS, exist_ok=True)
    path = os.path.join(PREVIEWS, name + EXTENSIONS.get(kind, ".jpg"))
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return path


def cache_size():
    total = 0
    for root, _dirs, files in os.walk(CACHE_ROOT):
        if "webkit" in root:
            continue
        total += sum(os.path.getsize(os.path.join(root, f)) for f in files)
    return total


def clear_cache():
    import shutil
    for d in (PREVIEWS, THUMBS):
        shutil.rmtree(d, ignore_errors=True)


def _thumb_file(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = f"{path}:{st.st_mtime_ns}:{st.st_size}:{THUMB_WIDTH}:v2"
    return os.path.join(THUMBS, hashlib.sha1(key.encode()).hexdigest() + ".jpg")


def load_thumb(path, callback):
    """Primeiro quadro reduzido. Fica salvo em JPEG, então da segunda vez é instantâneo."""
    def work():
        cached = _thumb_file(path)
        texture = None
        if cached and os.path.exists(cached):
            try:
                texture = Gdk.Texture.new_from_filename(cached)
            except GLib.Error:
                texture = None
        if texture is None:
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, THUMB_WIDTH, -1, True)
            except GLib.Error:
                pixbuf = None
            if pixbuf and cached:
                os.makedirs(THUMBS, exist_ok=True)
                if pixbuf.get_has_alpha():
                    # achata num pixbuf RGB de verdade: o gdk-pixbuf 2.44 (glycin) grava
                    # um RGBA em JPEG como tons de cinza, e o primeiro quadro dos GIFs
                    # vem com alpha (era por isso que as miniaturas paradas ficavam P&B)
                    w, h = pixbuf.get_width(), pixbuf.get_height()
                    flat = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
                    flat.fill(0x202020ff)
                    pixbuf.composite(flat, 0, 0, w, h, 0, 0, 1, 1,
                                     GdkPixbuf.InterpType.NEAREST, 255)
                    pixbuf = flat
                try:
                    pixbuf.savev(cached + ".part", "jpeg", ["quality"], ["88"])
                    os.replace(cached + ".part", cached)
                except GLib.Error:
                    pass
            texture = Gdk.Texture.new_for_pixbuf(pixbuf) if pixbuf else None
        _idle(callback, texture)
    _decode.submit(work)


# -- GIF ------------------------------------------------------------------------------

class GifClock:
    """Um relógio só pra todos os GIFs: em vez de cada card ter seu timer (e pedir um
    quadro novo da janela a cada um), todos avançam juntos, no máximo FPS vezes por
    segundo, e a janela redesenha uma vez por tique."""

    FPS = 15
    _animators = set()
    _source = None

    @classmethod
    def add(cls, animator):
        cls._animators.add(animator)
        if cls._source is None:
            cls._source = GLib.timeout_add(1000 // cls.FPS, cls._tick)

    @classmethod
    def remove(cls, animator):
        cls._animators.discard(animator)

    @classmethod
    def _tick(cls):
        if not cls._animators:
            cls._source = None
            return False
        now = GLib.get_monotonic_time() // 1000
        for animator in list(cls._animators):
            animator.step(now)
        return True


class _AnimCache:
    """GIFs já decodificados (os previews têm ~256px, então cabem vários na memória);
    voltar a ver um card não decodifica de novo."""

    MAX = 12   # ~5 MB cada (todos os quadros já compostos)
    items = {}

    @classmethod
    def get(cls, path):
        anim = cls.items.pop(path, None)
        if anim is not None:
            cls.items[path] = anim
        return anim

    @classmethod
    def put(cls, path, anim):
        cls.items[path] = anim
        while len(cls.items) > cls.MAX:
            cls.items.pop(next(iter(cls.items)))


class Animator:
    """Toca um GIF num Gtk.Picture (o GTK4 não anima GIF sozinho)."""

    # GIFs "nunca animados" nas configurações: nada toca, nem ao passar o mouse
    enabled = True

    @staticmethod
    def stop_all():
        for animator in list(GifClock._animators):
            animator.stop()

    def __init__(self, picture):
        self.picture = picture
        self.token = 0
        self.path = None
        self.iter = None
        self.due = 0

    @property
    def playing(self):
        return self.path is not None

    def play(self, path, still=None):
        if path and path == self.path:
            return
        self.stop(still)
        if not path or not path.lower().endswith(".gif") or not Animator.enabled:
            return
        self.path = path
        token = self.token
        anim = _AnimCache.get(path)
        if anim is not None:
            self._start(token, anim)
            return

        def work():
            try:
                loaded = GdkPixbuf.PixbufAnimation.new_from_file(path)
            except GLib.Error:
                return
            _idle(self._loaded, token, path, loaded)
        _decode.submit(work)

    def _loaded(self, token, path, anim):
        _AnimCache.put(path, anim)
        self._start(token, anim)

    def _start(self, token, anim):
        if token != self.token or anim.is_static_image():
            return
        self.iter = anim.get_iter(None)
        self.due = 0
        GifClock.add(self)

    def step(self, now):
        if self.iter is None or now < self.due:
            return
        pixbuf = self.iter.get_pixbuf()
        if pixbuf:
            self.picture.set_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))
        delay = self.iter.get_delay_time()
        if delay < 0:
            self._halt()
            return
        self.iter.advance(None)
        self.due = now + max(delay, 1000 // GifClock.FPS)

    def _halt(self):
        GifClock.remove(self)
        self.iter = None

    def stop(self, still=None):
        self.token += 1
        self.path = None
        self._halt()
        if still is not None:
            self.picture.set_paintable(still)


class Thumb(Gtk.Picture):
    """Miniatura 16:9 que não pede o tamanho da imagem (senão a grade vira uma coluna).
    A largura vem das configurações (tamanho dos cards)."""

    width = 260

    def __init__(self):
        super().__init__(content_fit=Gtk.ContentFit.COVER, can_shrink=True)

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return self.width * 3 // 4, self.width, -1, -1
        width = for_size if for_size > 0 else self.width
        return width * 9 // 16, width * 9 // 16, -1, -1


class FixedPicture(Gtk.Picture):
    """Picture com tamanho fixo (o Gtk.Picture pede o tamanho da imagem)."""

    def __init__(self, width, height, **kw):
        super().__init__(content_fit=Gtk.ContentFit.COVER, can_shrink=True, **kw)
        self.size = (width, height)

    def do_measure(self, orientation, for_size):
        n = self.size[0] if orientation == Gtk.Orientation.HORIZONTAL else self.size[1]
        return n, n, -1, -1


class PreviewCard(Gtk.FlowBoxChild):
    """Base dos cards da Biblioteca e da Oficina: miniatura, selos, título e GIF."""

    def __init__(self, title, meta=None):
        super().__init__()
        self.texture = None
        self.gif = None          # caminho do GIF local, quando tiver
        self.hovered = False
        self.wanted = False      # o GifDriver quer este card animado

        self.picture = Thumb()
        self.overlay = Gtk.Overlay(overflow=Gtk.Overflow.HIDDEN, css_classes=["wall-thumb"])
        self.overlay.set_child(self.picture)
        self.top_end = Gtk.Box(spacing=4, halign=Gtk.Align.END, valign=Gtk.Align.START,
                               margin_top=8, margin_end=8)
        self.top_start = Gtk.Box(spacing=4, halign=Gtk.Align.START, valign=Gtk.Align.START,
                                 margin_top=8, margin_start=8)
        self.bottom_end = Gtk.Box(spacing=4, halign=Gtk.Align.END, valign=Gtk.Align.END,
                                  margin_bottom=8, margin_end=8)
        for box in (self.top_end, self.top_start, self.bottom_end):
            self.overlay.add_overlay(box)

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["wall-card"])
        self.box.append(self.overlay)
        self.box.append(Gtk.Label(label=title, xalign=0, ellipsize=3, max_width_chars=1,
                                  hexpand=True, css_classes=["wall-title"], tooltip_text=title))
        if meta:
            self.box.append(Gtk.Label(label=meta, xalign=0, ellipsize=3, max_width_chars=1,
                                      css_classes=["wall-meta", "dim-label", "caption"]))
        self.set_child(self.box)

        self.animator = Animator(self.picture)
        # weakref: um lambda com self no sinal de um filho do próprio card forma um ciclo
        # que passa pelo GObject e o coletor do Python não enxerga; cada página da
        # Oficina vista ficava inteira na memória
        ref = weakref.ref(self)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: ref() and ref()._hover(True))
        motion.connect("leave", lambda *_: ref() and ref()._hover(False))
        self.add_controller(motion)

    @staticmethod
    def badge(text, *classes):
        return Gtk.Label(label=text, css_classes=["badge", *classes])

    def set_image(self, path):
        self.gif = path if path and path.lower().endswith(".gif") else None
        if path:
            load_thumb(path, self._set_texture)
        self._update_gif()

    def _set_texture(self, texture):
        self.texture = texture
        if not self.animator.playing:
            self.picture.set_paintable(texture)

    def do_unmap(self):
        # card saiu da tela (trocou de página, filtro ou recarregou): o GIF para de vez.
        # Sem isso os timers dos cards removidos continuavam rodando pra sempre.
        self.wanted = self.hovered = False
        self.animator.stop(self.texture)
        Gtk.FlowBoxChild.do_unmap(self)

    def _hover(self, on):
        self.hovered = on
        self._update_gif()

    def want_gif(self, on):
        if on != self.wanted:
            self.wanted = on
            self._update_gif()

    def _update_gif(self):
        if self.gif and Animator.enabled and (self.hovered or self.wanted):
            self.animator.play(self.gif)
        elif self.animator.playing:
            self.animator.stop(self.texture)


class GifDriver:
    """'GIFs sempre ligados': anima só os cards que estão na tela (no máximo
    MAX_PLAYING), e para tudo com a janela em segundo plano (se configurado)."""

    MAX_PLAYING = 12

    def __init__(self, window, settings):
        self.window = window
        self.settings = settings
        self.views = []          # (scroller, flowbox, ativo?)
        self._source = None
        window.connect("notify::is-active", lambda *_: self.update())
        settings.connect("changed", lambda _s, key: key.startswith("gif") and self.update())

    def add(self, scroller, grid, is_active):
        self.views.append((scroller, grid, is_active))
        adj = scroller.get_vadjustment()
        adj.connect("value-changed", lambda *_: self.update())
        adj.connect("changed", lambda *_: self.update())

    def update(self):
        if self._source is None:
            self._source = GLib.timeout_add(120, self._run)

    def _run(self):
        self._source = None
        on = self.settings["gif_mode"] == 0 and (
            self.window.is_active() or not self.settings["gifs_pause_unfocused"])
        for scroller, grid, is_active in self.views:
            active = on and is_active()
            height = scroller.get_height()
            budget = self.MAX_PLAYING
            child = grid.get_first_child()
            while child:
                visible = False
                if active and budget > 0 and child.get_child_visible() and child.get_mapped():
                    ok, rect = child.compute_bounds(scroller)
                    if ok:
                        y = rect.get_y()
                        visible = y + rect.get_height() > 0 and y < height
                if visible and child.gif:
                    budget -= 1
                child.want_gif(visible)
                child = child.get_next_sibling()
        return False


# -- fundo da janela -----------------------------------------------------------------

class Glow(Gtk.Widget):
    """Luz ambiente com as cores do tema (gradientes radiais do CSS .glow, como no Elo).
    Medida zero: só preenche o que o conteúdo ocupar."""

    def __init__(self):
        super().__init__(css_classes=["glow"], can_target=False)

    def do_measure(self, orientation, for_size):
        return 0, 0, -1, -1


def _blurred(path, radius):
    """Fundo já desfocado (Pillow, numa thread), salvo no cache. Antes era um
    filter: blur() do CSS, refeito pela GPU a cada quadro da janela (pesava com GIFs)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = f"{path}:{st.st_mtime_ns}:{radius}:bg2"
    out = os.path.join(THUMBS, hashlib.sha1(key.encode()).hexdigest() + ".png")
    if not os.path.exists(out):
        try:
            from PIL import Image, ImageEnhance, ImageFilter
            with Image.open(path) as im:
                im.seek(0)
                im = im.convert("RGB")
                im.thumbnail((480, 480))
            # borda espelhada pro desfoque não escurecer os cantos
            pad = radius
            big = Image.new("RGB", (im.width + pad * 2, im.height + pad * 2))
            big.paste(im.resize(big.size), (0, 0))
            big.paste(im, (pad, pad))
            im = big.filter(ImageFilter.GaussianBlur(radius / 2)).crop(
                (pad, pad, pad + im.width, pad + im.height))
            im = ImageEnhance.Color(im).enhance(1.25)
            os.makedirs(THUMBS, exist_ok=True)
            im.save(out + ".part", "PNG")
            os.replace(out + ".part", out)
        except Exception:  # noqa: BLE001 - sem Pillow ou imagem ruim: sem fundo
            return None
    try:
        return Gdk.Texture.new_from_filename(out)
    except GLib.Error:
        return None


class Backdrop(Gtk.Picture):
    """Wallpaper selecionado atrás do app. Não pede tamanho nenhum (quem manda é o
    conteúdo) e troca de imagem com um fade."""

    def __init__(self):
        super().__init__(content_fit=Gtk.ContentFit.COVER, can_shrink=True,
                         css_classes=["app-backdrop"], can_target=False, opacity=0)
        self.key = None
        self._next = None
        self._swapping = False
        target = Adw.PropertyAnimationTarget.new(self, "opacity")
        self.anim = Adw.TimedAnimation.new(self, 0, 1, 260, target)
        self.anim.connect("done", self._faded)

    def do_measure(self, orientation, for_size):
        return 0, 0, -1, -1

    blur = 32
    strength = 0.6

    def show_image(self, key, path):
        """key identifica o wallpaper (não recarrega o mesmo); path None esconde."""
        if key == self.key:
            return
        self.key = key
        if not path:
            self._fade_to(None)
            return
        blur = self.blur
        _decode.submit(lambda: _idle(lambda tex, k=key: self.key == k and self._fade_to(tex),
                                     _blurred(path, blur)))

    def refresh(self):
        key, self.key = self.key, None
        return key

    def _fade_to(self, texture):
        self._next = texture
        if self.get_opacity() <= 0.01:
            self._swap()
            return
        self._swapping = True
        self.anim.set_value_from(self.get_opacity())
        self.anim.set_value_to(0)
        self.anim.play()

    def _faded(self, _anim):
        if self._swapping:
            self._swapping = False
            self._swap()

    def _swap(self):
        self.set_paintable(self._next)
        if self._next is not None:
            self.anim.set_value_from(0)
            self.anim.set_value_to(1)
            self.anim.play()
