"""Authentification OAuth pilotée depuis l'interface + upload planifié."""
import os
import threading
import wsgiref.simple_server
import wsgiref.util
from datetime import timezone

# oauthlib refuse un redirect http:// : google_auth_oauthlib contourne en
# réécrivant l'URL en https au moment du fetch_token — on fait pareil plus bas.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from config import CLIENT_SECRET_FILE, TOKEN_FILE, SCOPE_READONLY

CHUNK_SIZE = 4 * 1024 * 1024  # 4 Mo -> progression fluide

_CALLBACK_TEMPLATE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Connexion YouTube</title>
<style>
 :root{color-scheme:light dark}
 body{margin:0;height:100vh;display:grid;place-items:center;
      font:15px/1.5 "Segoe UI",system-ui,sans-serif;background:#f6f7f9;color:#16181d}
 @media (prefers-color-scheme:dark){body{background:#0f1115;color:#e8eaed}}
 .card{text-align:center;padding:40px 48px;border-radius:16px;background:#fff;
       box-shadow:0 8px 32px rgba(0,0,0,.08)}
 @media (prefers-color-scheme:dark){.card{background:#171a21;box-shadow:none;border:1px solid #262a33}}
 .dot{width:52px;height:52px;border-radius:50%;background:__COLOR__;margin:0 auto 18px;
      display:grid;place-items:center;color:#fff;font-size:26px}
 p{color:#6b7280;margin:8px 0 0}
</style></head>
<body><div class="card"><div class="dot">__ICON__</div>
<h2 style="margin:0">__TITLE__</h2>
<p>__TEXT__</p></div>
<script>setTimeout(function(){window.close()},1400)</script>
</body></html>"""


def _page(color, icon, title, text):
    return (_CALLBACK_TEMPLATE
            .replace("__COLOR__", color).replace("__ICON__", icon)
            .replace("__TITLE__", title).replace("__TEXT__", text))


CALLBACK_OK = _page("#10b981", "&#10003;", "Compte connect&eacute;",
                    "Vous pouvez revenir &agrave; l'application.")
CALLBACK_KO = _page("#ef4444", "&#10005;", "Connexion annul&eacute;e",
                    "Refermez cette fen&ecirc;tre et r&eacute;essayez.")

_state = {"status": "idle", "error": None}
_lock = threading.Lock()


class _QuietHandler(wsgiref.simple_server.WSGIRequestHandler):
    def log_message(self, *args):  # pas de bruit dans la console
        pass


def auth_state():
    with _lock:
        return dict(_state)


def _set_state(status, error=None):
    with _lock:
        _state["status"] = status
        _state["error"] = error


def load_credentials(scopes):
    """Charge le token stocké et le rafraîchit si besoin. None si absent/périmé."""
    if not TOKEN_FILE.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), scopes)
    except (ValueError, OSError):
        return None

    if creds and set(scopes) - set(creds.scopes or []):
        return None  # les scopes demandés ont changé -> reconnexion requise

    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except RefreshError:
            return None
    return None


def logout():
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
    _set_state("idle")


def start_auth(scopes):
    """Démarre le flow OAuth et renvoie l'URL à ouvrir dans une popup.

    Un mini-serveur local à usage unique capte le retour de Google, écrit le
    token puis se ferme. L'interface suit l'avancement via auth_state().
    """
    if not CLIENT_SECRET_FILE.exists():
        raise FileNotFoundError("client_secret.json introuvable à la racine du projet.")

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), scopes)
    captured = {}

    def callback_app(environ, start_response):
        captured["uri"] = wsgiref.util.request_uri(environ)
        page = CALLBACK_KO if "error=" in captured["uri"] else CALLBACK_OK
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [page.encode("utf-8")]

    server = wsgiref.simple_server.make_server(
        "localhost", 0, callback_app, handler_class=_QuietHandler
    )
    server.timeout = 300
    flow.redirect_uri = "http://localhost:%d/" % server.server_port
    auth_url, _ = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true"
    )

    def worker():
        try:
            server.handle_request()
            uri = captured.get("uri")
            if not uri:
                _set_state("error", "Aucune réponse de Google (délai dépassé).")
                return
            if "error=" in uri:
                _set_state("error", "Autorisation refusée dans la fenêtre Google.")
                return
            flow.fetch_token(authorization_response=uri.replace("http://", "https://", 1))
            TOKEN_FILE.write_text(flow.credentials.to_json(), encoding="utf-8")
            _set_state("connected")
        except Exception as exc:  # noqa: BLE001 - le message est renvoyé à l'UI
            _set_state("error", str(exc))
        finally:
            try:
                server.server_close()
            except OSError:
                pass

    _set_state("pending")
    threading.Thread(target=worker, daemon=True).start()
    return auth_url


def build_service(creds):
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def fetch_channel(creds):
    """Infos de chaîne — nécessite le scope readonly, sinon renvoie None."""
    if SCOPE_READONLY not in (creds.scopes or []):
        return None
    try:
        service = build_service(creds)
        res = service.channels().list(part="snippet,statistics", mine=True).execute()
        items = res.get("items") or []
        if not items:
            return None
        snippet = items[0]["snippet"]
        stats = items[0].get("statistics", {})
        return {
            "title": snippet.get("title", ""),
            "thumbnail": (snippet.get("thumbnails", {}).get("default") or {}).get("url", ""),
            "subscribers": stats.get("subscriberCount"),
            "videos": stats.get("videoCount"),
        }
    except Exception:
        return None


def upload_scheduled(service, video_path, title, description, tags,
                     publish_local_dt, category_id="22", made_for_kids=False,
                     progress_cb=None, cancelled=None):
    """Upload une vidéo en privé avec publication planifiée.

    `publish_local_dt` est une datetime naïve dans le fuseau local de la
    machine ; elle est convertie en UTC car l'API attend publishAt en UTC.
    """
    title = (title or "").strip()
    if not title:
        raise ValueError("Le titre est vide.")

    publish_utc = publish_local_dt.astimezone(timezone.utc)

    body = {
        "snippet": {
            "title": title[:100],
            "description": description or "",
            "tags": [t for t in (tags or []) if t],
            "categoryId": str(category_id or "22"),
        },
        "status": {
            "privacyStatus": "private",  # obligatoire pour utiliser publishAt
            "publishAt": publish_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "selfDeclaredMadeForKids": bool(made_for_kids),
        },
    }

    media = MediaFileUpload(video_path, chunksize=CHUNK_SIZE, resumable=True,
                            mimetype="video/*")
    request = service.videos().insert(part="snippet,status", body=body,
                                      media_body=media)

    response = None
    while response is None:
        if cancelled is not None and cancelled():
            raise InterruptedError("Upload annulé.")
        status, response = request.next_chunk()
        if status and progress_cb:
            progress_cb(status.progress())

    if not response or "id" not in response:
        raise RuntimeError("Réponse inattendue de YouTube (pas d'identifiant vidéo).")

    if progress_cb:
        progress_cb(1.0)
    return response["id"], publish_utc
