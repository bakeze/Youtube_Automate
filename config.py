"""Configuration partagée : chemins, réglages utilisateur, historique, brouillon de file."""
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

VIDEO_FOLDER = BASE_DIR / "videos"
HISTORY_FILE = BASE_DIR / "uploads_history.json"
SETTINGS_FILE = BASE_DIR / "settings.json"
QUEUE_FILE = BASE_DIR / "queue_draft.json"
TOKEN_FILE = BASE_DIR / "token.json"
CLIENT_SECRET_FILE = BASE_DIR / "client_secret.json"

VIDEO_EXTENSIONS = (".mp4", ".mov", ".avi", ".mkv", ".webm")

SCOPE_UPLOAD = "https://www.googleapis.com/auth/youtube.upload"
SCOPE_READONLY = "https://www.googleapis.com/auth/youtube.readonly"

DEFAULT_SETTINGS = {
    "post_hour": 8,
    "post_minute": 30,
    "interval_days": 1,
    "similar_video_id": "yM_9izyJcys",
    "description_template": "#shorts\n\n\U0001F4FA Vidéo similaire: https://youtu.be/{similar_video_id}",
    "tags": ["shorts", "viral", "motivation"],
    "category_id": "22",
    "made_for_kids": False,
    "auto_title": True,
    "show_channel_info": False,
    "delete_after_upload": True,
}

CATEGORIES = [
    ("1", "Film & Animation"), ("2", "Autos & Véhicules"), ("10", "Musique"),
    ("15", "Animaux"), ("17", "Sport"), ("19", "Voyages & Événements"),
    ("20", "Gaming"), ("22", "Blogs & Personnes"), ("23", "Comédie"),
    ("24", "Divertissement"), ("25", "Actualités & Politique"),
    ("26", "Conseils & Style"), ("27", "Éducation"),
    ("28", "Science & Technologie"), ("29", "Associations & Activisme"),
]


def _read_json(path, fallback):
    if not os.path.exists(path):
        return fallback
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return fallback


def _write_json(path, data):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_settings():
    data = _read_json(SETTINGS_FILE, {})
    merged = dict(DEFAULT_SETTINGS)
    if isinstance(data, dict):
        merged.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    return merged


def save_settings(settings):
    current = load_settings()
    current.update({k: v for k, v in settings.items() if k in DEFAULT_SETTINGS})
    _write_json(SETTINGS_FILE, current)
    return current


def load_history():
    data = _read_json(HISTORY_FILE, {})
    return data if isinstance(data, dict) else {}


def save_history(history):
    _write_json(HISTORY_FILE, history)


def load_queue():
    data = _read_json(QUEUE_FILE, {})
    return data if isinstance(data, dict) else {}


def save_queue(queue):
    _write_json(QUEUE_FILE, queue)


def scopes_for(settings):
    """youtube.upload seul par défaut ; readonly en plus si l'utilisateur veut voir sa chaîne."""
    if settings.get("show_channel_info"):
        return [SCOPE_UPLOAD, SCOPE_READONLY]
    return [SCOPE_UPLOAD]


def build_description(settings):
    template = settings.get("description_template") or ""
    try:
        return template.format(similar_video_id=settings.get("similar_video_id", ""))
    except (KeyError, IndexError, ValueError):
        return template


VIDEO_FOLDER.mkdir(exist_ok=True)
