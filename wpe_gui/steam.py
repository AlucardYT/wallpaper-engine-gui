"""Steam Workshop sem chave de API.

- Busca: a página de navegação da Workshop vem renderizada no servidor e traz o
  resultado inteiro em JSON (react-query) dentro de #valve-ssr-data.
- Descrição completa: ISteamRemoteStorage/GetPublishedFileDetails (aberto).
- Inscrever/cancelar: IPublishedFileService/Subscribe com o token do login da Steam,
  que é o JWT dentro do cookie steamLoginSecure ("<steamid>||<jwt>"). Testado em
  2026-10-17. Ler a lista de inscritos/GetDetails com esse token dá 401 (pede chave)."""

import base64
import json
import os
import re
import time
import urllib.parse
import urllib.request

APPID = 431960
BROWSE = "https://steamcommunity.com/workshop/browse/"
DETAILS = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
SERVICE = "https://api.steampowered.com/IPublishedFileService/{}/v1/"
PROFILE = "https://steamcommunity.com/profiles/{}/?xml=1"
ITEM_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id={}"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) wallpaper-engine-gui"
STEAM = os.path.expanduser("~/.local/share/Steam/steamapps/workshop")
DOWNLOADS = os.path.join(STEAM, "downloads", str(APPID))

SORTS = [("trend", "Em alta"), ("totaluniquesubscribers", "Mais inscritos"),
         ("toprated", "Mais bem avaliados"), ("mostrecent", "Mais recentes")]
PERIODS = [(1, "Hoje"), (7, "Semana"), (30, "Mês"), (90, "3 meses"), (180, "6 meses"),
           (365, "Ano"), (-1, "Sempre")]
RATINGS = [(None, "Todas as idades"), ("Everyone", "Livre"), ("Questionable", "Questionável"),
           ("Mature", "Adulto")]
RATING_NAMES = {"Everyone": "Livre", "Questionable": "Questionável", "Mature": "Adulto"}
TYPES = [("both", "Cenas e vídeos"), ("Scene", "Cenas"), ("Video", "Vídeos")]
GENRES = ["Abstract", "Animal", "Anime", "Cartoon", "CGI", "Cyberpunk", "Fantasy", "Game",
          "Girls", "Guys", "Landscape", "Medieval", "Memes", "MMD", "Music", "Nature",
          "Pixel art", "Relaxing", "Retro", "Sci-Fi", "Sports", "Technology", "Television",
          "Vehicle", "Unspecified"]
# tags que não são gênero nem tipo, escondidas nos detalhes
HIDDEN_TAGS = {"Wallpaper", "Scene", "Video", *RATING_NAMES}


class SteamError(Exception):
    pass


class AuthError(SteamError):
    pass


def _get(url, data=None, timeout=20):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AuthError("login da Steam expirou") from e
        raise SteamError(f"Steam respondeu {e.code}") from e
    except OSError as e:
        raise SteamError("sem conexão com a Steam") from e


class Result:
    __slots__ = ("id", "title", "preview_url", "tags", "type", "rating", "subscriptions", "stars",
                 "votes", "size", "updated", "summary", "author")

    def __init__(self, raw, authors):
        self.id = str(raw["publishedfileid"])
        self.title = (raw.get("title") or self.id).strip()
        self.preview_url = raw.get("preview_url") or ""
        self.tags = [t.get("tag", "") for t in raw.get("tags") or []]
        self.type = "video" if "Video" in self.tags else "scene" if "Scene" in self.tags else ""
        self.rating = next((RATING_NAMES[t] for t in self.tags if t in RATING_NAMES), "")
        self.subscriptions = int(raw.get("subscriptions") or 0)
        self.stars = int(raw.get("star_rating") or 0)
        self.votes = int(raw.get("total_votes") or 0)
        self.size = int(raw.get("file_size") or 0)
        self.updated = int(raw.get("time_updated") or 0)
        self.summary = (raw.get("short_description") or "").strip()
        self.author = authors.get(str(raw.get("creator")), "")

    @property
    def type_name(self):
        return {"scene": "Cena", "video": "Vídeo"}.get(self.type, "Outro")

    @property
    def url(self):
        return ITEM_URL.format(self.id)


def browse(text="", sort="trend", days=7, kind="both", genre=None, rating=None, page=1):
    """Uma página (30 itens) da Workshop. Devolve (resultados, total de páginas)."""
    params = {"appid": APPID, "section": "readytouseitems", "p": page,
              "browsesort": "textsearch" if text and sort == "trend" else sort,
              "requiredtags[]": ["Wallpaper"]}
    if text:
        params["searchtext"] = text
    if sort == "trend":
        params["days"] = days
    if kind == "both":
        # o linux-wallpaperengine não roda web nem aplicativo
        params["excludedtags[]"] = ["Web", "Application"]
    else:
        params["requiredtags[]"].append(kind)
    if genre:
        params["requiredtags[]"].append(genre)
    if rating:
        params["requiredtags[]"].append(rating)
    html = _get(BROWSE + "?" + urllib.parse.urlencode(params, doseq=True))
    m = re.search(r'<script type="application/json" id="valve-ssr-data"[^>]*>(.*?)</script>',
                  html, re.S)
    if not m:
        raise SteamError("a página da Workshop mudou de formato")
    try:
        queries = json.loads(json.loads(m.group(1))["renderContext"]["queryData"])["queries"]
        data = next(q["state"]["data"] for q in queries if q["queryKey"][0] == "workshop_browse")
    except (KeyError, ValueError, StopIteration, TypeError) as e:
        raise SteamError("a página da Workshop mudou de formato") from e
    if not data:
        return [], 0
    authors = {}
    for d in data.get("creator_player_link_details") or []:
        pub = d.get("public_data") or {}
        authors[str(pub.get("steamid"))] = pub.get("persona_name") or ""
    results = [Result(r, authors) for r in data.get("results") or []]
    return results, int(data.get("total_pages") or 0)


def description(item_id):
    body = urllib.parse.urlencode({"itemcount": 1, "publishedfileids[0]": item_id}).encode()
    data = json.loads(_get(DETAILS, body))
    try:
        return data["response"]["publishedfiledetails"][0].get("description") or ""
    except (KeyError, IndexError):
        return ""


def subscribe(token, item_id, on=True):
    """notify_client faz o cliente da Steam (se aberto) baixar/remover na hora."""
    url = SERVICE.format("Subscribe" if on else "Unsubscribe") + "?access_token=" + \
        urllib.parse.quote(token)
    body = urllib.parse.urlencode({"publishedfileid": item_id, "list_type": 1,
                                   "appid": APPID, "notify_client": 1}).encode()
    req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            eresult = res.headers.get("x-eresult", "1")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AuthError("login da Steam expirou") from e
        raise SteamError(f"Steam respondeu {e.code}") from e
    except OSError as e:
        raise SteamError("sem conexão com a Steam") from e
    if eresult != "1":
        raise SteamError(f"a Steam recusou (código {eresult})")


def profile(steamid):
    """Nome e avatar públicos (XML do perfil, sem chave)."""
    try:
        xml = _get(PROFILE.format(steamid), timeout=10)
    except SteamError:
        return "", ""
    name = re.search(r"<steamID><!\[CDATA\[(.*?)\]\]>", xml, re.S)
    avatar = re.search(r"<avatarMedium><!\[CDATA\[(.*?)\]\]>", xml, re.S)
    return (name.group(1) if name else ""), (avatar.group(1) if avatar else "")


def parse_login_cookie(value):
    """steamLoginSecure → (steamid, token, expira_em). None se inválido/expirado."""
    value = urllib.parse.unquote(value or "")
    steamid, sep, token = value.partition("||")
    if not sep or not token:
        return None
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return None
    exp = int(claims.get("exp") or 0)
    if exp and exp < time.time() + 60:
        return None
    return steamid, token, exp


def downloading(item_id):
    return os.path.isdir(os.path.join(DOWNLOADS, item_id))


def compact(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} mi".replace(".", ",")
    if n >= 1_000:
        return f"{n // 1000} mil"
    return str(n)


def size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
