"""Gestion des rooms Socket.IO depuis les routes REST.

Chaque socket connecté rejoint la room personnelle `user_<id>` et une room
`conv_<id>` par conversation active.
"""
from core.extensions import socketio

NAMESPACE = '/'


def user_room(user_id):
    return f"user_{user_id}"


def conversation_room(conversation_id):
    return f"conv_{conversation_id}"


def device_room(device_id):
    return f"device_{device_id}"


def _room_sids(room):
    try:
        return [sid for sid, _ in socketio.server.manager.get_participants(NAMESPACE, room)]
    except (AttributeError, KeyError):
        # Serveur Socket.IO pas encore initialisé ou namespace vide.
        return []


def _user_sids(user_id):
    return _room_sids(user_room(user_id))


def emit_to_conversation(event, payload, conversation_id, exclude_user_id=None):
    """Diffuse un événement aux membres connectés d'une conversation (depuis le REST).

    exclude_user_id : l'auteur de l'action n'a pas besoin de l'événement (il a
    déjà la réponse HTTP) ; c'est le même comportement que `include_self=False`
    côté Socket.IO, ce qui évite les doublons dans l'appli.
    """
    skip = _user_sids(exclude_user_id) if exclude_user_id else None
    socketio.emit(event, payload, room=conversation_room(conversation_id), skip_sid=skip or None)


def join_users_to_conversation(user_ids, conversation_id):
    """Ajoute les sockets connectés de ces utilisateurs à la room de la conversation."""
    room = conversation_room(conversation_id)
    for user_id in user_ids:
        for sid in _user_sids(user_id):
            socketio.server.enter_room(sid, room, namespace=NAMESPACE)


def remove_users_from_conversation(user_ids, conversation_id):
    """Retire les sockets de la room : un membre retiré ne reçoit plus rien en direct."""
    room = conversation_room(conversation_id)
    for user_id in user_ids:
        for sid in _user_sids(user_id):
            socketio.server.leave_room(sid, room, namespace=NAMESPACE)


def disconnect_user(user_id):
    """Coupe toutes les connexions temps réel d'un utilisateur (suspension, suppression)."""
    for sid in _user_sids(user_id):
        socketio.server.disconnect(sid, namespace=NAMESPACE)


def disconnect_device(device_id):
    """Coupe les connexions temps réel d'un appareil révoqué (remplacé par un autre)."""
    for sid in _room_sids(device_room(device_id)):
        socketio.server.disconnect(sid, namespace=NAMESPACE)
