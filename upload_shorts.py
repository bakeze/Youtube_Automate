import os
import json
import datetime
import random
from tqdm import tqdm

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ⚙️ ========== CONFIGURATION - À MODIFIER ==========

VIDEO_FOLDER = "videos"

POST_HOUR = 8
POST_MINUTE = 30

# 👉 Option 1 : Date fixe (comme tu avais)
# START_DATE = datetime.date(2026, 4, 27)

# 👉 Option 2 : Commencer demain automatiquement (décommente si tu veux)
START_DATE = datetime.date.today() + datetime.timedelta(days=1)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
UPLOADS_HISTORY = "uploads_history.json"

# 🎬 Vidéo similaire/référencée lors de l'upload
SIMILAR_VIDEO_ID = "yM_9izyJcys"

# ========== FIN CONFIGURATION =========

SHORT_HOOKS = [
    "Personne ne t’a prévenu",
    "Tu fais encore ça ?",
    "Arrête ça maintenant",
    "Cette erreur te ruine tout",
    "On t’a menti là-dessus",
    "Regarde jusqu’à la fin",
    "Ce détail change tout",
]

SHORT_PAIN = [
    "ça te bloque",
    "tu perds du temps",
    "tu te sabotes",
    "ça ruine tes résultats",
    "ça t’empêche d’avancer",
]

SHORT_OPTIN = [
    "écoute bien",
    "regarde ça",
    "fais attention",
    "ne rate pas ça",
    "prends note",
]


def load_upload_history():
    if os.path.exists(UPLOADS_HISTORY):
        try:
            with open(UPLOADS_HISTORY, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}


def save_upload_history(history):
    with open(UPLOADS_HISTORY, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def is_video_uploaded(video_path):
    history = load_upload_history()
    filename = os.path.basename(video_path)
    return filename in history


def mark_video_uploaded(video_path, video_id, publish_datetime):
    history = load_upload_history()
    filename = os.path.basename(video_path)
    history[filename] = {
        "video_id": video_id,
        "uploaded_date": datetime.datetime.now().isoformat(),
        "scheduled_publish": publish_datetime.isoformat(),
        "status": "scheduled"
    }
    save_upload_history(history)


def authenticate_youtube():
    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
    credentials = flow.run_local_server(port=0)
    return build("youtube", "v3", credentials=credentials)


def get_videos_from_folder():
    return sorted([
        os.path.join(VIDEO_FOLDER, f)
        for f in os.listdir(VIDEO_FOLDER)
        if f.lower().endswith((".mp4", ".mov", ".avi", ".mkv"))
    ])


def clean_filename(filename):
    """Nettoie le nom du fichier pour utiliser comme thème"""
    name = os.path.splitext(os.path.basename(filename))[0]
    # Supprime les underscores et tirets
    name = name.replace("_", " ").replace("-", " ")
    # Enlève les espaces multiples
    name = " ".join(name.split()).strip()
    return name if name else "Motivation"


def sanitize_title(title):
    """
    Nettoie le titre de tous les caractères problématiques
    - Remplace les tirets spéciaux par des tirets normaux
    - Enlève les caractères spéciaux indésirables
    - Assure l'encodage UTF-8 valide et formatage fluide
    """
    if not title:
        return "Motivation"
    
    # Remplace les tirets spéciaux par un tiret normal
    title = title.replace("–", "-")  # em dash
    title = title.replace("—", "-")  # long dash
    title = title.replace("−", "-")  # minus sign
    title = title.replace("‐", "-")  # hyphen
    
    # Remplace les autres caractères spéciaux par des équivalents simples
    title = title.replace("•", "-")   # bullet point
    title = title.replace("·", "-")   # middle dot
    title = title.replace("»", "")    # guillemet fermant
    title = title.replace("«", "")    # guillemet ouvrant
    
    # Garde seulement les caractères acceptables pour YouTube
    # Lettres, chiffres, espaces, tirets, points, virgules, points d'exclamation, points d'interrogation et caractères accentués
    allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -.,!?éèêëàâäùûüôöœæç'")
    title = "".join(char for char in title if char in allowed_chars or ord(char) > 127)
    
    # Enlève les espaces multiples
    title = " ".join(title.split())
    
    # Nettoie les espaces autour des tirets et ponctuation
    title = title.replace(" - ", " - ").replace("  -  ", " - ")
    title = title.replace(" : ", " : ").replace("  :  ", " : ")
    title = title.replace(" , ", ", ").replace("  ,  ", ", ")
    
    # Première lettre majuscule
    title = title[0].upper() + title[1:] if len(title) > 0 else title
    
    # Limite à 100 caractères max (limite YouTube)
    title = title[:100].strip()
    
    if len(title) == 0:
        return "Motivation"
    
    return title


def generate_viral_title_cpo(filename):
    """Génère un titre viral aléatoire basé sur le nom du fichier"""
    try:
        theme = clean_filename(filename)
        if not theme or theme == "Motivation":
            # Si le nom n'est pas exploitable, titre simple
            hook = random.choice(SHORT_HOOKS)
            return sanitize_title(f"{hook} - Motivation")
        
        hook = random.choice(SHORT_HOOKS)
        pain = random.choice(SHORT_PAIN)
        optin = random.choice(SHORT_OPTIN)
        
        # Titre avec tiret normal (pas em dash)
        title = f"{hook} - {theme} : {pain}, {optin}"
        
        # Nettoie et valide le titre
        title = sanitize_title(title)
        
        # Vérification finale
        if not title or len(title.strip()) == 0:
            return "Motivation"
        
        return title
    except Exception as e:
        print(f"⚠️  Erreur génération titre: {e}")
        return "Motivation"


def upload_video_scheduled_with_progress(youtube, video_path, publish_datetime):
    """Upload une vidéo avec titre validé"""
    filename = os.path.basename(video_path)
    
    # Génère et valide le titre
    title = generate_viral_title_cpo(video_path)
    
    # ⚠️ VÉRIFICATION CRITIQUE: Le titre ne doit PAS être vide
    if not title or len(title.strip()) == 0:
        print(f"❌ ERREUR: Titre vide pour {filename}!")
        print(f"   Titre généré: '{title}'")
        return None
    
    print(f"📝 Titre validé: {title}")
    file_size = os.path.getsize(video_path)

    try:
        # Crée la description avec le lien de la vidéo similaire
        description = f"#shorts\n\n📺 Vidéo similaire: https://youtu.be/{SIMILAR_VIDEO_ID}"
        
        request = youtube.videos().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": title,  # ⚠️ DOIT être non-vide et valid UTF-8
                    "description": description,
                    "tags": ["shorts", "viral", "motivation"],
                    "categoryId": "22"
                },
                "status": {
                    "privacyStatus": "private",
                    "publishAt": publish_datetime.isoformat("T") + "Z",
                    "selfDeclaredMadeForKids": False
                }
            },
            media_body=MediaFileUpload(video_path, resumable=True)
        )

        response = None
        with tqdm(total=file_size, unit="B", unit_scale=True, desc=f"⬆️ {filename[:30]}") as pbar:
            while response is None:
                status, response = request.next_chunk()
                if status and status.resumable_progress:
                    pbar.update(int(status.resumable_progress) - pbar.n)

        if not response or "id" not in response:
            print(f"❌ Erreur upload: {filename}")
            return None

        video_id = response["id"]
        print(f"✅ Uploadée: {title}")
        print(f"📅 Planifiée pour: {publish_datetime.strftime('%Y-%m-%d %H:%M')}")
        mark_video_uploaded(video_path, video_id, publish_datetime)
        return video_id
        
    except Exception as e:
        print(f"❌ Erreur lors de l'upload de {filename}:")
        print(f"   {str(e)}")
        return None


def get_last_scheduled_date():
    """Récupère la dernière date de publication programmée + 1 jour"""
    history = load_upload_history()
    
    if not history:
        return START_DATE
    
    last_date = None
    for filename, data in history.items():
        if "scheduled_publish" in data:
            try:
                publish_date = datetime.datetime.fromisoformat(data["scheduled_publish"])
                if last_date is None or publish_date > last_date:
                    last_date = publish_date
            except:
                continue
    
    if last_date is None:
        return START_DATE
    
    # Retourne le jour suivant la dernière vidéo programmée
    next_date = (last_date + datetime.timedelta(days=1)).date()
    return next_date


def plan_uploads(youtube, videos):
    current_date = get_last_scheduled_date()
    uploaded_count = 0
    skipped_count = 0

    for video in videos:
        filename = os.path.basename(video)

        if is_video_uploaded(video):
            print(f"⏭️  SKIPPED: {filename} (déjà uploadée)")
            skipped_count += 1
            continue

        publish_datetime = datetime.datetime.combine(
            current_date,
            datetime.time(POST_HOUR, POST_MINUTE)
        )

        video_id = upload_video_scheduled_with_progress(youtube, video, publish_datetime)
        if video_id:
            uploaded_count += 1
        
        # Passer au jour suivant pour la prochaine vidéo
        current_date += datetime.timedelta(days=1)

    return uploaded_count, skipped_count


def main():
    youtube = authenticate_youtube()
    videos = get_videos_from_folder()

    print(f"\n🎥 {len(videos)} vidéos détectées")
    print(f"📤 Upload + planification (départ le {START_DATE.strftime('%d/%m/%Y')} à {POST_HOUR:02d}h{POST_MINUTE:02d})...\n")

    uploaded, skipped = plan_uploads(youtube, videos)

    print("\n✅ Résumé :")
    print(f"   📤 {uploaded} vidéo(s) uploadée(s)")
    print(f"   ⏭️  {skipped} vidéo(s) skippée(s) (déjà traitées)")
    print("\n🛌 Tu peux fermer le script, YouTube publiera automatiquement.")


if __name__ == "__main__":
    main()
