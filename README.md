# YouTube Automate

Envoyer et programmer des Shorts sur YouTube depuis une interface simple, sur votre machine.

---

## Démarrage

### 1. Récupérer le projet

Il faut [Python 3.10 ou plus](https://www.python.org/downloads/) et [Git](https://git-scm.com/downloads).

```bash
git clone https://github.com/<votre-compte>/Youtube_Automate.git
cd Youtube_Automate
pip install -r requirements.txt
```

### 2. Obtenir votre fichier `client_secret.json`

C'est la seule étape un peu longue, et elle ne se fait **qu'une fois**. Google exige que
chacun utilise ses propres identifiants pour publier sur sa chaîne.

1. Ouvrez [console.cloud.google.com](https://console.cloud.google.com/) et connectez-vous
   avec le compte Google **propriétaire de la chaîne YouTube**.
2. En haut, créez un projet (nom libre, par exemple `youtube-automate`).
3. Activez l'API : cherchez **YouTube Data API v3** dans la barre de recherche, ouvrez-la,
   cliquez sur **Activer**.
4. Menu de gauche → **API et services** → **Écran de consentement OAuth**
   (parfois appelé *Google Auth Platform*) :
   - type d'utilisateur : **Externe**, puis **Créer** ;
   - remplissez le nom de l'application et votre e-mail, enregistrez ;
   - dans **Audience** (ou *Utilisateurs test*), cliquez **Ajouter des utilisateurs** et
     saisissez votre propre adresse Gmail. ⚠️ Sans cela, la connexion sera refusée.
5. Menu de gauche → **Identifiants** → **Créer des identifiants** →
   **ID client OAuth** → type d'application : **Application de bureau** → **Créer**.
6. Cliquez sur **Télécharger le JSON**, renommez le fichier en `client_secret.json`
   et placez-le **à la racine du dossier du projet**, à côté de `app.py`.

### 3. Lancer

```bash
python app.py
```

Le navigateur s'ouvre automatiquement sur <http://127.0.0.1:8787>.
Pour arrêter : `Ctrl+C` dans le terminal.

> Sous Windows, vous pouvez aussi faire un clic droit sur `start.ps1` →
> **Exécuter avec PowerShell**.

### 4. Connecter sa chaîne

Cliquez sur **Connecter mon compte YouTube**. Une fenêtre Google s'ouvre, vous autorisez,
elle se referme toute seule. C'est fini — vous n'aurez plus à le refaire.

> Google affichera un avertissement « Cette application n'est pas validée » : c'est normal
> pour une application personnelle. Cliquez sur **Paramètres avancés** →
> **Accéder à … (non sécurisé)**.

---

## Utiliser l'application

| | |
|---|---|
| **Ajouter des vidéos** | Glissez vos fichiers sur la zone de dépôt. Ils sont copiés dans `videos/`. |
| **Tout programmer d'un coup** | Réglez la première publication, l'heure et l'intervalle, puis **Appliquer à la file**. |
| **Ajuster une vidéo** | Changez sa date et son heure directement sur sa ligne. Glissez les lignes pour changer l'ordre. |
| **Modifier le titre** | Cliquez dans le titre pour l'écrire vous-même, ou sur ↻ pour en générer un autre. |
| **Description et tags** | La flèche ⌄ à droite ouvre les champs détaillés. |
| **Exclure une vidéo** | Décochez-la : elle reste dans la liste mais ne sera pas envoyée. |
| **Envoyer** | Bouton bleu en bas. Les vidéos partent en privé, YouTube les publie tout seul aux dates prévues. |

L'onglet **Historique** liste tout ce qui a été envoyé, avec un lien vers YouTube Studio.
L'onglet **Réglages** contient les valeurs par défaut (heure, intervalle, tags, description…).

---

## Questions fréquentes

**Google me redemande de me connecter au bout de quelques jours.**
Tant que votre projet Google est en mode *Test*, l'autorisation expire après 7 jours.
Recliquez simplement sur **Connecter**. Pour éviter ça, passez le projet en *Production*
dans l'écran de consentement OAuth.

**« Le fichier client_secret.json est absent ».**
L'étape 2 n'est pas terminée, ou le fichier n'est pas au bon endroit : il doit être
directement dans le dossier du projet, à côté de `app.py`, avec exactement ce nom.

**« Accès bloqué : cette application n'a pas terminé la procédure de validation ».**
Votre adresse Gmail n'est pas dans la liste des utilisateurs test (étape 2.4).

**Le port 8787 est déjà utilisé.**
Lancez avec un autre port : `YTA_PORT=8788 python app.py`
(sous PowerShell : `$env:YTA_PORT=8788; python app.py`).

**Quota d'upload atteint.**
YouTube limite le nombre d'envois quotidiens par projet. Réessayez le lendemain.

**Je reprends le projet de quelqu'un d'autre et l'historique n'est pas le mien.**
`uploads_history.json` sert à ne jamais renvoyer deux fois la même vidéo. Pour repartir
de zéro, remplacez son contenu par `{}`.

**Puis-je utiliser plusieurs chaînes ?**
Oui, l'une après l'autre : Réglages → **Déconnecter**, puis reconnectez-vous avec l'autre
compte.

---

## À savoir

- **Heures** : vous saisissez l'heure de votre pays ; elle est convertie en UTC pour
  YouTube. Une vidéo réglée sur 08h30 est bien publiée à 08h30 chez vous.
- **Rien n'est partagé** : tout tourne sur votre machine. `client_secret.json` et
  `token.json` ne sont jamais envoyés ailleurs et sont exclus de Git.
- **Pas de doublon** : une vidéo déjà envoyée est mémorisée dans `uploads_history.json`
  et ne repartira pas une seconde fois.

## Structure du projet

```
app.py              serveur et interface
youtube_client.py   connexion Google et envoi des vidéos
queue_manager.py    file d'attente et calcul des dates
titles.py           génération des titres
config.py           réglages et chemins
static/             interface web (HTML, CSS, JS)
videos/             vos vidéos à envoyer
upload_shorts.py    ancien script en ligne de commande (conservé)
```

Fichiers créés automatiquement, à ne pas partager : `token.json`, `settings.json`,
`queue_draft.json`.
