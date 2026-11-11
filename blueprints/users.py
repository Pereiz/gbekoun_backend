# blueprints/users.py
import uuid
from flask import Blueprint, request, jsonify, g
from flasgger import swag_from
from psycopg2 import sql
from core.database import execute_query
from core.http import error_response
from core.realtime import disconnect_user
from core.security import require_auth
from services.users import (
    are_contacts,
    escape_like,
    is_blocked_between,
    mask_phone,
    phone_candidates,
)

users_bp = Blueprint('users', __name__, url_prefix='/api/users')

SEARCH_MAX_LIMIT = 50
# Texte composé presque uniquement de chiffres (un numéro de téléphone, même avec espaces).
PHONE_LIKE_REGEX = r'^\+?[0-9 ().-]{6,}$'


@users_bp.route('/me/push-token', methods=['POST'])
@require_auth
@swag_from('../descriptions/users/register_push_token.yml')
def register_push_token():
    data = request.get_json(silent=True) or {}
    push_token = data.get('expo_push_token')
    platform = data.get('platform', 'android')
    device_id = data.get('device_id')

    if not isinstance(push_token, str) or not (
        push_token.startswith(('ExpoPushToken[', 'ExponentPushToken['))
        and push_token.endswith(']')
        and len(push_token) <= 512
    ):
        return jsonify({'error': 'Jeton Expo invalide'}), 400

    if platform not in ('android', 'ios'):
        return jsonify({'error': 'Plateforme non supportée'}), 400

    if device_id is not None and (
        not isinstance(device_id, str) or len(device_id) > 255
    ):
        return jsonify({'error': 'device_id invalide'}), 400

    execute_query("""
        INSERT INTO gbekoun.push_devices
            (user_id, expo_push_token, platform, device_id, is_active, updated_at)
        VALUES (%s, %s, %s, %s, TRUE, NOW())
        ON CONFLICT (expo_push_token) DO UPDATE SET
            user_id = EXCLUDED.user_id,
            platform = EXCLUDED.platform,
            device_id = EXCLUDED.device_id,
            is_active = TRUE,
            updated_at = NOW()
    """, (g.current_user_id, push_token, platform, device_id))

    return jsonify({'registered': True}), 200


@users_bp.route('/me/push-token', methods=['DELETE'])
@require_auth
@swag_from('../descriptions/users/unregister_push_token.yml')
def unregister_push_token():
    data = request.get_json(silent=True) or {}
    push_token = data.get('expo_push_token')

    if not isinstance(push_token, str) or not (
        push_token.startswith(('ExpoPushToken[', 'ExponentPushToken['))
        and push_token.endswith(']')
        and len(push_token) <= 512
    ):
        return jsonify({'error': 'Jeton Expo invalide'}), 400

    execute_query("""
        UPDATE gbekoun.push_devices
        SET is_active = FALSE, updated_at = NOW()
        WHERE user_id = %s AND expo_push_token = %s
    """, (g.current_user_id, push_token))

    return jsonify({'registered': False}), 200


# ===================== PROFIL UTILISATEUR =====================

@users_bp.route('/me', methods=['GET'])
@require_auth
@swag_from('../descriptions/users/get_profile.yml')
def get_profile():
    """
    Récupère son propre profil
    """
    user = execute_query(sql.SQL("""
        SELECT id, phone_number, display_name, username, bio, avatar_url, 
               is_online, last_seen, created_at, is_admin,
               privacy_last_seen, privacy_profile_photo, privacy_about
        FROM gbekoun.users 
        WHERE id = %s AND deleted_at IS NULL
    """), (g.current_user_id,), fetch_one=True)
    
    if not user:
        return jsonify({'error': 'Utilisateur non trouvé'}), 404
    
    return jsonify(user), 200


@users_bp.route('/me', methods=['PUT'])
@require_auth
@swag_from('../descriptions/users/update_profile.yml')
def update_profile():
    """
    Met à jour son profil
    """
    data = request.get_json(silent=True) or {}
    updates = []
    params = []
    
    # Champs modifiables
    if 'display_name' in data:
        updates.append("display_name = %s")
        params.append(data['display_name'])
    
    if 'username' in data:
        # Vérifier l'unicité du username
        existing = execute_query(
            "SELECT id FROM gbekoun.users WHERE username = %s AND id != %s",
            (data['username'], g.current_user_id), fetch_one=True
        )
        if existing:
            return jsonify({'error': 'Ce nom d\'utilisateur est déjà pris'}), 409
        updates.append("username = %s")
        params.append(data['username'])
    
    if 'bio' in data:
        updates.append("bio = %s")
        params.append(data['bio'])
    
    if 'avatar_url' in data:
        updates.append("avatar_url = %s")
        params.append(data['avatar_url'])
    
    if not updates:
        return jsonify({'error': 'Rien à mettre à jour'}), 400
    
    updates.append("updated_at = NOW()")
    params.append(g.current_user_id)
    
    query = sql.SQL(f"""
        UPDATE gbekoun.users 
        SET {', '.join(updates)}
        WHERE id = %s
        RETURNING id, phone_number, display_name, username, bio, avatar_url
    """)
    
    updated = execute_query(query, tuple(params), fetch_one=True)
    
    return jsonify(updated), 200


@users_bp.route('/me/status', methods=['GET'])
@require_auth
@swag_from('../descriptions/users/get_status.yml')
def get_status():
    """
    Récupère son statut (online/last_seen)
    """
    user = execute_query(sql.SQL("""
        SELECT is_online, last_seen FROM gbekoun.users WHERE id = %s
    """), (g.current_user_id,), fetch_one=True)
    
    return jsonify({
        'is_online': user['is_online'] if user else False,
        'last_seen': user['last_seen'] if user else None
    }), 200


# ===================== RECHERCHE ET CONSULTATION =====================

@users_bp.route('/search', methods=['GET'])
@require_auth
@swag_from('../descriptions/users/search_users.yml')
def search_users():
    """Recherche d'utilisateurs.

    - Numéro complet (ex. +22991000001) : correspondance exacte, numéro renvoyé
      (celui qui cherche le connaît déjà).
    - Sinon, recherche par nom / pseudo : le numéro n'est PAS renvoyé, seulement
      une version masquée. Avant, chercher « 91 » listait les numéros de tout le monde.
    """
    query = (request.args.get('q') or '').strip()
    limit = max(1, min(request.args.get('limit', 20, type=int) or 20, SEARCH_MAX_LIMIT))

    if len(query) < 2:
        return error_response('Terme de recherche trop court', 400, 'QUERY_TOO_SHORT')

    phones = phone_candidates(query)
    if phones:
        users = execute_query("""
            SELECT id, phone_number, display_name, username, avatar_url, is_online
            FROM gbekoun.users u
            WHERE phone_number = ANY(%s) AND id != %s AND deleted_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM gbekoun.blocked_users b
                  WHERE (b.blocker_id = %s AND b.blocked_id = u.id)
                     OR (b.blocker_id = u.id AND b.blocked_id = %s)
              )
        """, (phones, g.current_user_id, g.current_user_id, g.current_user_id), fetch_all=True)
        for user in users:
            user['phone_masked'] = mask_phone(user['phone_number'])
        return jsonify(users), 200

    pattern = f'%{escape_like(query)}%'
    users = execute_query("""
        SELECT id, phone_number, display_name, username, avatar_url, is_online
        FROM gbekoun.users u
        -- Un nom ou pseudo qui ressemble à un numéro (valeur par défaut à
        -- l'inscription = le numéro) n'est pas cherchable : sinon « 9100 »
        -- permettrait de reconstituer un numéro chiffre par chiffre.
        WHERE ((display_name ILIKE %s AND display_name !~ %s)
               OR (username ILIKE %s AND username !~ %s))
          AND id != %s AND deleted_at IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM gbekoun.blocked_users b
              WHERE (b.blocker_id = %s AND b.blocked_id = u.id)
                 OR (b.blocker_id = u.id AND b.blocked_id = %s)
          )
        ORDER BY display_name ASC
        LIMIT %s
    """, (pattern, PHONE_LIKE_REGEX, pattern, PHONE_LIKE_REGEX,
          g.current_user_id, g.current_user_id, g.current_user_id, limit), fetch_all=True)

    for user in users:
        user['phone_masked'] = mask_phone(user.pop('phone_number'))
        # Ex. : un pseudo par défaut égal au numéro ne doit pas le révéler.
        if phone_candidates(user.get('username') or ''):
            user['username'] = None
        if phone_candidates(user.get('display_name') or ''):
            user['display_name'] = user['phone_masked']
    return jsonify(users), 200


@users_bp.route('/<user_id>', methods=['GET'])
@require_auth
@swag_from('../descriptions/users/get_user.yml')
def get_user(user_id):
    """Profil d'un utilisateur.

    Le numéro n'est visible que par l'utilisateur lui-même et par les personnes
    avec qui il partage une conversation (sinon : `phone_masked` seulement).
    """
    try:
        uuid.UUID(user_id)
    except ValueError:
        return error_response('Utilisateur non trouvé', 404, 'USER_NOT_FOUND')

    if is_blocked_between(g.current_user_id, user_id):
        return error_response('Utilisateur non trouvé', 404, 'USER_NOT_FOUND')

    user = execute_query("""
        SELECT id, phone_number, display_name, username, bio, avatar_url,
               is_online, last_seen, created_at, privacy_last_seen,
               privacy_profile_photo, privacy_about
        FROM gbekoun.users
        WHERE id::text = %s AND deleted_at IS NULL
    """, (user_id,), fetch_one=True)

    if not user:
        return error_response('Utilisateur non trouvé', 404, 'USER_NOT_FOUND')

    is_self = str(user['id']) == str(g.current_user_id)
    is_contact = is_self or are_contacts(g.current_user_id, user['id'])

    user['phone_masked'] = mask_phone(user['phone_number'])
    if not is_contact:
        user['phone_number'] = None
        if phone_candidates(user.get('username') or ''):
            user['username'] = None
        if phone_candidates(user.get('display_name') or ''):
            user['display_name'] = user['phone_masked']

    # Paramètres de confidentialité
    if not is_self:
        if user['privacy_last_seen'] == 'nobody':
            user['last_seen'] = None
        elif user['privacy_last_seen'] == 'contacts' and not is_contact:
            user['last_seen'] = None

        if user['privacy_profile_photo'] == 'nobody' or (
            user['privacy_profile_photo'] == 'contacts' and not is_contact
        ):
            user['avatar_url'] = None

        if user['privacy_about'] == 'nobody' or (
            user['privacy_about'] == 'contacts' and not is_contact
        ):
            user['bio'] = None

    return jsonify(user), 200


@users_bp.route('/by-phone/<phone_number>', methods=['GET'])
@require_auth
@swag_from('../descriptions/users/get_user_by_phone.yml')
def get_user_by_phone(phone_number):
    """Utilisateur correspondant exactement à un numéro complet."""
    phones = phone_candidates(phone_number)
    if not phones:
        return error_response('Numéro complet requis (format international)', 400, 'INVALID_PHONE')

    user = execute_query("""
        SELECT id, phone_number, display_name, username, avatar_url, is_online
        FROM gbekoun.users
        WHERE phone_number = ANY(%s) AND deleted_at IS NULL
    """, (phones,), fetch_one=True)

    if not user or is_blocked_between(g.current_user_id, user['id']):
        return error_response('Utilisateur non trouvé', 404, 'USER_NOT_FOUND')

    return jsonify(user), 200


@users_bp.route('/contacts', methods=['GET'])
@require_auth
@swag_from('../descriptions/users/get_contacts.yml')
def get_contacts():
    """
    Liste des utilisateurs avec qui l'utilisateur a une conversation directe
    """
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)
    
    contacts = execute_query(sql.SQL("""
        SELECT DISTINCT u.id, u.phone_number, u.display_name, u.username, 
               u.avatar_url, u.is_online, u.last_seen
        FROM gbekoun.conversation_participants cp1
        JOIN gbekoun.conversation_participants cp2 ON cp2.conversation_id = cp1.conversation_id
        JOIN gbekoun.conversations c ON c.id = cp1.conversation_id
        JOIN gbekoun.users u ON u.id = cp2.user_id
        WHERE cp1.user_id = %s 
        AND cp2.user_id != %s
        AND c.type = 'direct'
        AND u.deleted_at IS NULL
        ORDER BY u.display_name ASC
        LIMIT %s OFFSET %s
    """), (g.current_user_id, g.current_user_id, limit, offset), fetch_all=True)
    
    return jsonify(contacts), 200


# ===================== GESTION DES BLOCAGES =====================

@users_bp.route('/me/block/<user_id>', methods=['POST'])
@require_auth
@swag_from('../descriptions/users/block_user.yml')
def block_user(user_id):
    """
    Bloque un utilisateur
    """
    if user_id == g.current_user_id:
        return jsonify({'error': 'Vous ne pouvez pas vous bloquer vous-même'}), 400
    
    # Vérifier que l'utilisateur existe
    user = execute_query(
        sql.SQL("""SELECT id FROM gbekoun.users WHERE id = %s AND deleted_at IS NULL"""),
        (user_id,), fetch_one=True
    )
    if not user:
        return jsonify({'error': 'Utilisateur non trouvé'}), 404
    
    # Bloquer l'utilisateur
    execute_query(sql.SQL("""
        INSERT INTO gbekoun.blocked_users (blocker_id, blocked_id)
        VALUES (%s, %s)
        ON CONFLICT DO NOTHING
    """), (g.current_user_id, user_id))
    
    return jsonify({'message': 'Utilisateur bloqué'}), 200


@users_bp.route('/me/block/<user_id>', methods=['DELETE'])
@require_auth
@swag_from('../descriptions/users/unblock_user.yml')
def unblock_user(user_id):
    """
    Débloque un utilisateur
    """
    execute_query(sql.SQL("""
        DELETE FROM gbekoun.blocked_users
        WHERE blocker_id = %s AND blocked_id = %s
    """), (g.current_user_id, user_id))
    
    return jsonify({'message': 'Utilisateur débloqué'}), 200


@users_bp.route('/me/blocked', methods=['GET'])
@require_auth
@swag_from('../descriptions/users/get_blocked.yml')
def get_blocked_users():
    """
    Liste des utilisateurs bloqués
    """
    blocked = execute_query(sql.SQL("""
        SELECT u.id, u.phone_number, u.display_name, u.username, u.avatar_url
        FROM gbekoun.blocked_users b
        JOIN gbekoun.users u ON u.id = b.blocked_id
        WHERE b.blocker_id = %s AND u.deleted_at IS NULL
        ORDER BY b.created_at DESC
    """), (g.current_user_id,), fetch_all=True)
    
    return jsonify(blocked), 200


# ===================== CONFIDENTIALITÉ =====================

@users_bp.route('/me/privacy', methods=['PUT'])
@require_auth
@swag_from('../descriptions/users/update_privacy.yml')
def update_privacy():
    """
    Met à jour les paramètres de confidentialité
    """
    data = request.get_json(silent=True) or {}
    updates = []
    params = []
    
    # last_seen: everyone, contacts, nobody
    if 'last_seen' in data:
        if data['last_seen'] not in ['everyone', 'contacts', 'nobody']:
            return jsonify({'error': 'Valeur invalide pour last_seen'}), 400
        updates.append("privacy_last_seen = %s")
        params.append(data['last_seen'])
    
    # profile_photo: everyone, contacts, nobody
    if 'profile_photo' in data:
        if data['profile_photo'] not in ['everyone', 'contacts', 'nobody']:
            return jsonify({'error': 'Valeur invalide pour profile_photo'}), 400
        updates.append("privacy_profile_photo = %s")
        params.append(data['profile_photo'])
    
    # about: everyone, contacts, nobody
    if 'about' in data:
        if data['about'] not in ['everyone', 'contacts', 'nobody']:
            return jsonify({'error': 'Valeur invalide pour about'}), 400
        updates.append("privacy_about = %s")
        params.append(data['about'])
    
    if not updates:
        return jsonify({'error': 'Rien à mettre à jour'}), 400
    
    updates.append("updated_at = NOW()")
    params.append(g.current_user_id)
    
    query = sql.SQL(f"""
        UPDATE gbekoun.users 
        SET {', '.join(updates)}
        WHERE id = %s
        RETURNING privacy_last_seen, privacy_profile_photo, privacy_about
    """)
    
    updated = execute_query(query, tuple(params), fetch_one=True)
    
    return jsonify(updated), 200


# ===================== SUPPRESSION DE COMPTE =====================

@users_bp.route('/me', methods=['DELETE'])
@require_auth
@swag_from('../descriptions/users/delete_account.yml')
def delete_account():
    """
    Supprime son compte (soft delete)
    """
    data = request.get_json(silent=True) or {}
    confirmation = data.get('confirmation', False)
    
    if not confirmation:
        return jsonify({'error': 'Confirmation requise'}), 400
    
    # Soft delete : marquer comme supprimé
    execute_query(sql.SQL("""
        UPDATE gbekoun.users 
        SET deleted_at = NOW(), is_online = FALSE,  is_suspended = FALSE
        WHERE id = %s
    """), (g.current_user_id,))
    
    # Ses connexions temps réel sont fermées ; ses tokens sont refusés par require_auth.
    disconnect_user(g.current_user_id)
    return jsonify({'message': 'Compte supprimé'}), 200