"""Événements Socket.IO (temps réel).

Authentification : le token JWT est passé dans la query string (`?token=...`).
Toute la logique Socket.IO est ici ; app.py ne fait que l'importer.
"""
import threading
from datetime import datetime, timezone

from flask import request
from flask_socketio import emit, join_room, rooms

from core.database import execute_query, messages_col
from core.extensions import socketio
from core.realtime import conversation_room, device_room, user_room
from core.security import SESSION_ACTIVE, decode_token, get_session_state, verify_token
from services.messaging import MessageError, create_message, is_participant
from utils.message_helpers import (
    parse_object_id,
    serialize_message_value,
    set_user_reaction,
    to_utc_isoformat,
)

# ===================== CONNEXIONS ACTIVES (mémoire du process) =====================
# Fonctionne avec un seul worker Gunicorn (configuration actuelle du Dockerfile).
_connections_lock = threading.Lock()
_user_sids = {}   # { user_id: set(sid) }
_sid_user = {}    # { sid: user_id }


def _now_iso():
    return to_utc_isoformat(datetime.now(timezone.utc))


def _current_user_id():
    return verify_token(request.args.get('token'))


def _is_in_conversation_room(conversation_id):
    """Le socket a rejoint la room seulement s'il est membre actif : vérification sans requête SQL."""
    return isinstance(conversation_id, str) and conversation_room(conversation_id) in rooms()


def _contacts_of(user_id):
    """Utilisateurs partageant au moins une conversation active avec user_id."""
    rows = execute_query("""
        SELECT DISTINCT cp2.user_id
        FROM gbekoun.conversation_participants cp1
        JOIN gbekoun.conversation_participants cp2
          ON cp2.conversation_id = cp1.conversation_id
        WHERE cp1.user_id = %s AND cp2.user_id != %s
          AND cp1.deleted_at IS NULL AND cp2.deleted_at IS NULL
    """, (user_id, user_id), fetch_all=True)
    return [row['user_id'] for row in rows or []]


def _broadcast_presence(user_id, is_online):
    payload = {'user_id': user_id, 'is_online': is_online, 'last_seen': _now_iso()}
    for contact_id in _contacts_of(user_id):
        emit('user_presence', payload, room=user_room(contact_id))


def _load_message_for_member(message_id, user_id):
    """Retourne (message, None) si l'utilisateur peut agir dessus, sinon (None, erreur)."""
    object_id = parse_object_id(message_id)
    if not object_id:
        return None, ('INVALID_ID', 'ID de message invalide')

    message = messages_col.find_one({'_id': object_id})
    if not message:
        return None, ('NOT_FOUND', 'Message non trouvé')

    if not is_participant(message['conversation_id'], user_id):
        return None, ('FORBIDDEN', 'Non autorisé')

    return message, None


def _emit_error(code, message):
    emit('error', {'code': code, 'message': message})


# ===================== CONNEXION / DÉCONNEXION =====================

@socketio.on('connect')
def handle_connect():
    claims = decode_token(request.args.get('token'))
    if not claims:
        return False
    user_id = claims['user_id']

    # Compte suspendu/supprimé, ou appareil remplacé par un autre : connexion refusée.
    if get_session_state(user_id, claims['device_id']) != SESSION_ACTIVE:
        return False

    with _connections_lock:
        first_connection = not _user_sids.get(user_id)
        _user_sids.setdefault(user_id, set()).add(request.sid)
        _sid_user[request.sid] = user_id

    join_room(user_room(user_id))
    # Permet de couper précisément cet appareil s'il est remplacé (core/realtime.disconnect_device).
    join_room(device_room(claims['device_id']))

    # Uniquement les conversations où l'utilisateur est encore membre.
    conversations = execute_query("""
        SELECT conversation_id FROM gbekoun.conversation_participants
        WHERE user_id = %s AND deleted_at IS NULL
    """, (user_id,), fetch_all=True)
    for conversation in conversations or []:
        join_room(conversation_room(conversation['conversation_id']))

    execute_query("""
        UPDATE gbekoun.users SET is_online = TRUE, last_seen = NOW() WHERE id = %s
    """, (user_id,))

    if first_connection:
        _broadcast_presence(user_id, True)

    emit('connected', {
        'message': 'Connecté au serveur',
        'user_id': user_id,
        'timestamp': _now_iso(),
    })
    return True


@socketio.on('disconnect')
def handle_disconnect():
    with _connections_lock:
        user_id = _sid_user.pop(request.sid, None)
        if not user_id:
            return
        sids = _user_sids.get(user_id, set())
        sids.discard(request.sid)
        went_offline = not sids
        if went_offline:
            _user_sids.pop(user_id, None)

    if went_offline:
        execute_query("""
            UPDATE gbekoun.users SET is_online = FALSE, last_seen = NOW() WHERE id = %s
        """, (user_id,))
        _broadcast_presence(user_id, False)


# ===================== MESSAGES =====================

@socketio.on('send_message')
def handle_send_message(data):
    """Payload: { conversation_id, type, content, reply_to_id }"""
    user_id = _current_user_id()
    if not user_id:
        return _emit_error('UNAUTHORIZED', 'Non authentifié')
    if not isinstance(data, dict):
        return _emit_error('INVALID_DATA', 'Données du message invalides')

    conversation_id = data.get('conversation_id')
    try:
        message = create_message(
            conversation_id,
            user_id,
            data.get('type', 'text'),
            data.get('content'),
            data.get('reply_to_id'),
        )
    except MessageError as error:
        return _emit_error(error.code, error.message)

    socket_message = serialize_message_value(message)

    # Aux autres participants, puis un seul accusé de réception à l'expéditeur.
    emit('new_message', socket_message, room=conversation_room(conversation_id), include_self=False)
    emit('message_sent', {
        'message_id': socket_message['_id'],
        'timestamp': socket_message['timestamp'],
        'reply_to_id': socket_message.get('reply_to_id'),
    })


@socketio.on('edit_message')
def handle_edit_message(data):
    """Payload: { message_id, new_content }"""
    user_id = _current_user_id()
    if not user_id:
        return _emit_error('UNAUTHORIZED', 'Non authentifié')
    if not isinstance(data, dict):
        return _emit_error('INVALID_DATA', 'Données invalides')

    new_content = data.get('new_content')
    if not data.get('message_id') or not isinstance(new_content, str) or not new_content:
        return _emit_error('INVALID_DATA', 'message_id et new_content requis')

    message, error = _load_message_for_member(data.get('message_id'), user_id)
    if error:
        return _emit_error(*error)

    if message['sender_id'] != user_id:
        return _emit_error('FORBIDDEN', 'Vous ne pouvez modifier que vos propres messages')
    if message.get('is_deleted'):
        return _emit_error('MESSAGE_DELETED', 'Un message supprimé ne peut plus être modifié')

    edited_at = datetime.now(timezone.utc)
    messages_col.update_one(
        {'_id': message['_id']},
        {'$set': {'content': new_content, 'edited_at': edited_at}}
    )

    emit('message_edited', {
        'message_id': str(message['_id']),
        'new_content': new_content,
        'edited_at': to_utc_isoformat(edited_at),
    }, room=conversation_room(message['conversation_id']))


@socketio.on('delete_message')
def handle_delete_message(data):
    """Payload: { message_id, for_everyone: false }"""
    user_id = _current_user_id()
    if not user_id:
        return _emit_error('UNAUTHORIZED', 'Non authentifié')
    if not isinstance(data, dict) or not data.get('message_id'):
        return _emit_error('INVALID_DATA', 'message_id requis')

    for_everyone = bool(data.get('for_everyone', False))
    message, error = _load_message_for_member(data.get('message_id'), user_id)
    if error:
        return _emit_error(*error)

    message_id = str(message['_id'])
    conversation_id = message['conversation_id']

    if for_everyone:
        if message['sender_id'] != user_id:
            return _emit_error(
                'FORBIDDEN', 'Vous ne pouvez supprimer que vos propres messages pour tout le monde'
            )
        if message.get('is_deleted'):
            deleted_at = message.get('deleted_at')
            return emit('message_deleted', {
                'message_id': message_id,
                'for_everyone': True,
                'deleted_by': message.get('deleted_by'),
                'deleted_at': to_utc_isoformat(deleted_at) if deleted_at else None,
            }, room=conversation_room(conversation_id))

        deleted_at = datetime.now(timezone.utc)
        messages_col.update_one(
            {'_id': message['_id']},
            {
                '$set': {
                    'content': '',
                    'reactions': [],
                    'is_deleted': True,
                    'deleted_at': deleted_at,
                    'deleted_by': user_id,
                },
                '$unset': {'metadata': '', 'forwarded_from': '', 'reply_to_id': ''},
            }
        )
    else:
        deleted_at = None
        messages_col.update_one(
            {'_id': message['_id']},
            {'$addToSet': {'deleted_for': user_id}}
        )

    # Pour tous : toute la conversation. Pour moi : seulement mes autres appareils.
    emit('message_deleted', {
        'message_id': message_id,
        'for_everyone': for_everyone,
        'deleted_by': user_id,
        'deleted_at': to_utc_isoformat(deleted_at) if deleted_at else None,
    }, room=conversation_room(conversation_id) if for_everyone else user_room(user_id))


# ===================== ÉCRITURE EN COURS =====================

@socketio.on('typing')
def handle_typing(data):
    """Payload: { conversation_id, is_typing: true }"""
    user_id = _current_user_id()
    if not user_id or not isinstance(data, dict):
        return

    conversation_id = data.get('conversation_id')
    if not _is_in_conversation_room(conversation_id):
        return

    emit('user_typing', {
        'conversation_id': conversation_id,
        'user_id': user_id,
        'is_typing': bool(data.get('is_typing', True)),
    }, room=conversation_room(conversation_id), include_self=False)


# ===================== LECTURE =====================

@socketio.on('mark_read')
def handle_mark_read(data):
    """Payload: { conversation_id, message_id }"""
    user_id = _current_user_id()
    if not user_id or not isinstance(data, dict):
        return

    conversation_id = data.get('conversation_id')
    if not _is_in_conversation_room(conversation_id):
        return

    message_id = data.get('message_id')
    object_id = parse_object_id(message_id)
    if message_id and not object_id:
        return _emit_error('INVALID_ID', 'ID de message invalide')

    if object_id:
        messages_col.update_one(
            {'_id': object_id, 'conversation_id': conversation_id},
            {'$addToSet': {'read_by': user_id}}
        )

    execute_query("""
        UPDATE gbekoun.conversation_participants
        SET unread_count = 0, last_read_message_id = %s
        WHERE conversation_id = %s AND user_id = %s
    """, (message_id, conversation_id, user_id))

    emit('message_read', {
        'conversation_id': conversation_id,
        'user_id': user_id,
        'message_id': message_id,
    }, room=conversation_room(conversation_id), include_self=False)


# ===================== RÉACTIONS =====================

@socketio.on('react')
def handle_add_reaction(data):
    """Payload: { message_id, reaction: "❤️" }"""
    user_id = _current_user_id()
    if not user_id:
        return _emit_error('UNAUTHORIZED', 'Non authentifié')
    if not isinstance(data, dict) or not data.get('message_id') or not data.get('reaction'):
        return _emit_error('INVALID_DATA', 'message_id et reaction requis')

    message, error = _load_message_for_member(data.get('message_id'), user_id)
    if error:
        return _emit_error(*error)
    if message.get('is_deleted'):
        return _emit_error('MESSAGE_DELETED', 'Impossible de réagir à un message supprimé')

    set_user_reaction(messages_col, message['_id'], user_id, data['reaction'])

    emit('reaction_added', {
        'message_id': str(message['_id']),
        'user_id': user_id,
        'reaction': data['reaction'],
    }, room=conversation_room(message['conversation_id']))


@socketio.on('remove_reaction')
def handle_remove_reaction(data):
    """Payload: { message_id }"""
    user_id = _current_user_id()
    if not user_id:
        return _emit_error('UNAUTHORIZED', 'Non authentifié')
    if not isinstance(data, dict) or not data.get('message_id'):
        return _emit_error('INVALID_DATA', 'message_id requis')

    message, error = _load_message_for_member(data.get('message_id'), user_id)
    if error:
        return _emit_error(*error)

    messages_col.update_one(
        {'_id': message['_id']},
        {'$pull': {'reactions': {'user_id': user_id}}}
    )

    emit('reaction_removed', {
        'message_id': str(message['_id']),
        'user_id': user_id,
    }, room=conversation_room(message['conversation_id']))


# ===================== KEEP-ALIVE =====================

@socketio.on('ping')
def handle_ping(data):
    """Payload: { timestamp }"""
    emit('pong', {
        'timestamp': _now_iso(),
        'client_timestamp': data.get('timestamp') if isinstance(data, dict) else None,
    })
