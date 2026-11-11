# blueprints/media.py
"""Lecture des fichiers stockés par le serveur (photos de profil).

Authentification requise : l'appli doit envoyer l'en-tête Authorization en
chargeant l'image (ex. expo-image : source={{ uri, headers: { Authorization } }}).
La confidentialité de la photo (everyone / contacts / nobody) est appliquée.
"""
from flask import Blueprint, g, send_file
from flasgger import swag_from

from core.database import execute_query
from core.http import error_response
from core.security import require_auth
from services.media import AVATAR_URL_PREFIX, avatar_path
from services.users import are_contacts, is_blocked_between

media_bp = Blueprint('media', __name__, url_prefix='/api/media')


def _not_found():
    return error_response('Image introuvable', 404, 'NOT_FOUND')


@media_bp.route('/avatars/<user_id>/<filename>', methods=['GET'])
@require_auth
@swag_from('../descriptions/media/get_avatar.yml')
def get_avatar(user_id, filename):
    path = avatar_path(user_id, filename)
    if not path:
        return _not_found()

    owner = execute_query("""
        SELECT id, avatar_url, privacy_profile_photo
        FROM gbekoun.users WHERE id = %s AND deleted_at IS NULL
    """, (user_id,), fetch_one=True)
    # Seule la photo ACTUELLE est servie (pas un ancien fichier).
    if not owner or owner['avatar_url'] != f'{AVATAR_URL_PREFIX}/{user_id}/{filename}':
        return _not_found()

    if str(owner['id']) != str(g.current_user_id):
        if is_blocked_between(g.current_user_id, owner['id']):
            return _not_found()
        privacy = owner['privacy_profile_photo']
        if privacy == 'nobody' or (privacy == 'contacts' and not are_contacts(g.current_user_id, owner['id'])):
            return _not_found()

    response = send_file(path, mimetype='image/jpeg', conditional=True, max_age=86400)
    # Le nom change à chaque nouvelle photo : cache possible, mais privé (contenu soumis à autorisation).
    response.headers['Cache-Control'] = 'private, max-age=86400'
    return response
