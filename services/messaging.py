"""Envoi de messages : utilisé à la fois par l'API REST et par Socket.IO."""
from datetime import datetime, timezone

from core.database import execute_query, messages_col
from core.notifications import notify_new_message
from core.realtime import join_users_to_conversation
from utils.message_helpers import validate_reply_to

# Types acceptés à l'envoi. Les médias (image, video, audio, file) ne sont pas
# encore pris en charge (pas d'upload) : sans cette restriction, un client
# pourrait envoyer un « média » pointant vers n'importe quelle URL externe.
# Le validateur MongoDB (database/mongo/init.js) connaît déjà tous les types.
MESSAGE_TYPES = ('text', 'location', 'contact')
MEDIA_TYPES = ('image', 'video', 'audio', 'file')


class MessageError(Exception):
    """Erreur métier renvoyée telle quelle au client."""

    def __init__(self, code, message, http_status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def is_participant(conversation_id, user_id):
    """Vrai si l'utilisateur est un membre actif de la conversation."""
    return bool(execute_query("""
        SELECT 1 FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
    """, (conversation_id, user_id), fetch_one=True))


def after_message_created(conversation_id, sender_id, message_id):
    """Mises à jour à faire après chaque nouveau message (envoyé ou transféré)."""
    # Conversation directe supprimée par un des deux : elle réapparaît chez lui
    # dès qu'un nouveau message arrive (l'ancien historique reste effacé).
    revived = execute_query("""
        UPDATE gbekoun.conversation_participants cp
        SET deleted_at = NULL, unread_count = 0
        FROM gbekoun.conversations c, gbekoun.users u
        WHERE c.id = cp.conversation_id
          AND u.id = cp.user_id
          AND c.type = 'direct'
          AND cp.conversation_id = %s
          AND cp.deleted_at IS NOT NULL
          AND u.deleted_at IS NULL           -- jamais pour un compte supprimé
        RETURNING cp.user_id
    """, (conversation_id,), fetch_all=True)
    if revived:
        join_users_to_conversation([row['user_id'] for row in revived], conversation_id)

    # Seuls les membres encore présents voient leur compteur augmenter.
    execute_query("""
        UPDATE gbekoun.conversation_participants
        SET unread_count = unread_count + 1
        WHERE conversation_id = %s AND user_id != %s AND deleted_at IS NULL
    """, (conversation_id, sender_id))

    execute_query("""
        UPDATE gbekoun.conversations SET updated_at = NOW() WHERE id = %s
    """, (conversation_id,))

    notify_new_message(conversation_id, sender_id, message_id)


def create_message(conversation_id, sender_id, msg_type, content, reply_to_id=None):
    """Valide puis enregistre un message. Retourne le document MongoDB créé.

    Note : la mise en sourdine ne bloque plus l'envoi. Elle ne concerne que les
    notifications reçues (filtrées dans core/notifications.py).
    """
    if not isinstance(conversation_id, str) or not conversation_id:
        raise MessageError('INVALID_DATA', 'conversation_id requis')
    if not isinstance(content, str) or not content:
        raise MessageError('INVALID_DATA', 'Contenu du message requis')
    if msg_type in MEDIA_TYPES:
        raise MessageError('MEDIA_NOT_SUPPORTED', 'L\'envoi de médias n\'est pas encore disponible')
    if msg_type not in MESSAGE_TYPES:
        raise MessageError('INVALID_DATA', 'Type de message invalide')

    if not is_participant(conversation_id, sender_id):
        raise MessageError('FORBIDDEN', 'Non autorisé', 403)

    reply_object_id, reply_error = validate_reply_to(
        messages_col, conversation_id, sender_id, reply_to_id
    )
    if reply_error:
        raise MessageError('INVALID_REPLY', reply_error)

    message = {
        'conversation_id': conversation_id,
        'sender_id': sender_id,
        'type': msg_type,
        'content': content,
        'timestamp': datetime.now(timezone.utc),
        'read_by': [sender_id],
        'deleted_for': [],
        'reactions': [],
    }
    if reply_object_id:
        message['reply_to_id'] = reply_object_id

    result = messages_col.insert_one(message)
    message['_id'] = result.inserted_id

    after_message_created(conversation_id, sender_id, str(result.inserted_id))
    return message


DELETED_ACCOUNT_NAME = 'Compte supprimé'


def attach_senders(messages):
    """Ajoute `sender` = {id, display_name, avatar_url} à chaque message (sur place).

    Une seule requête pour toute la liste. Le nom est lu au moment de
    l'affichage : un changement de nom se voit aussi sur les anciens messages.
    Accepte un message seul ou une liste ; retourne la même valeur.
    """
    items = messages if isinstance(messages, list) else [messages]
    sender_ids = sorted({str(m['sender_id']) for m in items if m and m.get('sender_id')})
    if not sender_ids:
        return messages

    rows = execute_query("""
        SELECT id::text AS id, display_name, avatar_url, deleted_at
        FROM gbekoun.users
        WHERE id::text = ANY(%s)
    """, (sender_ids,), fetch_all=True) or []
    users = {row['id']: row for row in rows}

    for message in items:
        if not message or not message.get('sender_id'):
            continue
        user = users.get(str(message['sender_id']))
        if not user or user.get('deleted_at'):
            message['sender'] = {'id': str(message['sender_id']),
                                 'display_name': DELETED_ACCOUNT_NAME, 'avatar_url': None}
        else:
            message['sender'] = {'id': user['id'], 'display_name': user['display_name'],
                                 'avatar_url': user['avatar_url']}
    return messages
