# sockets/events.py
from flask_socketio import emit, join_room, leave_room
from flask import request
from datetime import datetime
from bson.objectid import ObjectId

from app import socketio, execute_query, verify_token, messages_col, mongo_db


# ===================== STOCKAGE EN MÉMOIRE =====================
# Stockage des connexions actives (sans Redis)
active_users = {}  # { user_id: [socket_ids] }
user_rooms = {}    # { user_id: [room_names] }


# ===================== FONCTIONS UTILITAIRES =====================

def get_user_rooms(user_id):
    """Récupère toutes les salles (conversations) d'un utilisateur"""
    conversations = execute_query("""
        SELECT conversation_id FROM gbekoun.conversation_participants
        WHERE user_id = %s AND deleted_at IS NULL
    """, (user_id,), fetch_all=True)
    
    return [f"conv_{c['conversation_id']}" for c in conversations] if conversations else []


def notify_conversation(conversation_id, event, data, exclude_user_id=None):
    """Envoie un événement à tous les participants d'une conversation"""
    room = f"conv_{conversation_id}"
    emit(event, data, room=room, include_self=(exclude_user_id is None))
    if exclude_user_id:
        # Note: include_self=False ne fonctionne pas avec les rooms personnelles
        # On utilise plutôt la logique dans l'émetteur
        pass


def update_conversation_timestamp(conversation_id):
    """Met à jour le timestamp de la conversation"""
    execute_query("""
        UPDATE gbekoun.conversations 
        SET updated_at = NOW() 
        WHERE id = %s
    """, (conversation_id,))


# ===================== ÉVÉNEMENTS DE CONNEXION =====================

@socketio.on('connect')
def handle_connect():
    """
    Connexion WebSocket
    Payload: { token: "jwt" }
    """
    token = request.args.get('token')
    if not token:
        emit('error', {'code': 'AUTH_REQUIRED', 'message': 'Token requis'})
        return False
    
    user_id = verify_token(token)
    if not user_id:
        emit('error', {'code': 'INVALID_TOKEN', 'message': 'Token invalide ou expiré'})
        return False
    
    # Stocker la connexion
    if user_id not in active_users:
        active_users[user_id] = []
    active_users[user_id].append(request.sid)
    
    # Rejoindre la room personnelle
    join_room(f"user_{user_id}")
    
    # Rejoindre les rooms des conversations existantes
    conversations = get_user_rooms(user_id)
    for room in conversations:
        join_room(room)
        if user_id not in user_rooms:
            user_rooms[user_id] = []
        user_rooms[user_id].append(room)
    
    # Mettre à jour le statut online
    execute_query("""
        UPDATE gbekoun.users 
        SET is_online = TRUE, last_seen = NOW() 
        WHERE id = %s
    """, (user_id,))
    
    # Notifier les contacts que l'utilisateur est en ligne
    # Récupérer toutes les conversations pour notifier les participants
    convs = execute_query("""
        SELECT DISTINCT cp2.user_id, cp1.conversation_id
        FROM gbekoun.conversation_participants cp1
        JOIN gbekoun.conversation_participants cp2 ON cp2.conversation_id = cp1.conversation_id
        WHERE cp1.user_id = %s AND cp2.user_id != %s
    """, (user_id, user_id), fetch_all=True)
    
    notified_users = set()
    for conv in convs:
        if conv['user_id'] not in notified_users:
            emit('user_presence', {
                'user_id': user_id,
                'is_online': True,
                'last_seen': datetime.utcnow().isoformat()
            }, room=f"user_{conv['user_id']}")
            notified_users.add(conv['user_id'])
    
    emit('connected', {
        'message': 'Connecté au serveur',
        'user_id': user_id,
        'timestamp': datetime.utcnow().isoformat()
    })
    
    return True


@socketio.on('disconnect')
def handle_disconnect():
    """
    Déconnexion WebSocket
    """
    disconnected_user = None
    
    # Trouver l'utilisateur associé à ce socket
    for uid, sids in active_users.items():
        if request.sid in sids:
            sids.remove(request.sid)
            if not sids:
                disconnected_user = uid
                del active_users[uid]
                if uid in user_rooms:
                    del user_rooms[uid]
            break
    
    if disconnected_user:
        # Mettre à jour le statut offline
        execute_query("""
            UPDATE gbekoun.users 
            SET is_online = FALSE, last_seen = NOW() 
            WHERE id = %s
        """, (disconnected_user,))
        
        # Notifier les contacts
        convs = execute_query("""
            SELECT DISTINCT cp2.user_id
            FROM gbekoun.conversation_participants cp1
            JOIN gbekoun.conversation_participants cp2 ON cp2.conversation_id = cp1.conversation_id
            WHERE cp1.user_id = %s AND cp2.user_id != %s
        """, (disconnected_user, disconnected_user), fetch_all=True)
        
        for conv in convs:
            emit('user_presence', {
                'user_id': disconnected_user,
                'is_online': False,
                'last_seen': datetime.utcnow().isoformat()
            }, room=f"user_{conv['user_id']}")


# ===================== ÉVÉNEMENTS DE MESSAGES =====================

@socketio.on('send_message')
def handle_send_message(data):
    """
    Envoie un message
    Payload: { conversation_id, type, content, reply_to_id }
    """
    token = request.args.get('token')
    user_id = verify_token(token)
    if not user_id:
        emit('error', {'code': 'UNAUTHORIZED', 'message': 'Non authentifié'})
        return
    
    conversation_id = data.get('conversation_id')
    msg_type = data.get('type', 'text')
    content = data.get('content')
    reply_to_id = data.get('reply_to_id')
    
    if not conversation_id or not content:
        emit('error', {'code': 'INVALID_DATA', 'message': 'conversation_id et content requis'})
        return
    
    # Vérifier que l'utilisateur est dans la conversation
    participant = execute_query("""
        SELECT 1 FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
    """, (conversation_id, user_id), fetch_one=True)
    
    if not participant:
        emit('error', {'code': 'FORBIDDEN', 'message': 'Non autorisé'})
        return
    
    # Vérifier si la conversation est en sourdine
    muted = execute_query("""
        SELECT muted_until FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id = %s
    """, (conversation_id, user_id), fetch_one=True)
    
    if muted and muted.get('muted_until') and muted['muted_until'] > datetime.utcnow():
        emit('error', {'code': 'MUTED', 'message': 'Conversation en sourdine'})
        return
    
    # Créer le message
    message = {
        'conversation_id': conversation_id,
        'sender_id': user_id,
        'type': msg_type,
        'content': content,
        'timestamp': datetime.utcnow(),
        'read_by': [user_id],
        'deleted_for': [],
        'reactions': []
    }
    
    if reply_to_id:
        message['reply_to_id'] = reply_to_id
    
    result = messages_col.insert_one(message)
    message_id = str(result.inserted_id)
    message['_id'] = message_id
    
    # Incrémenter unread_count des autres participants
    execute_query("""
        UPDATE gbekoun.conversation_participants
        SET unread_count = unread_count + 1
        WHERE conversation_id = %s AND user_id != %s
    """, (conversation_id, user_id))
    
    # Mettre à jour le timestamp de la conversation
    update_conversation_timestamp(conversation_id)
    
    # À l'expéditeur (confirmation)
    emit('message_sent', {'message_id': message_id}, to=request.sid)

    # Envoyer le message à tous les participants
    emit('new_message', message, room=f"conv_{conversation_id}", include_self=False)
    
    # Confirmation à l'expéditeur
    emit('message_sent', {
        'message_id': message_id,
        'timestamp': message['timestamp'].isoformat()
    })


@socketio.on('edit_message')
def handle_edit_message(data):
    """
    Modifie un message
    Payload: { message_id, new_content }
    """
    token = request.args.get('token')
    user_id = verify_token(token)
    if not user_id:
        emit('error', {'code': 'UNAUTHORIZED', 'message': 'Non authentifié'})
        return
    
    message_id = data.get('message_id')
    new_content = data.get('new_content')
    
    if not message_id or not new_content:
        emit('error', {'code': 'INVALID_DATA', 'message': 'message_id et new_content requis'})
        return
    
    # Récupérer le message
    try:
        message = messages_col.find_one({'_id': ObjectId(message_id)})
    except:
        emit('error', {'code': 'INVALID_ID', 'message': 'ID de message invalide'})
        return
    
    if not message:
        emit('error', {'code': 'NOT_FOUND', 'message': 'Message non trouvé'})
        return
    
    # Vérifier que l'utilisateur est l'auteur
    if message['sender_id'] != user_id:
        emit('error', {'code': 'FORBIDDEN', 'message': 'Vous ne pouvez modifier que vos propres messages'})
        return
    
    # Modifier le message
    messages_col.update_one(
        {'_id': ObjectId(message_id)},
        {'$set': {'content': new_content, 'edited_at': datetime.utcnow()}}
    )
    
    # Notifier les participants
    emit('message_edited', {
        'message_id': message_id,
        'new_content': new_content,
        'edited_at': datetime.utcnow().isoformat()
    }, room=f"conv_{message['conversation_id']}")


@socketio.on('delete_message')
def handle_delete_message(data):
    """
    Supprime un message
    Payload: { message_id, for_everyone: false }
    """
    token = request.args.get('token')
    user_id = verify_token(token)
    if not user_id:
        emit('error', {'code': 'UNAUTHORIZED', 'message': 'Non authentifié'})
        return
    
    message_id = data.get('message_id')
    for_everyone = data.get('for_everyone', False)
    
    if not message_id:
        emit('error', {'code': 'INVALID_DATA', 'message': 'message_id requis'})
        return
    
    # Récupérer le message
    try:
        message = messages_col.find_one({'_id': ObjectId(message_id)})
    except:
        emit('error', {'code': 'INVALID_ID', 'message': 'ID de message invalide'})
        return
    
    if not message:
        emit('error', {'code': 'NOT_FOUND', 'message': 'Message non trouvé'})
        return
    
    if for_everyone:
        # Vérifier que l'utilisateur est l'auteur
        if message['sender_id'] != user_id:
            emit('error', {'code': 'FORBIDDEN', 'message': 'Vous ne pouvez supprimer que vos propres messages pour tout le monde'})
            return
        # Supprimer complètement
        messages_col.delete_one({'_id': ObjectId(message_id)})
        deleted_by = user_id
    else:
        # Soft delete : ajouter l'utilisateur à deleted_for
        messages_col.update_one(
            {'_id': ObjectId(message_id)},
            {'$addToSet': {'deleted_for': user_id}}
        )
        deleted_by = user_id
    
    # Notifier les participants
    emit('message_deleted', {
        'message_id': message_id,
        'for_everyone': for_everyone,
        'deleted_by': deleted_by
    }, room=f"conv_{message['conversation_id']}")


# ===================== ÉVÉNEMENTS DE TYPING =====================

@socketio.on('typing')
def handle_typing(data):
    """
    Indicateur d'écriture
    Payload: { conversation_id, is_typing: true }
    """
    token = request.args.get('token')
    user_id = verify_token(token)
    if not user_id:
        return
    
    conversation_id = data.get('conversation_id')
    is_typing = data.get('is_typing', True)
    
    if not conversation_id:
        return
    
    emit('user_typing', {
        'conversation_id': conversation_id,
        'user_id': user_id,
        'is_typing': is_typing
    }, room=f"conv_{conversation_id}", include_self=False)


# ===================== ÉVÉNEMENTS DE LECTURE =====================

@socketio.on('mark_read')
def handle_mark_read(data):
    """
    Marque les messages comme lus
    Payload: { conversation_id, message_id }
    """
    token = request.args.get('token')
    user_id = verify_token(token)
    if not user_id:
        return
    
    conversation_id = data.get('conversation_id')
    message_id = data.get('message_id')
    
    if not conversation_id:
        return
    
    # Marquer le message comme lu dans MongoDB
    if message_id:
        try:
            messages_col.update_one(
                {'_id': ObjectId(message_id)},
                {'$addToSet': {'read_by': user_id}}
            )
        except:
            pass
    
    # Remettre unread_count à 0
    execute_query("""
        UPDATE gbekoun.conversation_participants
        SET unread_count = 0, last_read_message_id = %s
        WHERE conversation_id = %s AND user_id = %s
    """, (message_id, conversation_id, user_id))
    
    # Notifier les autres participants
    emit('message_read', {
        'conversation_id': conversation_id,
        'user_id': user_id,
        'message_id': message_id
    }, room=f"conv_{conversation_id}", include_self=False)


# ===================== ÉVÉNEMENTS DE RÉACTIONS =====================

@socketio.on('react')
def handle_add_reaction(data):
    """
    Ajoute une réaction
    Payload: { message_id, reaction: "❤️" }
    """
    token = request.args.get('token')
    user_id = verify_token(token)
    if not user_id:
        emit('error', {'code': 'UNAUTHORIZED', 'message': 'Non authentifié'})
        return
    
    message_id = data.get('message_id')
    reaction = data.get('reaction')
    
    if not message_id or not reaction:
        emit('error', {'code': 'INVALID_DATA', 'message': 'message_id et reaction requis'})
        return
    
    # Récupérer le message
    try:
        message = messages_col.find_one({'_id': ObjectId(message_id)})
    except:
        emit('error', {'code': 'INVALID_ID', 'message': 'ID de message invalide'})
        return
    
    if not message:
        emit('error', {'code': 'NOT_FOUND', 'message': 'Message non trouvé'})
        return
    
    # Ajouter ou mettre à jour la réaction
    messages_col.update_one(
        {'_id': ObjectId(message_id)},
        {
            '$pull': {'reactions': {'user_id': user_id}},
            '$push': {'reactions': {'user_id': user_id, 'reaction': reaction, 'timestamp': datetime.utcnow()}}
        }
    )
    
    # Notifier les participants
    emit('reaction_added', {
        'message_id': message_id,
        'user_id': user_id,
        'reaction': reaction
    }, room=f"conv_{message['conversation_id']}")


@socketio.on('remove_reaction')
def handle_remove_reaction(data):
    """
    Supprime une réaction
    Payload: { message_id }
    """
    token = request.args.get('token')
    user_id = verify_token(token)
    if not user_id:
        emit('error', {'code': 'UNAUTHORIZED', 'message': 'Non authentifié'})
        return
    
    message_id = data.get('message_id')
    
    if not message_id:
        emit('error', {'code': 'INVALID_DATA', 'message': 'message_id requis'})
        return
    
    # Récupérer le message
    try:
        message = messages_col.find_one({'_id': ObjectId(message_id)})
    except:
        emit('error', {'code': 'INVALID_ID', 'message': 'ID de message invalide'})
        return
    
    if not message:
        emit('error', {'code': 'NOT_FOUND', 'message': 'Message non trouvé'})
        return
    
    # Supprimer la réaction
    messages_col.update_one(
        {'_id': ObjectId(message_id)},
        {'$pull': {'reactions': {'user_id': user_id}}}
    )
    
    # Notifier les participants
    emit('reaction_removed', {
        'message_id': message_id,
        'user_id': user_id
    }, room=f"conv_{message['conversation_id']}")


# ===================== KEEP-ALIVE =====================

@socketio.on('ping')
def handle_ping(data):
    """
    Keep-alive
    Payload: { timestamp }
    """
    token = request.args.get('token')
    user_id = verify_token(token)
    if user_id:
        # Mettre à jour le heartbeat
        pass
    
    emit('pong', {
        'timestamp': datetime.utcnow().isoformat(),
        'client_timestamp': data.get('timestamp')
    })


# ===================== ÉVÉNEMENTS DE CONVERSATION =====================

def notify_participant_joined(conversation_id, new_user_id, added_by_id):
    """Notifie l'ajout d'un participant"""
    emit('participant_joined', {
        'conversation_id': conversation_id,
        'user_id': new_user_id,
        'added_by': added_by_id
    }, room=f"conv_{conversation_id}")


def notify_participant_left(conversation_id, user_id):
    """Notifie le départ d'un participant"""
    emit('participant_left', {
        'conversation_id': conversation_id,
        'user_id': user_id
    }, room=f"conv_{conversation_id}")


def notify_admin_changed(conversation_id, user_id, is_admin):
    """Notifie le changement de statut admin"""
    emit('admin_changed', {
        'conversation_id': conversation_id,
        'user_id': user_id,
        'is_admin': is_admin
    }, room=f"conv_{conversation_id}")


def notify_conversation_updated(conversation_id, changes):
    """Notifie la mise à jour d'une conversation"""
    emit('conversation_updated', {
        'conversation_id': conversation_id,
        'changes': changes
    }, room=f"conv_{conversation_id}")


# ===================== FONCTIONS EXPORTÉES =====================

def join_conversation_room(user_id, conversation_id):
    """Ajoute un utilisateur à la room d'une conversation"""
    room = f"conv_{conversation_id}"
    for sid in active_users.get(user_id, []):
        join_room(room, sid=sid)
    
    if user_id not in user_rooms:
        user_rooms[user_id] = []
    if room not in user_rooms[user_id]:
        user_rooms[user_id].append(room)


def leave_conversation_room(user_id, conversation_id):
    """Retire un utilisateur de la room d'une conversation"""
    room = f"conv_{conversation_id}"
    for sid in active_users.get(user_id, []):
        leave_room(room, sid=sid)
    
    if user_id in user_rooms and room in user_rooms[user_id]:
        user_rooms[user_id].remove(room)