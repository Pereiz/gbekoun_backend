# blueprints/conversations.py
from flask import Blueprint, request, jsonify, g
from flasgger import swag_from
import uuid
from datetime import datetime, timedelta
from psycopg2 import sql
from core.database import execute_query, messages_col, transaction
from core.realtime import join_users_to_conversation, remove_users_from_conversation
from core.security import require_auth
from utils.message_helpers import serialize_message_value

conversations_bp = Blueprint('conversations', __name__)


# ===================== ROUTES PRINCIPALES =====================

@conversations_bp.route('/', methods=['GET'])
@require_auth
@swag_from('../descriptions/conversations/get_conversations.yml')
def get_conversations():
    """Liste toutes les conversations de l'utilisateur"""
    
    # Récupérer les paramètres de pagination
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)
    
    # Récupérer les conversations avec les infos des autres participants
    conversations = execute_query(sql.SQL("""
        SELECT 
            c.id, c.type, c.name, c.avatar_url, c.created_at,
            cp.unread_count, cp.muted_until,
            cp.is_archived, cp.is_pinned,
            (
                SELECT json_build_object(
                    'id', u.id,
                    'display_name', u.display_name,
                    'avatar_url', u.avatar_url,
                    'is_online', u.is_online,
                    'phone_number', u.phone_number
                )
                FROM gbekoun.conversation_participants cp2
                JOIN gbekoun.users u ON u.id = cp2.user_id
                WHERE cp2.conversation_id = c.id AND cp2.user_id != %s
                LIMIT 1
            ) as other_user
        FROM gbekoun.conversations c
        JOIN gbekoun.conversation_participants cp ON cp.conversation_id = c.id
        WHERE cp.user_id = %s AND cp.deleted_at IS NULL
        ORDER BY cp.is_pinned DESC, c.updated_at DESC
        LIMIT %s OFFSET %s
    """), (g.current_user_id, g.current_user_id, limit, offset), fetch_all=True)
    
    # Ajouter le dernier message de chaque conversation depuis MongoDB
    for conv in conversations:
        last_msg = list(messages_col.find(
            {
                'conversation_id': conv['id'],
                'deleted_for': {'$ne': g.current_user_id}
            }
        ).sort('timestamp', -1).limit(1))
        
        if last_msg:
            conv['last_message'] = serialize_message_value(last_msg[0])
        else:
            conv['last_message'] = None
    
    return jsonify(conversations), 200


@conversations_bp.route('/direct', methods=['POST'])
@require_auth
@swag_from('../descriptions/conversations/create_direct.yml')
def create_direct():
    """Crée ou récupère une conversation directe 1-1"""
    data = request.get_json(silent=True) or {}
    phone_number = data.get('phone_number')

    if not phone_number:
        return jsonify({'error': 'Numéro de téléphone requis'}), 400

    # Trouver l'autre utilisateur
    other_user = execute_query(
        sql.SQL("""SELECT id FROM gbekoun.users WHERE phone_number = %s AND deleted_at IS NULL"""),
        (phone_number,), fetch_one=True
    )

    if not other_user:
        return jsonify({'error': 'Utilisateur non trouvé'}), 404

    other_user_id = str(other_user['id'])
    if other_user_id == str(g.current_user_id):
        return jsonify({'error': 'Impossible de créer une conversation avec vous-même'}), 400

    # Verrou sur la paire d'utilisateurs : deux requêtes simultanées ne créent
    # jamais deux conversations directes pour le même duo.
    pair_lock_key = ':'.join(sorted((str(g.current_user_id), other_user_id)))
    with transaction() as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (pair_lock_key,)
        )
        # On cherche la conversation du duo MÊME si l'un des deux l'a supprimée :
        # avant, une suppression faisait créer une 2e conversation et les messages
        # de l'autre arrivaient dans l'ancienne, devenue invisible.
        cur.execute("""
            SELECT c.id
            FROM gbekoun.conversations c
            JOIN gbekoun.conversation_participants cp1 ON cp1.conversation_id = c.id
            JOIN gbekoun.conversation_participants cp2 ON cp2.conversation_id = c.id
            WHERE c.type = 'direct'
              AND cp1.user_id = %s AND cp2.user_id = %s
            ORDER BY c.created_at, c.id
            LIMIT 1
        """, (g.current_user_id, other_user_id))
        existing = cur.fetchone()

        if existing:
            conversation_id = str(existing['id'])
            # Si l'utilisateur l'avait supprimée, elle réapparaît (historique toujours effacé).
            cur.execute("""
                UPDATE gbekoun.conversation_participants
                SET deleted_at = NULL
                WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NOT NULL
            """, (conversation_id, g.current_user_id))
            status_code, body = 200, {'id': conversation_id, 'existing': True}
        else:
            conversation_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO gbekoun.conversations (id, type, created_by)
                VALUES (%s, 'direct', %s)
            """, (conversation_id, g.current_user_id))
            cur.executemany("""
                INSERT INTO gbekoun.conversation_participants (conversation_id, user_id)
                VALUES (%s, %s)
            """, [(conversation_id, g.current_user_id), (conversation_id, other_user_id)])
            status_code, body = 201, {'id': conversation_id}

    join_users_to_conversation((g.current_user_id, other_user_id), conversation_id)
    return jsonify(body), status_code


@conversations_bp.route('/group', methods=['POST'])
@require_auth
@swag_from('../descriptions/conversations/create_group.yml')
def create_group():
    """Crée un nouveau groupe"""
    data = request.get_json(silent=True) or {}
    name = data.get('name')
    participants_phones = data.get('participants', [])
    avatar_url = data.get('avatar_url')

    if not name:
        return jsonify({'error': 'Nom du groupe requis'}), 400
    if not isinstance(participants_phones, list):
        return jsonify({'error': 'participants doit être une liste de numéros'}), 400

    conversation_id = str(uuid.uuid4())

    # Tout se fait dans une seule transaction : en cas d'erreur, rien n'est créé
    # (avant, un groupe à moitié créé pouvait rester en base).
    with transaction() as cur:
        cur.execute("""
            SELECT id FROM gbekoun.users
            WHERE phone_number = ANY(%s) AND deleted_at IS NULL
        """, (list(participants_phones),))
        # Le créateur en premier, puis les autres sans doublon (même numéro répété, ou soi-même).
        participant_ids = list(dict.fromkeys(
            [str(g.current_user_id)] + [str(row['id']) for row in cur.fetchall()]
        ))

        cur.execute("""
            INSERT INTO gbekoun.conversations (id, type, name, avatar_url, created_by)
            VALUES (%s, 'group', %s, %s, %s)
        """, (conversation_id, name, avatar_url, g.current_user_id))

        # Le créateur est administrateur du groupe.
        cur.executemany("""
            INSERT INTO gbekoun.conversation_participants (conversation_id, user_id, is_admin)
            VALUES (%s, %s, %s)
        """, [
            (conversation_id, user_id, user_id == str(g.current_user_id))
            for user_id in participant_ids
        ])

    join_users_to_conversation(participant_ids, conversation_id)
    return jsonify({'id': conversation_id}), 201


@conversations_bp.route('/<conversation_id>', methods=['GET'])
@require_auth
@swag_from('../descriptions/conversations/get_conversation.yml')
def get_conversation(conversation_id):
    """Détails d'une conversation spécifique"""

    # Vérifier que l'utilisateur a accès à la conversation
    participant_query= sql.SQL("""
        SELECT cp.*, c.type, c.name, c.avatar_url, c.created_at, c.created_by
        FROM gbekoun.conversation_participants cp
        JOIN gbekoun.conversations c ON c.id = cp.conversation_id
        WHERE cp.conversation_id = %s AND cp.user_id = %s AND cp.deleted_at IS NULL
    """)
    participant = execute_query(participant_query, (conversation_id, g.current_user_id), fetch_one=True)
    
    if not participant:
        return jsonify({'error': 'Conversation non trouvée'}), 404
    
    # Récupérer tous les participants
    all_particiapants_query = sql.SQL("""
        SELECT u.id, u.display_name, u.avatar_url, u.is_online, u.phone_number,
               cp.is_admin, cp.joined_at, cp.muted_until, cp.is_archived, cp.is_pinned
        FROM gbekoun.conversation_participants cp
        JOIN gbekoun.users u ON u.id = cp.user_id
        WHERE cp.conversation_id = %s AND cp.deleted_at IS NULL
        ORDER BY cp.is_admin DESC, u.display_name ASC
    """)
    participants = execute_query(all_particiapants_query, (conversation_id,), fetch_all=True)
    
    result = dict(participant)
    result['participants'] = participants
    
    return jsonify(result), 200


@conversations_bp.route('/<conversation_id>', methods=['PUT'])
@require_auth
@swag_from('../descriptions/conversations/update_group.yml')
def update_group(conversation_id):
    """Met à jour un groupe (nom, avatar)"""
    data = request.get_json(silent=True) or {}
    
    # Vérifier que l'utilisateur est admin
    admin_check = execute_query(sql.SQL("""
        SELECT is_admin FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
    """), (conversation_id, g.current_user_id), fetch_one=True)
    
    if not admin_check:
        return jsonify({'error': 'Conversation non trouvée'}), 404
    
    if not admin_check['is_admin']:
        return jsonify({'error': 'Seul un administrateur peut modifier le groupe'}), 403
    
    # Construire la requête de mise à jour
    updates = []
    params = []
    
    if 'name' in data:
        updates.append("name = %s")
        params.append(data['name'])
    
    if 'avatar_url' in data:
        updates.append("avatar_url = %s")
        params.append(data['avatar_url'])
    
    if not updates:
        return jsonify({'error': 'Rien à mettre à jour'}), 400
    
    updates.append("updated_at = NOW()")
    params.append(conversation_id)
    
    query = sql.SQL(f"""
        UPDATE gbekoun.conversations 
        SET {', '.join(updates)}
        WHERE id = %s
        RETURNING id, name, avatar_url
    """)
    
    updated = execute_query(query, tuple(params), fetch_one=True)
    
    return jsonify(updated), 200


@conversations_bp.route('/<conversation_id>', methods=['DELETE'])
@require_auth
@swag_from('../descriptions/conversations/delete_conversation.yml')
def delete_conversation(conversation_id):
    """Supprime une conversation pour l'utilisateur (soft delete)

    L'historique est effacé pour lui seul. Pour une conversation directe, elle
    réapparaît vide au prochain message (voir services/messaging.py).
    """
    participant = execute_query(sql.SQL("""
        SELECT 1 FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
    """), (conversation_id, g.current_user_id), fetch_one=True)
    if not participant:
        return jsonify({'error': 'Conversation non trouvée'}), 404

    messages_col.update_many(
        {'conversation_id': conversation_id},
        {'$addToSet': {'deleted_for': g.current_user_id}}
    )

    execute_query(sql.SQL("""
        UPDATE gbekoun.conversation_participants
        SET deleted_at = NOW(), unread_count = 0
        WHERE conversation_id = %s AND user_id = %s
    """), (conversation_id, g.current_user_id))

    remove_users_from_conversation([g.current_user_id], conversation_id)
    return jsonify({'message': 'Conversation supprimée'}), 200


# ===================== GESTION DES ÉTATS =====================

@conversations_bp.route('/<conversation_id>/archive', methods=['POST'])
@require_auth
@swag_from('../descriptions/conversations/archive.yml')
def archive_conversation(conversation_id):
    """Archive la conversation"""
    
    execute_query(sql.SQL("""
        UPDATE gbekoun.conversation_participants
        SET is_archived = TRUE
        WHERE conversation_id = %s AND user_id = %s
    """), (conversation_id, g.current_user_id))
    
    return jsonify({'message': 'Conversation archivée'}), 200


@conversations_bp.route('/<conversation_id>/unarchive', methods=['POST'])
@require_auth
@swag_from('../descriptions/conversations/unarchive.yml')
def unarchive_conversation(conversation_id):
    """Désarchive la conversation"""
    
    execute_query(sql.SQL("""
        UPDATE gbekoun.conversation_participants
        SET is_archived = FALSE
        WHERE conversation_id = %s AND user_id = %s
    """), (conversation_id, g.current_user_id))
    
    return jsonify({'message': 'Conversation désarchivée'}), 200


@conversations_bp.route('/<conversation_id>/pin', methods=['POST'])
@require_auth
@swag_from('../descriptions/conversations/pin.yml')
def pin_conversation(conversation_id):
    """Épingle la conversation"""
    
    execute_query(sql.SQL("""
        UPDATE gbekoun.conversation_participants
        SET is_pinned = TRUE
        WHERE conversation_id = %s AND user_id = %s
    """), (conversation_id, g.current_user_id))
    
    return jsonify({'message': 'Conversation épinglée'}), 200


@conversations_bp.route('/<conversation_id>/unpin', methods=['POST'])
@require_auth
@swag_from('../descriptions/conversations/unpin.yml')
def unpin_conversation(conversation_id):
    """Désépingle la conversation"""
    
    execute_query(sql.SQL("""
        UPDATE gbekoun.conversation_participants
        SET is_pinned = FALSE
        WHERE conversation_id = %s AND user_id = %s
    """), (conversation_id, g.current_user_id))
    
    return jsonify({'message': 'Conversation désépinglée'}), 200


@conversations_bp.route('/<conversation_id>/mute', methods=['POST'])
@require_auth
@swag_from('../descriptions/conversations/mute.yml')
def mute_conversation(conversation_id):
    """Met la conversation en sourdine"""
    data = request.get_json(silent=True) or {}
    duration_hours = data.get('duration_hours', 8)
    
    muted_until = datetime.utcnow() + timedelta(hours=duration_hours)
    
    execute_query(sql.SQL("""
        UPDATE gbekoun.conversation_participants
        SET muted_until = %s
        WHERE conversation_id = %s AND user_id = %s
    """), (muted_until, conversation_id, g.current_user_id))
    
    return jsonify({
        'message': f'Conversation en sourdine jusqu\'à {muted_until}',
        'muted_until': muted_until
    }), 200


@conversations_bp.route('/<conversation_id>/unmute', methods=['POST'])
@require_auth
@swag_from('../descriptions/conversations/unmute.yml')
def unmute_conversation(conversation_id):
    """Désactive la sourdine"""
    
    execute_query(sql.SQL("""
        UPDATE gbekoun.conversation_participants
        SET muted_until = NULL
        WHERE conversation_id = %s AND user_id = %s
    """), (conversation_id, g.current_user_id))
    
    return jsonify({'message': 'Sourdine désactivée'}), 200


# ===================== GESTION DES PARTICIPANTS =====================

@conversations_bp.route('/<conversation_id>/participants', methods=['GET'])
@require_auth
@swag_from('../descriptions/conversations/get_participants.yml')
def get_participants(conversation_id):
    """Liste des participants"""
    
    # Vérifier l'accès
    access = execute_query(sql.SQL("""
        SELECT 1 FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
    """), (conversation_id, g.current_user_id), fetch_one=True)
    
    if not access:
        return jsonify({'error': 'Conversation non trouvée'}), 404
    
    participants = execute_query(sql.SQL("""
        SELECT u.id, u.display_name, u.avatar_url, u.is_online, u.phone_number,
               cp.is_admin, cp.joined_at
        FROM gbekoun.conversation_participants cp
        JOIN gbekoun.users u ON u.id = cp.user_id
        WHERE cp.conversation_id = %s AND cp.deleted_at IS NULL
        ORDER BY cp.is_admin DESC, u.display_name ASC
    """), (str(conversation_id),), fetch_all=True)
    
    return jsonify(participants), 200


@conversations_bp.route('/<conversation_id>/participants', methods=['POST'])
@require_auth
@swag_from('../descriptions/conversations/add_participant.yml')
def add_participant(conversation_id):
    """Ajoute un participant au groupe"""
    data = request.get_json(silent=True) or {}
    phone_number = data.get('phone_number')
    
    if not phone_number:
        return jsonify({'error': 'Numéro de téléphone requis'}), 400
    
    # Vérifier que l'utilisateur courant est admin du groupe
    admin_check = execute_query("""
        SELECT is_admin FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
    """, (conversation_id, g.current_user_id), fetch_one=True)
    
    if not admin_check:
        return jsonify({'error': 'Conversation non trouvée'}), 404
    
    if not admin_check['is_admin']:
        return jsonify({'error': 'Seul un administrateur peut ajouter des participants'}), 403
    
    # Vérifier que la conversation est un groupe
    conv = execute_query(
        sql.SQL("""SELECT type FROM gbekoun.conversations WHERE id = %s"""),
        (conversation_id,), fetch_one=True
    )
    
    if not conv or conv['type'] != 'group':
        return jsonify({'error': 'Seuls les groupes peuvent avoir plusieurs participants'}), 400
    
    # Trouver l'utilisateur à ajouter
    user_to_add = execute_query(
        sql.SQL("""SELECT id FROM gbekoun.users WHERE phone_number = %s"""),
        (phone_number,), fetch_one=True
    )
    
    if not user_to_add:
        return jsonify({'error': 'Utilisateur non trouvé'}), 404
    
    # Ajouter le participant (ou le réintégrer s'il était parti)
    execute_query("""
        INSERT INTO gbekoun.conversation_participants (conversation_id, user_id, is_admin)
        VALUES (%s, %s, FALSE)
        ON CONFLICT (conversation_id, user_id) DO UPDATE
        SET deleted_at = NULL, is_admin = FALSE
    """, (conversation_id, user_to_add['id']))

    # Ses appareils connectés reçoivent les messages du groupe immédiatement.
    join_users_to_conversation([user_to_add['id']], conversation_id)
    return jsonify({'message': 'Participant ajouté'}), 200



@conversations_bp.route('/<conversation_id>/participants/<user_id>', methods=['DELETE'])
@require_auth
@swag_from('../descriptions/conversations/remove_participant.yml')
def remove_participant(conversation_id, user_id):
    """Retire un participant du groupe"""
    
    # Vérifier que l'utilisateur courant est admin
    admin_check = execute_query(sql.SQL("""
        SELECT is_admin FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
    """), (conversation_id, g.current_user_id), fetch_one=True)
    
    if not admin_check:
        return jsonify({'error': 'Conversation non trouvée'}), 404
    
    if not admin_check['is_admin']:
        return jsonify({'error': 'Seul un administrateur peut retirer des participants'}), 403
    
    # Retirer le participant (soft delete)
    execute_query(sql.SQL("""
        UPDATE gbekoun.conversation_participants
        SET deleted_at = NOW(), is_admin = FALSE
        WHERE conversation_id = %s AND user_id = %s
    """), (conversation_id, user_id))

    # Il ne doit plus recevoir les messages en temps réel.
    remove_users_from_conversation([user_id], conversation_id)
    return jsonify({'message': 'Participant retiré'}), 200


@conversations_bp.route('/<conversation_id>/leave', methods=['POST'])
@require_auth
@swag_from('../descriptions/conversations/leave_group.yml')
def leave_group(conversation_id):
    """Quitte un groupe"""
    
    # Vérifier que l'utilisateur est dans la conversation
    participant = execute_query(sql.SQL("""
        SELECT is_admin FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
    """), (conversation_id, g.current_user_id), fetch_one=True)
    
    if not participant:
        return jsonify({'error': 'Conversation non trouvée'}), 404
    
    # Vérifier que ce n'est pas le seul admin
    if participant['is_admin']:
        admin_count = execute_query(sql.SQL("""
            SELECT COUNT(*) as count FROM gbekoun.conversation_participants
            WHERE conversation_id = %s AND is_admin = TRUE AND deleted_at IS NULL
        """), (conversation_id,), fetch_one=True)
        
        if admin_count['count'] == 1:
            return jsonify({'error': 'Vous êtes le seul administrateur. Nommez un autre admin avant de quitter.'}), 400
    
    # Quitter le groupe
    execute_query(sql.SQL("""
        UPDATE gbekoun.conversation_participants
        SET deleted_at = NOW(), is_admin = FALSE
        WHERE conversation_id = %s AND user_id = %s
    """), (conversation_id, g.current_user_id))

    remove_users_from_conversation([g.current_user_id], conversation_id)
    return jsonify({'message': 'Groupe quitté'}), 200


# ===================== ADMINISTRATION DU GROUPE =====================

@conversations_bp.route('/<conversation_id>/make-admin/<user_id>', methods=['POST'])
@require_auth
@swag_from('../descriptions/conversations/make_admin.yml')
def make_admin(conversation_id, user_id):
    """Promouvoit un participant en admin"""
    
    # Vérifier que l'utilisateur courant est admin
    admin_check = execute_query(sql.SQL("""
        SELECT is_admin FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
    """), (conversation_id, g.current_user_id), fetch_one=True)
    
    if not admin_check or not admin_check['is_admin']:
        return jsonify({'error': 'Seul un administrateur peut nommer des admins'}), 403
    
    # Promouvoir l'utilisateur
    execute_query(sql.SQL("""
        UPDATE gbekoun.conversation_participants
        SET is_admin = TRUE
        WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
    """), (conversation_id, user_id))
    
    return jsonify({'message': 'Utilisateur promu administrateur'}), 200


@conversations_bp.route('/<conversation_id>/remove-admin/<user_id>', methods=['POST'])
@require_auth
@swag_from('../descriptions/conversations/remove_admin.yml')
def remove_admin(conversation_id, user_id):
    """Rétrograde un admin"""
    
    # Vérifier que l'utilisateur courant est admin
    admin_check = execute_query(sql.SQL("""
        SELECT is_admin FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
    """), (conversation_id, g.current_user_id), fetch_one=True)
    
    if not admin_check or not admin_check['is_admin']:
        return jsonify({'error': 'Seul un administrateur peut rétrograder des admins'}), 403
    
    # Ne pas permettre de rétrograder le dernier admin
    admin_count = execute_query(sql.SQL("""
        SELECT COUNT(*) as count FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND is_admin = TRUE AND deleted_at IS NULL
    """), (conversation_id,), fetch_one=True)
    
    if admin_count['count'] <= 1:
        return jsonify({'error': 'Il doit rester au moins un administrateur'}), 400
    
    # Rétrograder l'utilisateur
    execute_query(sql.SQL("""
        UPDATE gbekoun.conversation_participants
        SET is_admin = FALSE
        WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
    """), (conversation_id, user_id))
    
    return jsonify({'message': 'Administrateur rétrogradé'}), 200


# ===================== MÉDIAS ET MESSAGES =====================

@conversations_bp.route('/<conversation_id>/media', methods=['GET'])
@require_auth
@swag_from('../descriptions/conversations/get_media.yml')
def get_media(conversation_id):
    """Liste les médias partagés dans la conversation"""
    
    # Vérifier l'accès
    access = execute_query(sql.SQL("""
        SELECT 1 FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
    """), (conversation_id, g.current_user_id), fetch_one=True)
    
    if not access:
        return jsonify({'error': 'Conversation non trouvée'}), 404
    
    media_type = request.args.get('type')
    limit = request.args.get('limit', 50, type=int)
    
    query = {
        'conversation_id': conversation_id,
        'type': {'$in': ['image', 'video', 'audio', 'file']},
        'deleted_for': {'$ne': g.current_user_id},
        'is_deleted': {'$ne': True}
    }
    
    if media_type:
        query['type'] = media_type
    
    media = list(messages_col.find(query).sort('timestamp', -1).limit(limit))
    
    return jsonify(serialize_message_value(media)), 200


@conversations_bp.route('/<conversation_id>/read', methods=['POST'])
@require_auth
@swag_from('../descriptions/conversations/mark_read.yml')
def mark_read(conversation_id):
    """Marque tous les messages comme lus"""
    data = request.get_json(silent=True) or {}
    message_id = data.get('message_id')
    
    # Mettre à jour le compteur de messages non lus
    execute_query(sql.SQL("""
        UPDATE gbekoun.conversation_participants
        SET unread_count = 0, last_read_message_id = %s
        WHERE conversation_id = %s AND user_id = %s
    """), (message_id, conversation_id, g.current_user_id))
    
    return jsonify({'message': 'Messages marqués comme lus'}), 200


@conversations_bp.route('/<conversation_id>/clear', methods=['POST'])
@require_auth
@swag_from('../descriptions/conversations/clear_messages.yml')
def clear_messages(conversation_id):
    """Efface tous les messages de la conversation pour l'utilisateur"""
    
    # Soft delete : marquer les messages comme supprimés pour cet utilisateur
    messages_col.update_many(
        {'conversation_id': conversation_id},
        {'$addToSet': {'deleted_for': g.current_user_id}}
    )
    
    # Remettre le compteur de messages non lus à zéro
    execute_query(sql.SQL("""
        UPDATE gbekoun.conversation_participants
        SET unread_count = 0
        WHERE conversation_id = %s AND user_id = %s
    """), (conversation_id, g.current_user_id))
    
    return jsonify({'message': 'Messages effacés'}), 200