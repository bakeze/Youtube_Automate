"""Génération et nettoyage des titres viraux (repris de upload_shorts.py)."""
import os
import random

SHORT_HOOKS = [
    "Personne ne t'a prévenu",
    "Tu fais encore ça ?",
    "Arrête ça maintenant",
    "Cette erreur te ruine tout",
    "On t'a menti là-dessus",
    "Regarde jusqu'à la fin",
    "Ce détail change tout",
]

SHORT_PAIN = [
    "ça te bloque",
    "tu perds du temps",
    "tu te sabotes",
    "ça ruine tes résultats",
    "ça t'empêche d'avancer",
]

SHORT_OPTIN = [
    "écoute bien",
    "regarde ça",
    "fais attention",
    "ne rate pas ça",
    "prends note",
]

MAX_TITLE_LENGTH = 100

_ALLOWED = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -.,!?éèêëàâäùûüôöœæç'"
)


def clean_filename(filename):
    """Nettoie le nom du fichier pour l'utiliser comme thème."""
    name = os.path.splitext(os.path.basename(filename))[0]
    name = name.replace("_", " ").replace("-", " ")
    name = " ".join(name.split()).strip()
    return name if name else "Motivation"


def sanitize_title(title):
    """Nettoie le titre de tous les caractères problématiques pour l'API YouTube."""
    if not title:
        return "Motivation"

    for bad, good in (("–", "-"), ("—", "-"), ("−", "-"), ("‐", "-"),
                      ("•", "-"), ("·", "-"), ("»", ""), ("«", ""),
                      ("<", ""), (">", "")):
        title = title.replace(bad, good)

    title = "".join(c for c in title if c in _ALLOWED or ord(c) > 127)
    title = " ".join(title.split())
    title = title.replace("  -  ", " - ").replace("  :  ", " : ").replace("  ,  ", ", ")

    if title:
        title = title[0].upper() + title[1:]

    title = title[:MAX_TITLE_LENGTH].strip()
    return title or "Motivation"


def generate_viral_title(filename):
    """Génère un titre viral aléatoire basé sur le nom du fichier."""
    try:
        theme = clean_filename(filename)
        hook = random.choice(SHORT_HOOKS)
        if not theme or theme == "Motivation":
            return sanitize_title(f"{hook} - Motivation")

        pain = random.choice(SHORT_PAIN)
        optin = random.choice(SHORT_OPTIN)
        return sanitize_title(f"{hook} - {theme} : {pain}, {optin}")
    except Exception:
        return "Motivation"
