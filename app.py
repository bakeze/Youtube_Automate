"""YouTube Automate — serveur local + interface web.

Lancement :  python app.py
"""
import os
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import config
import queue_manager as qm
import titles
import youtube_client as yt

if hasattr(sys.stdout, "reconfigure"):  # console Windows en cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STATIC_DIR = Path(__file__).resolve().parent / "static"
HOST = "127.0.0.1"
PORT = int(os.environ.get("YTA_PORT", "8787"))

app = FastAPI(title="YouTube Automate", docs_url=None, redoc_url=None)

# ---------------------------------------------------------------- état du job

JOB_LOCK = threading.Lock()
JOB = {
    "running": False,
    "cancel": False,
    "total": 0,
    "done": 0,
    "failed": 0,
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

    items = [i for i in qm.as_list()
             if i.get("enabled", True) and i.get("exists")
             and (not filenames or i["filename"] in filenames)]

    _job_update(total=len(items), done=0, failed=0, error=None)

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

            history = config.load_history()
            history[name] = {
                "video_id": video_id,
                "title": item["title"],
                "uploaded_date": datetime.now().isoformat(),
                "scheduled_publish": publish_dt.strftime(qm.ISO_MINUTES),
                "scheduled_publish_utc": publish_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "scheduled",
            }
            config.save_history(history)
            qm.remove(name)

            with JOB_LOCK:
                JOB["done"] += 1
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
        url = yt.start_auth(config.scopes_for(settings))
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, _friendly_error(exc))
    return {"auth_url": url}


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
    for key in ("made_for_kids", "auto_title", "show_channel_info"):
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
                    "failed": 0, "current": None, "current_progress": 0.0,
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


def main():
    import uvicorn

    url = f"http://{HOST}:{PORT}"
    print(f"\n  YouTube Automate\n  Interface : {url}\n  (Ctrl+C pour arrêter)\n", flush=True)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
