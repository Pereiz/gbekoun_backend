# blueprints/users.py
from flask import Blueprint, request, jsonify, g
from flasgger import swag_from
from datetime import datetime
from psycopg2 import sql
from app import execute_query, require_auth

users_bp = Blueprint('users', __name__, url_prefix='/api/users')


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
    data = request.get_json()
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
    """
    Recherche des utilisateurs
    """
    query = request.args.get('q', '')
    limit = request.args.get('limit', 20, type=int)
    
    if not query or len(query) < 2:
        return jsonify({'error': 'Terme de recherche trop court'}), 400
    
    # Récupérer la liste des utilisateurs bloqués
    blocked = execute_query(sql.SQL("""
        SELECT blocked_id FROM gbekoun.blocked_users WHERE blocker_id = %s
    """), (g.current_user_id,), fetch_all=True)
    blocked_ids = [str(b['blocked_id']) for b in blocked] if blocked else []
    
    # Recherche
    users = execute_query(sql.SQL("""
        SELECT id, phone_number, display_name, username, avatar_url, is_online
        FROM gbekoun.users
        WHERE (phone_number ILIKE %s OR display_name ILIKE %s OR username ILIKE %s)
        AND id != %s
        AND deleted_at IS NULL
        LIMIT %s
    """), (f'%{query}%', f'%{query}%', f'%{query}%', g.current_user_id, limit), fetch_all=True)
    
    # Filtrer les utilisateurs bloqués
    users = [u for u in users if u['id'] not in blocked_ids] if blocked_ids else users
    
    return jsonify(users), 200


@users_bp.route('/<user_id>', methods=['GET'])
@require_auth
@swag_from('../descriptions/users/get_user.yml')
def get_user(user_id):
    """
    Récupère un utilisateur par ID
    """
    # Vérifier si l'utilisateur est bloqué
    is_blocked = execute_query(sql.SQL("""
        SELECT 1 FROM gbekoun.blocked_users 
        WHERE blocker_id = %s AND blocked_id = %s
    """), (g.current_user_id, user_id), fetch_one=True)
    
    if is_blocked:
        return jsonify({'error': 'Utilisateur non trouvé'}), 404
    
    user = execute_query(sql.SQL("""
        SELECT id, phone_number, display_name, username, bio, avatar_url, 
               is_online, last_seen, created_at, privacy_last_seen, 
               privacy_profile_photo, privacy_about
        FROM gbekoun.users 
        WHERE id = %s AND deleted_at IS NULL
    """), (user_id,), fetch_one=True)
    
    if not user:
        return jsonify({'error': 'Utilisateur non trouvé'}), 404
    
    # Appliquer les paramètres de confidentialité
    if user['privacy_last_seen'] == 'nobody' and user['id'] != g.current_user_id:
        user['last_seen'] = None
    elif user['privacy_last_seen'] == 'contacts':
        # Vérifier si les utilisateurs sont en contact (conversation directe)
        are_contacts = execute_query(sql.SQL("""
            SELECT 1 FROM gbekoun.conversation_participants cp1
            JOIN gbekoun.conversation_participants cp2 ON cp2.conversation_id = cp1.conversation_id
            JOIN gbekoun.conversations c ON c.id = cp1.conversation_id
            WHERE c.type = 'direct' 
            AND cp1.user_id = %s AND cp2.user_id = %s
        """), (g.current_user_id, user_id), fetch_one=True)
        
        if not are_contacts:
            user['last_seen'] = None
    
    if user['privacy_profile_photo'] == 'nobody' and user['id'] != g.current_user_id:
        user['avatar_url'] = None
    
    return jsonify(user), 200


@users_bp.route('/by-phone/<phone_number>', methods=['GET'])
@require_auth
@swag_from('../descriptions/users/get_user_by_phone.yml')
def get_user_by_phone(phone_number):
    """
    Récupère un utilisateur par numéro de téléphone
    """
    user = execute_query(sql.SQL("""
        SELECT id, phone_number, display_name, username, avatar_url, is_online
        FROM gbekoun.users 
        WHERE phone_number = %s AND deleted_at IS NULL
    """), (phone_number,), fetch_one=True)
    
    if not user:
        return jsonify({'error': 'Utilisateur non trouvé'}), 404
    
    # Vérifier si l'utilisateur est bloqué
    is_blocked = execute_query(sql.SQL("""
        SELECT 1 FROM gbekoun.blocked_users 
        WHERE blocker_id = %s AND blocked_id = %s
    """), (g.current_user_id, user['id']), fetch_one=True)
    
    if is_blocked:
        return jsonify({'error': 'Utilisateur non trouvé'}), 404
    
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
    data = request.get_json()
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
    data = request.get_json()
    confirmation = data.get('confirmation', False)
    
    if not confirmation:
        return jsonify({'error': 'Confirmation requise'}), 400
    
    # Soft delete : marquer comme supprimé
    execute_query(sql.SQL("""
        UPDATE gbekoun.users 
        SET deleted_at = NOW(), is_online = FALSE, is_active = FALSE
        WHERE id = %s
    """), (g.current_user_id,))
    
    # Anonymiser les messages existants
    # Le flag deleted_for est déjà géré par MongoDB
    
    return jsonify({'message': 'Compte supprimé'}), 200