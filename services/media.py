"""Stockage des images (photos de profil, avatars de groupe) sur le disque du serveur.

Les fichiers sont écrits dans UPLOAD_FOLDER (volume Docker persistant).
Chaque image reçue est :
- vérifiée (c'est vraiment une image JPEG/PNG/WebP/GIF, taille raisonnable) ;
- recadrée au carré et réduite à 512×512 ;
- réencodée en JPEG, ce qui supprime les métadonnées (EXIF, position GPS…)
  et tout contenu caché dans le fichier d'origine.

Deux catégories, rangées par propriétaire (utilisateur ou conversation) :
    /api/media/avatars/<user_id>/<fichier>.jpg
    /api/media/groups/<conversation_id>/<fichier>.jpg
"""
import os
import secrets
import uuid
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from core.config import Config
from core.http import ApiError

AVATAR_MAX_BYTES = 2 * 1024 * 1024
AVATAR_SIZE = (512, 512)
ALLOWED_FORMATS = {'JPEG', 'PNG', 'WEBP', 'GIF'}
# Protection contre les « bombes » de décompression (petit fichier, image géante).
Image.MAX_IMAGE_PIXELS = 40_000_000

USER_AVATARS = 'avatars'
GROUP_AVATARS = 'groups'
CATEGORIES = (USER_AVATARS, GROUP_AVATARS)
URL_PREFIX = '/api/media'
AVATAR_URL_PREFIX = f'{URL_PREFIX}/{USER_AVATARS}'


def _category_root(category):
    if category not in CATEGORIES:
        raise ValueError(category)
    return os.path.join(Config.UPLOAD_FOLDER, category)


def _process_image(file_storage):
    data = file_storage.read(AVATAR_MAX_BYTES + 1)
    if not data:
        raise ApiError('FILE_REQUIRED', 'Fichier image requis (champ « file »)')
    if len(data) > AVATAR_MAX_BYTES:
        raise ApiError('FILE_TOO_LARGE', 'Image trop lourde (2 Mo maximum)', 413)

    try:
        with Image.open(BytesIO(data)) as probe:
            if probe.format not in ALLOWED_FORMATS:
                raise ApiError('UNSUPPORTED_IMAGE', 'Formats acceptés : JPEG, PNG, WebP, GIF', 415)
            probe.verify()
        image = Image.open(BytesIO(data))
        image.seek(0)  # GIF animé : première image
        image = ImageOps.exif_transpose(image)  # respecte l'orientation de la photo
        if image.mode not in ('RGB', 'L'):
            background = Image.new('RGB', image.size, (255, 255, 255))
            rgba = image.convert('RGBA')
            background.paste(rgba, mask=rgba.split()[-1])
            image = background
        image = ImageOps.fit(image.convert('RGB'), AVATAR_SIZE, Image.LANCZOS)
    except ApiError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        raise ApiError('INVALID_IMAGE', 'Fichier image invalide ou illisible', 415)

    output = BytesIO()
    image.save(output, format='JPEG', quality=85, optimize=True)
    return output.getvalue()


def save_image(category, owner_id, file_storage):
    """Enregistre l'image et retourne son URL relative.

    Nom de fichier aléatoire : une nouvelle URL à chaque changement (pas de
    cache périmé côté appli) et impossible à deviner.
    """
    content = _process_image(file_storage)
    owner_dir = os.path.join(_category_root(category), str(owner_id))
    os.makedirs(owner_dir, exist_ok=True)
    filename = f'{secrets.token_hex(16)}.jpg'
    temporary = os.path.join(owner_dir, f'.{filename}.tmp')
    with open(temporary, 'wb') as handle:
        handle.write(content)
    os.replace(temporary, os.path.join(owner_dir, filename))  # écriture atomique
    return f'{URL_PREFIX}/{category}/{owner_id}/{filename}'


def image_path(category, owner_id, filename):
    """Chemin disque d'une image, ou None si le nom est invalide (pas de ../ possible)."""
    if category not in CATEGORIES:
        return None
    try:
        owner_id = str(uuid.UUID(str(owner_id)))
    except ValueError:
        return None
    if not filename.endswith('.jpg') or len(filename) != 36 or not all(
        c in '0123456789abcdef' for c in filename[:-4]
    ):
        return None
    path = os.path.join(_category_root(category), owner_id, filename)
    return path if os.path.isfile(path) else None


def delete_image(url):
    """Supprime le fichier d'une image stockée chez nous (ignore les autres URL)."""
    if not url or not url.startswith(URL_PREFIX + '/'):
        return
    parts = url[len(URL_PREFIX) + 1:].split('/')
    if len(parts) != 3:
        return
    path = image_path(*parts)
    if path:
        try:
            os.remove(path)
        except OSError:
            pass


# --- Raccourcis pour les photos de profil (noms utilisés ailleurs dans le code) ---

def save_avatar(user_id, file_storage):
    return save_image(USER_AVATARS, user_id, file_storage)


def avatar_path(user_id, filename):
    return image_path(USER_AVATARS, user_id, filename)


def delete_avatar_file(avatar_url):
    delete_image(avatar_url)
