"""YouTube Automate — serveur local ou distant + interface web.

Lancement :  python app.py

Variables d'environnement (toutes optionnelles) :
  YTA_HOST        interface d'écoute       (défaut 127.0.0.1 ; 0.0.0.0 pour un serveur)
  YTA_PORT        port d'écoute            (défaut 8787)
  YTA_PASSWORD    mot de passe d'accès     (obligatoire hors loopback)
  YTA_PUBLIC_URL  adresse publique https   (obligatoire hors loopback, pour le retour OAuth)
  YTA_NO_BROWSER  n'ouvre pas de navigateur au démarrage
"""
import hmac
import hashlib
import os
import secrets
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import config
import queue_manager as qm
import titles
import youtube_client as yt

if hasattr(sys.stdout, "reconfigure"):  # console Windows en cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
SECRET_FILE = BASE_DIR / ".session_secret"

HOST = os.environ.get("YTA_HOST", "127.0.0.1").strip()
PORT = int(os.environ.get("YTA_PORT", "8787"))
PASSWORD = os.environ.get("YTA_PASSWORD", "").strip()
PUBLIC_URL = os.environ.get("YTA_PUBLIC_URL", "").strip().rstrip("/")

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
IS_LOCAL = HOST in LOOPBACK
COOKIE_NAME = "yta_session"

app = FastAPI(title="YouTube Automate", docs_url=None, redoc_url=None)


# ------------------------------------------------------------ authentification

def _session_secret():
    """Secret stable entre deux redémarrages, pour ne pas invalider les sessions."""
    if SECRET_FILE.exists():
        return SECRET_FILE.read_bytes()
    value = secrets.token_bytes(32)
    SECRET_FILE.write_bytes(value)
    return value


SECRET = _session_secret()


def _session_token():
    return hmac.new(SECRET, PASSWORD.encode("utf-8"), hashlib.sha256).hexdigest()


def _oauth_redirect_uri():
    """None en local (retour loopback), URL de l'app en mode serveur."""
    return f"{PUBLIC_URL}/oauth/callback" if PUBLIC_URL else None


@app.middleware("http")
async def require_login(request: Request, call_next):
    if not PASSWORD:  # usage local sans mot de passe
        return await call_next(request)

    path = request.url.path
    if path in ("/login", "/api/login") or path.startswith("/static/"):
        return await call_next(request)

    if hmac.compare_digest(request.cookies.get(COOKIE_NAME, ""), _session_token()):
        return await call_next(request)

    if path.startswith("/api/"):
        return JSONResponse(status_code=401, content={"detail": "Session expirée."})
    return RedirectResponse("/login", status_code=303)


@app.get("/login")
def login_page():
    return FileResponse(STATIC_DIR / "login.html")


@app.post("/api/login")
def login(payload: dict = Body(...)):
    if not PASSWORD:
        return {"ok": True}
    if not hmac.compare_digest(str(payload.get("password", "")), PASSWORD):
        raise HTTPException(401, "Mot de passe incorrect.")

    response = JSONResponse({"ok": True})
    response.set_cookie(
        COOKIE_NAME, _session_token(), httponly=True, samesite="lax",
        secure=PUBLIC_URL.startswith("https://"), max_age=60 * 60 * 24 * 30,
    )
    return response

# ---------------------------------------------------------------- état du job

JOB_LOCK = threading.Lock()
JOB = {
    "running": False,
    "cancel": False,
    "total": 0,
    "done": 0,
    "failed": 0,
    "freed": 0,
    "current": None,
    "current_progress": 0.0,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "log": [],
}


def _job_update(**changes):
    with JOB_LOCK:
        JOB.update(changes)


def _job_log(filename, status, message="", video_id=None, publish_at=None):
    with JOB_LOCK:
        JOB["log"].append({
            "filename": filename,
            "status": status,
            "message": message,
            "video_id": video_id,
            "publish_at": publish_at,
            "at": datetime.now().isoformat(timespec="seconds"),
        })
        JOB["log"] = JOB["log"][-200:]


def _cancelled():
    with JOB_LOCK:
        return JOB["cancel"]


def _safe_video_path(filename):
    """Empêche toute sortie du dossier videos/."""
    name = os.path.basename(filename or "")
    path = (config.VIDEO_FOLDER / name).resolve()
    if path.parent != config.VIDEO_FOLDER.resolve() or not path.exists():
        raise HTTPException(404, "Fichier introuvable.")
    return path


def _friendly_error(exc):
    text = str(exc)
    if "quotaExceeded" in text or "uploadLimitExceeded" in text:
        return "Quota d'upload YouTube atteint. Réessayez dans 24 h."
    if "invalidPublishAt" in text:
        return "Date de publication refusée : elle doit être dans le futur."
    if "youtubeSignupRequired" in text:
        return "Ce compte Google n'a pas de chaîne YouTube."
    if "invalid_grant" in text:
        return "Session Google expirée. Reconnectez le compte."
    return text[:400]


def _run_job(filenames):
    settings = config.load_settings()
    scopes = config.scopes_for(settings)
    creds = yt.load_credentials(scopes)
    if creds is None:
        _job_update(running=False, error="Compte non connecté.",
                    finished_at=datetime.now().isoformat(timespec="seconds"))
        return

    try:
        service = yt.build_service(creds)
    except Exception as exc:  # noqa: BLE001
        _job_update(running=False, error=_friendly_error(exc),
                    finished_at=datetime.now().isoformat(timespec="seconds"))
        return

    delete_after = bool(settings.get("delete_after_upload"))
    items = [i for i in qm.as_list()
             if i.get("enabled", True) and i.get("exists")
             and (not filenames or i["filename"] in filenames)]

    _job_update(total=len(items), done=0, failed=0, freed=0, error=None)

    for item in items:
        if _cancelled():
            _job_log(item["filename"], "cancelled", "Annulé avant l'envoi.")
            continue

        name = item["filename"]
        _job_update(current=name, current_progress=0.0)
        publish_dt = qm._parse_dt(item["publish_at"])

        try:
            if publish_dt is None:
                raise ValueError("Date de publication invalide.")
            if publish_dt <= datetime.now():
                raise ValueError("La date de publication est déjà passée.")

            video_id, publish_utc = yt.upload_scheduled(
                service,
                str(config.VIDEO_FOLDER / name),
                item["title"],
                item.get("description", ""),
                item.get("tags", []),
                publish_dt,
                category_id=settings.get("category_id", "22"),
                made_for_kids=settings.get("made_for_kids", False),
                progress_cb=lambda p: _job_update(current_progress=round(p, 4)),
                cancelled=_cancelled,
            )

            # L'historique est écrit AVANT toute suppression : si le nettoyage
            # échoue, la vidéo reste malgré tout marquée comme envoyée.
            history = config.load_history()
            history[name] = {
                "video_id": video_id,
                "title": item["title"],
                "uploaded_date": datetime.now().isoformat(),
                "scheduled_publish": publish_dt.strftime(qm.ISO_MINUTES),
                "scheduled_publish_utc": publish_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "scheduled",
                "size": item.get("size", 0),
            }
            config.save_history(history)

            freed = 0
            if delete_after:
                try:
                    qm.remove(name, delete_file=True)
                    freed = item.get("size", 0)
                    history[name]["file_deleted"] = True
                    config.save_history(history)
                except OSError as exc:
                    qm.remove(name)
                    _job_log(name, "warning",
                             f"Vidéo envoyée, mais le fichier n'a pas pu être supprimé : {exc}")
            else:
                qm.remove(name)

            with JOB_LOCK:
                JOB["done"] += 1
                JOB["freed"] += freed
            _job_log(name, "ok", item["title"], video_id, item["publish_at"])

        except InterruptedError:
            _job_log(name, "cancelled", "Upload interrompu.")
        except Exception as exc:  # noqa: BLE001
            with JOB_LOCK:
                JOB["failed"] += 1
            _job_log(name, "error", _friendly_error(exc))

    _job_update(running=False, current=None, current_progress=0.0,
                finished_at=datetime.now().isoformat(timespec="seconds"))


# ------------------------------------------------------------------- routes

@app.get("/api/state")
def get_state():
    settings = config.load_settings()
    scopes = config.scopes_for(settings)
    creds = yt.load_credentials(scopes)

    account = {"connected": creds is not None, "channel": None,
               "flow": yt.auth_state(),
               "client_secret": config.CLIENT_SECRET_FILE.exists()}
    if creds is not None and settings.get("show_channel_info"):
        account["channel"] = yt.fetch_channel(creds)

    qm.sync(settings)
    with JOB_LOCK:
        job = dict(JOB)

    return {
        "account": account,
        "settings": settings,
        "categories": [{"id": cid, "label": label} for cid, label in config.CATEGORIES],
        "queue": qm.as_list(),
        "history": qm.history_list(),
        "job": job,
        "next_slot": qm.next_free_slot(settings).strftime(qm.ISO_MINUTES),
        "now": datetime.now().strftime(qm.ISO_MINUTES),
    }


@app.post("/api/auth/start")
def auth_start():
    settings = config.load_settings()
    try:
        url = yt.start_auth(config.scopes_for(settings), _oauth_redirect_uri())
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, _friendly_error(exc))
    return {"auth_url": url}


@app.get("/oauth/callback")
def oauth_callback(request: Request):
    """Retour de Google en mode serveur (YTA_PUBLIC_URL défini)."""
    params = request.query_params
    if params.get("error"):
        return HTMLResponse(yt.CALLBACK_KO)

    # Google renvoie sur l'URL publique ; c'est elle qui doit servir à l'échange.
    response_url = f"{PUBLIC_URL}/oauth/callback?{request.url.query}"
    try:
        yt.finish_auth(response_url, params.get("state", ""))
    except Exception:  # noqa: BLE001 - le détail est déjà dans auth_state()
        return HTMLResponse(yt.CALLBACK_KO, status_code=400)
    return HTMLResponse(yt.CALLBACK_OK)


@app.get("/api/auth/status")
def auth_status():
    settings = config.load_settings()
    creds = yt.load_credentials(config.scopes_for(settings))
    return {"connected": creds is not None, "flow": yt.auth_state()}


@app.post("/api/auth/logout")
def auth_logout():
    yt.logout()
    return {"ok": True}


@app.get("/api/settings")
def get_settings():
    return config.load_settings()


@app.post("/api/settings")
def post_settings(payload: dict = Body(...)):
    clean = {}
    if "post_hour" in payload:
        clean["post_hour"] = max(0, min(23, int(payload["post_hour"])))
    if "post_minute" in payload:
        clean["post_minute"] = max(0, min(59, int(payload["post_minute"])))
    if "interval_days" in payload:
        clean["interval_days"] = max(1, min(365, int(payload["interval_days"])))
    for key in ("similar_video_id", "description_template", "category_id"):
        if key in payload:
            clean[key] = str(payload[key])
    if "tags" in payload:
        raw = payload["tags"]
        if isinstance(raw, str):
            raw = [t.strip() for t in raw.split(",")]
        clean["tags"] = [t for t in (raw or []) if t][:30]
    for key in ("made_for_kids", "auto_title", "show_channel_info",
                "delete_after_upload"):
        if key in payload:
            clean[key] = bool(payload[key])
    return config.save_settings(clean)


@app.post("/api/queue/refresh")
def queue_refresh():
    qm.sync()
    return {"queue": qm.as_list()}


@app.post("/api/queue/item/{filename}")
def queue_update(filename: str, payload: dict = Body(...)):
    try:
        item = qm.update_item(filename, payload)
    except KeyError:
        raise HTTPException(404, "Vidéo absente de la file.")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return item


@app.post("/api/queue/item/{filename}/title")
def queue_regen_title(filename: str):
    try:
        return {"title": qm.regenerate_title(filename)}
    except KeyError:
        raise HTTPException(404, "Vidéo absente de la file.")


@app.post("/api/queue/reorder")
def queue_reorder(payload: dict = Body(...)):
    qm.reorder(payload.get("filenames") or [])
    return {"queue": qm.as_list()}


@app.post("/api/queue/reschedule")
def queue_reschedule(payload: dict = Body(...)):
    settings = config.load_settings()
    try:
        qm.reschedule(
            payload.get("start_date"),
            payload.get("hour", settings["post_hour"]),
            payload.get("minute", settings["post_minute"]),
            payload.get("interval_days", settings["interval_days"]),
            only_enabled=payload.get("only_enabled", True),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"queue": qm.as_list()}


@app.delete("/api/queue/item/{filename}")
def queue_remove(filename: str, delete_file: bool = False):
    if delete_file:
        _safe_video_path(filename)
    qm.remove(filename, delete_file=delete_file)
    return {"ok": True}


@app.post("/api/videos/upload")
async def videos_upload(files: list[UploadFile] = File(...)):
    saved, rejected = [], []
    for upload in files:
        name = os.path.basename(upload.filename or "")
        if not name or not name.lower().endswith(config.VIDEO_EXTENSIONS):
            rejected.append(upload.filename)
            continue

        target = config.VIDEO_FOLDER / name
        stem, suffix = os.path.splitext(name)
        counter = 1
        while target.exists():
            target = config.VIDEO_FOLDER / f"{stem} ({counter}){suffix}"
            counter += 1

        with open(target, "wb") as out:
            while chunk := await upload.read(1024 * 1024):
                out.write(chunk)
        saved.append(target.name)

    qm.sync()
    return {"saved": saved, "rejected": rejected, "queue": qm.as_list()}


@app.get("/api/videos/{filename}")
def video_file(filename: str):
    path = _safe_video_path(filename)
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/job")
def job_state():
    with JOB_LOCK:
        return dict(JOB)


@app.post("/api/job/start")
def job_start(payload: dict = Body(default={})):
    with JOB_LOCK:
        if JOB["running"]:
            raise HTTPException(409, "Un envoi est déjà en cours.")
        JOB.update({"running": True, "cancel": False, "total": 0, "done": 0,
                    "failed": 0, "freed": 0, "current": None, "current_progress": 0.0,
                    "error": None, "log": [],
                    "started_at": datetime.now().isoformat(timespec="seconds"),
                    "finished_at": None})

    filenames = payload.get("filenames") or []
    threading.Thread(target=_run_job, args=(filenames,), daemon=True).start()
    return {"ok": True}


@app.post("/api/job/cancel")
def job_cancel():
    _job_update(cancel=True)
    return {"ok": True}


@app.post("/api/preview-title")
def preview_title(payload: dict = Body(...)):
    return {"title": titles.generate_viral_title(payload.get("filename", "video.mp4"))}


@app.delete("/api/history/{filename}")
def history_remove(filename: str):
    history = config.load_history()
    if filename in history:
        del history[filename]
        config.save_history(history)
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(Exception)
def unhandled(request, exc):  # noqa: ARG001
    return JSONResponse(status_code=500, content={"detail": _friendly_error(exc)})


def _check_public_setup():
    """Refuse d'exposer l'application sans mot de passe ni retour OAuth valide."""
    problems = []
    if not PASSWORD:
        problems.append(
            "YTA_PASSWORD n'est pas défini : n'importe qui atteignant le port "
            "pourrait publier sur votre chaîne YouTube."
        )
    if not PUBLIC_URL:
        problems.append(
            "YTA_PUBLIC_URL n'est pas défini : Google ne saurait pas où renvoyer "
            "l'utilisateur après l'autorisation, la connexion échouerait."
        )
    elif not PUBLIC_URL.startswith("https://"):
        problems.append(
            f"YTA_PUBLIC_URL vaut « {PUBLIC_URL} » : Google exige https:// en "
            "dehors de localhost."
        )

    if problems:
        print("\n  Démarrage refusé — l'application écouterait sur "
              f"{HOST} sans configuration sûre :\n", flush=True)
        for problem in problems:
            print(f"    - {problem}", flush=True)
        print("\n  Voir la section « Mise en ligne sur un serveur » du README.\n", flush=True)
        sys.exit(1)


def main():
    import uvicorn

    if not IS_LOCAL:
        _check_public_setup()

    shown = PUBLIC_URL or f"http://{HOST}:{PORT}"
    print(f"\n  YouTube Automate\n  Interface : {shown}", flush=True)
    if PASSWORD:
        print("  Accès protégé par mot de passe", flush=True)
    if config.load_settings().get("delete_after_upload"):
        print("  Les fichiers sont supprimés après envoi réussi", flush=True)
    print("  (Ctrl+C pour arrêter)\n", flush=True)

    if IS_LOCAL and not os.environ.get("YTA_NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(shown)).start()

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
