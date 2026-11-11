"""Stockage des images (photos de profil) sur le disque du serveur.

Les fichiers sont écrits dans UPLOAD_FOLDER (volume Docker persistant).
Chaque image reçue est :
- vérifiée (c'est vraiment une image JPEG/PNG/WebP/GIF, taille raisonnable) ;
- recadrée au carré et réduite à 512×512 ;
- réencodée en JPEG, ce qui supprime les métadonnées (EXIF, position GPS…)
  et tout contenu caché dans le fichier d'origine.
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

AVATAR_DIR = 'avatars'
AVATAR_URL_PREFIX = '/api/media/avatars'


def _avatar_root():
    return os.path.join(Config.UPLOAD_FOLDER, AVATAR_DIR)


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


def save_avatar(user_id, file_storage):
    """Enregistre l'avatar et retourne son URL publique relative.

    Nom de fichier aléatoire : une nouvelle URL à chaque changement (pas de
    cache périmé côté appli) et impossible à deviner.
    """
    content = _process_image(file_storage)
    user_dir = os.path.join(_avatar_root(), str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    filename = f'{secrets.token_hex(16)}.jpg'
    temporary = os.path.join(user_dir, f'.{filename}.tmp')
    with open(temporary, 'wb') as handle:
        handle.write(content)
    os.replace(temporary, os.path.join(user_dir, filename))  # écriture atomique
    return f'{AVATAR_URL_PREFIX}/{user_id}/{filename}'


def avatar_path(user_id, filename):
    """Chemin disque d'un avatar, ou None si le nom est invalide (pas de ../ possible)."""
    try:
        user_id = str(uuid.UUID(str(user_id)))
    except ValueError:
        return None
    if not filename.endswith('.jpg') or len(filename) != 36 or not all(
        c in '0123456789abcdef' for c in filename[:-4]
    ):
        return None
    path = os.path.join(_avatar_root(), str(user_id), filename)
    return path if os.path.isfile(path) else None


def delete_avatar_file(avatar_url):
    """Supprime le fichier d'un ancien avatar stocké chez nous (ignore les autres URL)."""
    if not avatar_url or not avatar_url.startswith(AVATAR_URL_PREFIX + '/'):
        return
    parts = avatar_url[len(AVATAR_URL_PREFIX) + 1:].split('/')
    if len(parts) != 2:
        return
    path = avatar_path(parts[0], parts[1])
    if path:
        try:
            os.remove(path)
        except OSError:
            pass
