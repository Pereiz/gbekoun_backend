# blueprints/messages.py
import re
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request
from flasgger import swag_from

from core.database import execute_query, messages_col
from core.extensions import socketio
from core.realtime import conversation_room, user_room
from core.security import require_auth
from services.messaging import (
    MessageError,
    after_message_created,
    create_message,
    is_participant,
)
from utils.message_helpers import (
    parse_object_id,
    serialize_message_value,
    set_user_reaction,
    to_utc_isoformat,
)

messages_bp = Blueprint('messages', __name__)

MAX_LIMIT = 200  # aligné sur /api/config


def _limit_arg(default=50):
    limit = request.args.get('limit', default, type=int) or default
    return max(1, min(limit, MAX_LIMIT))


def _forbidden():
    return jsonify({'error': 'Non autorisé'}), 403


def _invalid_message_id():
    return jsonify({'error': 'ID de message invalide'}), 400


# ===================== ROUTES PRINCIPALES =====================

@messages_bp.route('/<conversation_id>/messages', methods=['GET'])
@require_auth
@swag_from('../descriptions/messages/get_messages.yml')
def get_messages(conversation_id):
    """Historique des messages (paginé)"""
    if not is_participant(conversation_id, g.current_user_id):
        return _forbidden()

    query = {
        'conversation_id': conversation_id,
        'deleted_for': {'$ne': g.current_user_id},
    }

    before_id = request.args.get('before_id')
    if before_id:
        before_object_id = parse_object_id(before_id)
        if not before_object_id:
            return jsonify({'error': 'before_id invalide'}), 400
        query['_id'] = {'$lt': before_object_id}

    messages = list(messages_col.find(query).sort('timestamp', -1).limit(_limit_arg()))
    return jsonify(serialize_message_value(messages)), 200


@messages_bp.route('/<conversation_id>/messages/search', methods=['GET'])
@require_auth
@swag_from('../descriptions/messages/search_messages.yml')
def search_messages(conversation_id):
    """Recherche dans les messages"""
    if not is_participant(conversation_id, g.current_user_id):
        return _forbidden()

    query_text = request.args.get('q', '')
    if not query_text:
        return jsonify({'error': 'Terme de recherche requis'}), 400

    # re.escape : le texte saisi est cherché tel quel (pas interprété comme une regex).
    cursor = messages_col.find({
        'conversation_id': conversation_id,
        'deleted_for': {'$ne': g.current_user_id},
        'is_deleted': {'$ne': True},
        'content': {'$regex': re.escape(query_text), '$options': 'i'}
    }).sort('timestamp', -1).limit(_limit_arg())

    return jsonify(serialize_message_value(list(cursor))), 200


@messages_bp.route('/<conversation_id>/messages/<message_id>', methods=['GET'])
@require_auth
@swag_from('../descriptions/messages/get_message.yml')
def get_message(conversation_id, message_id):
    """Récupère un message spécifique"""
    if not is_participant(conversation_id, g.current_user_id):
        return _forbidden()

    object_id = parse_object_id(message_id)
    if not object_id:
        return _invalid_message_id()

    message = messages_col.find_one({
        '_id': object_id,
        'conversation_id': conversation_id,
        'deleted_for': {'$ne': g.current_user_id}
    })
    if not message:
        return jsonify({'error': 'Message non trouvé'}), 404

    return jsonify(serialize_message_value(message)), 200


@messages_bp.route('/<conversation_id>/messages', methods=['POST'])
@require_auth
@swag_from('../descriptions/messages/send_message.yml')
def send_message_rest(conversation_id):
    """Envoie un message (fallback REST)"""
    data = request.get_json(silent=True) or {}

    try:
        message = create_message(
            conversation_id,
            g.current_user_id,
            data.get('type', 'text'),
            data.get('content'),
            data.get('reply_to_id'),
        )
    except MessageError as error:
        return jsonify({'error': error.message}), error.http_status

    reply_to_id = message.get('reply_to_id')
    return jsonify({
        'message_id': str(message['_id']),
        'timestamp': to_utc_isoformat(message['timestamp']),
        'reply_to_id': str(reply_to_id) if reply_to_id else None
    }), 201


@messages_bp.route('/<conversation_id>/messages/<message_id>', methods=['PUT'])
@require_auth
@swag_from('../descriptions/messages/edit_message.yml')
def edit_message(conversation_id, message_id):
    """Modifie un message"""
    data = request.get_json(silent=True) or {}
    new_content = data.get('content')
    if not isinstance(new_content, str) or not new_content:
        return jsonify({'error': 'Nouveau contenu requis'}), 400

    object_id = parse_object_id(message_id)
    if not object_id:
        return _invalid_message_id()

    message = messages_col.find_one({'_id': object_id, 'conversation_id': conversation_id})
    if not message:
        return jsonify({'error': 'Message non trouvé'}), 404

    if not is_participant(conversation_id, g.current_user_id):
        return _forbidden()

    if message.get('is_deleted'):
        return jsonify({'error': 'Un message supprimé ne peut plus être modifié'}), 400

    if message['sender_id'] != g.current_user_id:
        return jsonify({'error': 'Vous ne pouvez modifier que vos propres messages'}), 403

    result = messages_col.update_one(
        {'_id': object_id},
        {'$set': {'content': new_content, 'edited_at': datetime.now(timezone.utc)}}
    )
    if result.matched_count > 0:
        return jsonify({'message': 'Message modifié'}), 200

    return jsonify({'error': 'Erreur lors de la modification'}), 500


@messages_bp.route('/<conversation_id>/messages/<message_id>', methods=['DELETE'])
@require_auth
@swag_from('../descriptions/messages/delete_message.yml')
def delete_message(conversation_id, message_id):
    """Supprime un message (pour moi, ou pour tout le monde)"""
    for_everyone = request.args.get('for_everyone', 'false').lower() == 'true'

    if not is_participant(conversation_id, g.current_user_id):
        return _forbidden()

    object_id = parse_object_id(message_id)
    if not object_id:
        return _invalid_message_id()

    message = messages_col.find_one({'_id': object_id, 'conversation_id': conversation_id})
    if not message:
        return jsonify({'error': 'Message non trouvé'}), 404

    if for_everyone:
        if message['sender_id'] != g.current_user_id:
            return jsonify({'error': 'Vous ne pouvez supprimer que vos propres messages pour tout le monde'}), 403

        if message.get('is_deleted'):
            return jsonify({'message': 'Message supprimé'}), 200

        deleted_at = datetime.now(timezone.utc)
        result = messages_col.update_one(
            {'_id': object_id},
            {
                '$set': {
                    'content': '',
                    'reactions': [],
                    'is_deleted': True,
                    'deleted_at': deleted_at,
                    'deleted_by': g.current_user_id
                },
                '$unset': {'metadata': '', 'forwarded_from': '', 'reply_to_id': ''}
            }
        )
    else:
        deleted_at = None
        result = messages_col.update_one(
            {'_id': object_id},
            {'$addToSet': {'deleted_for': g.current_user_id}}
        )

    if result.matched_count == 0:
        return jsonify({'error': 'Erreur lors de la suppression'}), 500

    socketio.emit('message_deleted', {
        'message_id': message_id,
        'for_everyone': for_everyone,
        'deleted_by': g.current_user_id,
        'deleted_at': to_utc_isoformat(deleted_at) if deleted_at else None
    }, room=conversation_room(conversation_id) if for_everyone else user_room(g.current_user_id))
    return jsonify({'message': 'Message supprimé'}), 200


# ===================== RÉACTIONS =====================

@messages_bp.route('/<conversation_id>/messages/<message_id>/react', methods=['POST'])
@require_auth
@swag_from('../descriptions/messages/add_reaction.yml')
def add_reaction(conversation_id, message_id):
    """Ajoute (ou remplace) la réaction de l'utilisateur sur un message"""
    data = request.get_json(silent=True) or {}
    reaction = data.get('reaction')
    if not reaction:
        return jsonify({'error': 'Réaction requise'}), 400

    if not is_participant(conversation_id, g.current_user_id):
        return _forbidden()

    object_id = parse_object_id(message_id)
    if not object_id:
        return _invalid_message_id()

    message = messages_col.find_one({'_id': object_id, 'conversation_id': conversation_id})
    if not message:
        return jsonify({'error': 'Message non trouvé'}), 404

    set_user_reaction(messages_col, object_id, g.current_user_id, reaction)
    return jsonify({'message': 'Réaction ajoutée'}), 200


@messages_bp.route('/<conversation_id>/messages/<message_id>/react', methods=['DELETE'])
@require_auth
@swag_from('../descriptions/messages/remove_reaction.yml')
def remove_reaction(conversation_id, message_id):
    """Supprime la réaction de l'utilisateur"""
    if not is_participant(conversation_id, g.current_user_id):
        return _forbidden()

    object_id = parse_object_id(message_id)
    if not object_id:
        return _invalid_message_id()

    messages_col.update_one(
        {'_id': object_id, 'conversation_id': conversation_id},
        {'$pull': {'reactions': {'user_id': g.current_user_id}}}
    )
    return jsonify({'message': 'Réaction supprimée'}), 200


# ===================== TRANSFERT =====================

@messages_bp.route('/<conversation_id>/messages/<message_id>/forward', methods=['POST'])
@require_auth
@swag_from('../descriptions/messages/forward_message.yml')
def forward_message(conversation_id, message_id):
    """Transfère un message vers d'autres conversations"""
    data = request.get_json(silent=True) or {}
    target_conversation_ids = data.get('conversation_ids', [])
    if not isinstance(target_conversation_ids, list) or not target_conversation_ids:
        return jsonify({'error': 'Liste de conversations cible requise'}), 400

    # Il faut être membre de la conversation d'origine pour pouvoir transférer :
    # sinon on pourrait copier (et donc lire) n'importe quel message.
    if not is_participant(conversation_id, g.current_user_id):
        return _forbidden()

    object_id = parse_object_id(message_id)
    if not object_id:
        return _invalid_message_id()

    original = messages_col.find_one({
        '_id': object_id,
        'conversation_id': conversation_id,
        'deleted_for': {'$ne': g.current_user_id},
        'is_deleted': {'$ne': True}
    })
    if not original:
        return jsonify({'error': 'Message non trouvé'}), 404

    forwarded_messages = []
    for target_conv_id in dict.fromkeys(target_conversation_ids):
        if not isinstance(target_conv_id, str) or not is_participant(target_conv_id, g.current_user_id):
            continue

        new_message = {
            'conversation_id': target_conv_id,
            'sender_id': g.current_user_id,
            'type': original['type'],
            'content': original['content'],
            'timestamp': datetime.now(timezone.utc),
            'read_by': [g.current_user_id],
            'deleted_for': [],
            'reactions': [],
            'forwarded_from': {
                'original_message_id': message_id,
                'original_conversation_id': conversation_id,
                'original_sender_id': original['sender_id']
            }
        }
        result = messages_col.insert_one(new_message)
        new_message['_id'] = result.inserted_id
        after_message_created(target_conv_id, g.current_user_id, str(result.inserted_id))

        socketio.emit('new_message', serialize_message_value(new_message),
                      room=conversation_room(target_conv_id))
        forwarded_messages.append({
            'conversation_id': target_conv_id,
            'message_id': str(result.inserted_id)
        })

    return jsonify({
        'forwarded': forwarded_messages,
        'count': len(forwarded_messages)
    }), 200


# ===================== MESSAGES ÉPINGLÉS =====================

@messages_bp.route('/starred', methods=['GET'])
@require_auth
@swag_from('../descriptions/messages/get_starred.yml')
def get_starred_messages():
    """Messages épinglés/importants (stockés dans PostgreSQL, contenu dans MongoDB)"""
    starred = execute_query("""
        SELECT sm.message_id, sm.conversation_id, sm.starred_at,
               c.name as conversation_name, c.type as conversation_type
        FROM gbekoun.starred_messages sm
        JOIN gbekoun.conversations c ON c.id = sm.conversation_id
        WHERE sm.user_id = %s
        ORDER BY sm.starred_at DESC
        LIMIT %s
    """, (g.current_user_id, _limit_arg()), fetch_all=True)

    for item in starred:
        object_id = parse_object_id(item['message_id'])
        message = messages_col.find_one({'_id': object_id}) if object_id else None
        if not message:
            item['content'] = '[Message non trouvé]'
            continue
        item['content'] = message.get('content')
        item['type'] = message.get('type')
        item['sender_id'] = message.get('sender_id')
        item['timestamp'] = message.get('timestamp')
        item['is_deleted'] = message.get('is_deleted', False)
        item['deleted_at'] = message.get('deleted_at')

    return jsonify(serialize_message_value(starred)), 200


@messages_bp.route('/<conversation_id>/messages/<message_id>/star', methods=['POST'])
@require_auth
@swag_from('../descriptions/messages/star_message.yml')
def star_message(conversation_id, message_id):
    """Épingle un message"""
    if not is_participant(conversation_id, g.current_user_id):
        return _forbidden()

    object_id = parse_object_id(message_id)
    if not object_id:
        return _invalid_message_id()

    if not messages_col.find_one({'_id': object_id, 'conversation_id': conversation_id}):
        return jsonify({'error': 'Message non trouvé'}), 404

    execute_query("""
        INSERT INTO gbekoun.starred_messages (user_id, conversation_id, message_id)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, message_id) DO NOTHING
    """, (g.current_user_id, conversation_id, message_id))

    return jsonify({'message': 'Message épinglé'}), 200


@messages_bp.route('/<conversation_id>/messages/<message_id>/star', methods=['DELETE'])
@require_auth
@swag_from('../descriptions/messages/unstar_message.yml')
def unstar_message(conversation_id, message_id):
    """Désépingle un message"""
    execute_query("""
        DELETE FROM gbekoun.starred_messages
        WHERE user_id = %s AND conversation_id = %s AND message_id = %s
    """, (g.current_user_id, conversation_id, message_id))

    return jsonify({'message': 'Message désépinglé'}), 200


# ===================== MENTIONS =====================

@messages_bp.route('/mentions', methods=['GET'])
@require_auth
@swag_from('../descriptions/messages/get_mentions.yml')
def get_mentions():
    """Messages où l'utilisateur est mentionné (@[user_id] ou @user_id)"""
    only_unread = request.args.get('only_unread', 'false').lower() == 'true'
    user_pattern = re.escape(g.current_user_id)

    query = {
        'deleted_for': {'$ne': g.current_user_id},
        'is_deleted': {'$ne': True},
        'content': {'$regex': f'@\\[?{user_pattern}\\]?'}
    }
    messages = list(messages_col.find(query).sort('timestamp', -1).limit(_limit_arg()))

    if only_unread:
        messages = [m for m in messages if g.current_user_id not in m.get('read_by', [])]

    for msg in messages:
        msg['conversation'] = execute_query("""
            SELECT name, type FROM gbekoun.conversations WHERE id = %s
        """, (msg['conversation_id'],), fetch_one=True)

    return jsonify(serialize_message_value(messages)), 200
