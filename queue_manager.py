"""File d'attente : synchronisation avec le dossier videos/ et calcul des dates."""
import os
from datetime import date, datetime, time, timedelta

import config
import titles

ISO_MINUTES = "%Y-%m-%dT%H:%M:%S"


def _parse_dt(value):
    """Parse une date ISO (naïve ou avec offset) en datetime naïve locale."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _fmt(dt):
    return dt.strftime(ISO_MINUTES)


def list_video_files():
    if not config.VIDEO_FOLDER.exists():
        return []
    return sorted(
        p.name for p in config.VIDEO_FOLDER.iterdir()
        if p.is_file() and p.suffix.lower() in config.VIDEO_EXTENSIONS
    )


def last_scheduled_datetime(queue=None, history=None):
    """Dernière date occupée, en tenant compte de l'historique ET de la file."""
    history = config.load_history() if history is None else history
    queue = config.load_queue() if queue is None else queue

    latest = None
    for entry in history.values():
        dt = _parse_dt(entry.get("scheduled_publish"))
        if dt and (latest is None or dt > latest):
            latest = dt
    for item in queue.values():
        dt = _parse_dt(item.get("publish_at"))
        if dt and (latest is None or dt > latest):
            latest = dt
    return latest


def next_free_slot(settings, queue=None, history=None):
    """Prochain créneau libre : lendemain de la dernière date, à l'heure réglée."""
    slot_time = time(int(settings["post_hour"]), int(settings["post_minute"]))
    step = max(1, int(settings.get("interval_days", 1)))
    latest = last_scheduled_datetime(queue, history)

    if latest is None:
        return datetime.combine(date.today() + timedelta(days=1), slot_time)
    return datetime.combine(latest.date() + timedelta(days=step), slot_time)


def _new_item(filename, settings, order, publish_at):
    return {
        "filename": filename,
        "order": order,
        "enabled": True,
        "title": titles.generate_viral_title(filename) if settings.get("auto_title")
                 else titles.clean_filename(filename),
        "description": config.build_description(settings),
        "tags": list(settings.get("tags", [])),
        "publish_at": _fmt(publish_at),
    }


def sync(settings=None):
    """Aligne le brouillon de file sur le contenu réel du dossier videos/."""
    settings = settings or config.load_settings()
    queue = config.load_queue()
    history = config.load_history()
    files = list_video_files()

    # Retire les entrées dont le fichier a disparu ou qui sont déjà uploadées.
    queue = {
        name: item for name, item in queue.items()
        if name in files and name not in history
    }

    next_order = max((item.get("order", 0) for item in queue.values()), default=-1) + 1

    for name in files:
        if name in history or name in queue:
            continue
        slot = next_free_slot(settings, queue, history)
        queue[name] = _new_item(name, settings, next_order, slot)
        next_order += 1

    config.save_queue(queue)
    return queue


def as_list(queue=None):
    """File triée, enrichie des infos fichier, prête pour l'interface."""
    queue = config.load_queue() if queue is None else queue
    items = []
    for name, item in queue.items():
        path = config.VIDEO_FOLDER / name
        size = path.stat().st_size if path.exists() else 0
        items.append({**item, "filename": name, "size": size, "exists": path.exists()})
    items.sort(key=lambda i: (i.get("order", 0), i["filename"]))
    for index, item in enumerate(items):
        item["order"] = index
    return items


def update_item(filename, changes):
    queue = config.load_queue()
    if filename not in queue:
        raise KeyError(filename)

    item = queue[filename]
    if "title" in changes:
        item["title"] = titles.sanitize_title(changes["title"])
    if "description" in changes:
        item["description"] = str(changes["description"])[:5000]
    if "tags" in changes:
        raw = changes["tags"]
        if isinstance(raw, str):
            raw = [t.strip() for t in raw.split(",")]
        item["tags"] = [t for t in (raw or []) if t][:30]
    if "enabled" in changes:
        item["enabled"] = bool(changes["enabled"])
    if "publish_at" in changes:
        dt = _parse_dt(changes["publish_at"])
        if dt is None:
            raise ValueError("Date de publication invalide.")
        item["publish_at"] = _fmt(dt)

    config.save_queue(queue)
    return item


def regenerate_title(filename):
    queue = config.load_queue()
    if filename not in queue:
        raise KeyError(filename)
    queue[filename]["title"] = titles.generate_viral_title(filename)
    config.save_queue(queue)
    return queue[filename]["title"]


def reorder(filenames):
    queue = config.load_queue()
    for index, name in enumerate(filenames):
        if name in queue:
            queue[name]["order"] = index
    config.save_queue(queue)
    return queue


def reschedule(start_date_str, hour, minute, interval_days, only_enabled=True):
    """Réattribue les dates en cascade à partir d'une date de départ."""
    start = _parse_dt(start_date_str)
    if start is None:
        try:
            start = datetime.combine(date.fromisoformat(start_date_str), time(0, 0))
        except (ValueError, TypeError):
            raise ValueError("Date de début invalide.")

    slot_time = time(int(hour), int(minute))
    step = max(1, int(interval_days))
    current = datetime.combine(start.date(), slot_time)

    queue = config.load_queue()
    for item in as_list(queue):
        name = item["filename"]
        if only_enabled and not item.get("enabled", True):
            continue
        queue[name]["publish_at"] = _fmt(current)
        current += timedelta(days=step)

    config.save_queue(queue)
    return queue


def remove(filename, delete_file=False):
    queue = config.load_queue()
    queue.pop(filename, None)
    config.save_queue(queue)
    if delete_file:
        path = config.VIDEO_FOLDER / filename
        if path.exists() and path.parent == config.VIDEO_FOLDER:
            os.remove(path)


def history_list():
    """Historique trié par date de publication programmée, le plus récent d'abord."""
    history = config.load_history()
    rows = []
    for name, entry in history.items():
        scheduled = _parse_dt(entry.get("scheduled_publish"))
        rows.append({
            "filename": name,
            "video_id": entry.get("video_id"),
            "title": entry.get("title"),
            "uploaded_date": entry.get("uploaded_date"),
            "scheduled_publish": entry.get("scheduled_publish"),
            "published": bool(scheduled and scheduled <= datetime.now()),
            "status": entry.get("status", "scheduled"),
        })
    rows.sort(key=lambda r: r.get("scheduled_publish") or "", reverse=True)
    return rows
