# blueprints/messages.py
from flask import Blueprint, request, jsonify, g
from flasgger import swag_from
from bson.objectid import ObjectId
from datetime import datetime
from psycopg2 import sql
from app import execute_query, require_auth, messages_col

messages_bp = Blueprint('messages', __name__)


# ===================== ROUTES PRINCIPALES =====================

@messages_bp.route('/<conversation_id>/messages', methods=['GET'])
@require_auth
@swag_from('../descriptions/messages/get_messages.yml')
def get_messages(conversation_id):
    """
    Historique des messages (paginé)
    """
    # Vérifier l'accès à la conversation
    participant = execute_query(sql.SQL("""
        SELECT 1 FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
    """), (conversation_id, g.current_user_id), fetch_one=True)
    
    if not participant:
        return jsonify({'error': 'Non autorisé'}), 403
    
    # Paramètres de pagination
    limit = request.args.get('limit', 50, type=int)
    before_id = request.args.get('before_id')
    
    # Construire la requête
    query = {'conversation_id': conversation_id}
    
    # Filtrer les messages supprimés pour cet utilisateur
    query['deleted_for'] = {'$ne': g.current_user_id}
    
    if before_id:
        query['_id'] = {'$lt': ObjectId(before_id)}
    
    # Exécuter la requête
    cursor = messages_col.find(query).sort('timestamp', -1).limit(limit)
    messages = list(cursor)
    
    # Convertir ObjectId en string
    for msg in messages:
        msg['_id'] = str(msg['_id'])
        if 'reply_to_id' in msg and msg['reply_to_id']:
            msg['reply_to_id'] = str(msg['reply_to_id'])
    
    return jsonify(messages), 200


@messages_bp.route('/<conversation_id>/messages/search', methods=['GET'])
@require_auth
@swag_from('../descriptions/messages/search_messages.yml')
def search_messages(conversation_id):
    """
    Recherche dans les messages
    """
    # Vérifier l'accès
    participant = execute_query(sql.SQL("""
        SELECT 1 FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
    """), (conversation_id, g.current_user_id), fetch_one=True)
    
    if not participant:
        return jsonify({'error': 'Non autorisé'}), 403
    
    # Paramètres de recherche
    query_text = request.args.get('q', '')
    limit = request.args.get('limit', 50, type=int)
    
    if not query_text:
        return jsonify({'error': 'Terme de recherche requis'}), 400
    
    # Recherche textuelle (MongoDB)
    # Note: Pour une recherche avancée, créez un index textuel sur 'content'
    cursor = messages_col.find({
        'conversation_id': conversation_id,
        'deleted_for': {'$ne': g.current_user_id},
        'content': {'$regex': query_text, '$options': 'i'}
    }).sort('timestamp', -1).limit(limit)
    
    messages = list(cursor)
    
    for msg in messages:
        msg['_id'] = str(msg['_id'])
    
    return jsonify(messages), 200


@messages_bp.route('/<conversation_id>/messages/<message_id>', methods=['GET'])
@require_auth
@swag_from('../descriptions/messages/get_message.yml')
def get_message(conversation_id, message_id):
    """
    Récupère un message spécifique
    """
    # Vérifier l'accès
    participant = execute_query(sql.SQL("""
        SELECT 1 FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
    """), (conversation_id, g.current_user_id), fetch_one=True)
    
    if not participant:
        return jsonify({'error': 'Non autorisé'}), 403
    
    # Récupérer le message
    try:
        message = messages_col.find_one({
            '_id': ObjectId(message_id),
            'conversation_id': conversation_id,
            'deleted_for': {'$ne': g.current_user_id}
        })
    except:
        return jsonify({'error': 'ID de message invalide'}), 400
    
    if not message:
        return jsonify({'error': 'Message non trouvé'}), 404
    
    message['_id'] = str(message['_id'])
    
    return jsonify(message), 200


@messages_bp.route('/<conversation_id>/messages', methods=['POST'])
@require_auth
@swag_from('../descriptions/messages/send_message.yml')
def send_message_rest(conversation_id):
    """
    Envoie un message (fallback REST)
    """
    data = request.get_json()
    
    msg_type = data.get('type', 'text')
    content = data.get('content')
    reply_to_id = data.get('reply_to_id')
    
    if not content:
        return jsonify({'error': 'Contenu du message requis'}), 400
    
    # Vérifier l'accès
    participant = execute_query(sql.SQL("""
        SELECT unread_count FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
    """), (conversation_id, g.current_user_id), fetch_one=True)
    
    if not participant:
        return jsonify({'error': 'Non autorisé'}), 403
    
    # Vérifier si en sourdine
    muted_until = execute_query(sql.SQL("""
        SELECT muted_until FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id = %s
    """), (conversation_id, g.current_user_id), fetch_one=True)
    
    if muted_until and muted_until.get('muted_until') and muted_until['muted_until'] > datetime.utcnow():
        return jsonify({'error': 'Conversation en sourdine'}), 403
    
    # Créer le message
    message = {
        'conversation_id': conversation_id,
        'sender_id': g.current_user_id,
        'type': msg_type,
        'content': content,
        'timestamp': datetime.utcnow(),
        'read_by': [g.current_user_id],
        'deleted_for': [],
        'reactions': []
    }
    
    if reply_to_id:
        message['reply_to_id'] = reply_to_id
    
    result = messages_col.insert_one(message)
    message_id = str(result.inserted_id)
    
    # Incrémenter unread_count des autres participants
    execute_query(sql.SQL("""
        UPDATE gbekoun.conversation_participants
        SET unread_count = unread_count + 1
        WHERE conversation_id = %s AND user_id != %s
    """), (conversation_id, g.current_user_id))
    
    # Mettre à jour le timestamp de la conversation
    execute_query(sql.SQL("""
        UPDATE gbekoun.conversations
        SET updated_at = NOW()
        WHERE id = %s
    """), (conversation_id,))
    
    return jsonify({
        'message_id': message_id,
        'timestamp': message['timestamp']
    }), 201


@messages_bp.route('/<conversation_id>/messages/<message_id>', methods=['PUT'])
@require_auth
@swag_from('../descriptions/messages/edit_message.yml')
def edit_message(conversation_id, message_id):
    """
    Modifie un message
    """
    data = request.get_json()
    new_content = data.get('content')
    
    if not new_content:
        return jsonify({'error': 'Nouveau contenu requis'}), 400
    
    # Vérifier l'accès et que l'utilisateur est l'auteur
    try:
        message = messages_col.find_one({
            '_id': ObjectId(message_id),
            'conversation_id': conversation_id
        })
    except:
        return jsonify({'error': 'ID de message invalide'}), 400
    
    if not message:
        return jsonify({'error': 'Message non trouvé'}), 404
    
    if message['sender_id'] != g.current_user_id:
        return jsonify({'error': 'Vous ne pouvez modifier que vos propres messages'}), 403
    
    # Modifier le message
    result = messages_col.update_one(
        {'_id': ObjectId(message_id)},
        {
            '$set': {
                'content': new_content,
                'edited_at': datetime.utcnow()
            }
        }
    )
    
    if result.modified_count > 0:
        return jsonify({'message': 'Message modifié'}), 200
    
    return jsonify({'error': 'Erreur lors de la modification'}), 500


@messages_bp.route('/<conversation_id>/messages/<message_id>', methods=['DELETE'])
@require_auth
@swag_from('../descriptions/messages/delete_message.yml')
def delete_message(conversation_id, message_id):
    """
    Supprime un message
    """
    for_everyone = request.args.get('for_everyone', 'false').lower() == 'true'
    
    # Vérifier l'accès
    try:
        message = messages_col.find_one({
            '_id': ObjectId(message_id),
            'conversation_id': conversation_id
        })
    except:
        return jsonify({'error': 'ID de message invalide'}), 400
    
    if not message:
        return jsonify({'error': 'Message non trouvé'}), 404
    
    if for_everyone:
        # Vérifier que l'utilisateur est l'auteur
        if message['sender_id'] != g.current_user_id:
            return jsonify({'error': 'Vous ne pouvez supprimer que vos propres messages pour tout le monde'}), 403
        
        # Supprimer complètement le message
        result = messages_col.delete_one({'_id': ObjectId(message_id)})
    else:
        # Soft delete : ajouter l'utilisateur à deleted_for
        result = messages_col.update_one(
            {'_id': ObjectId(message_id)},
            {'$addToSet': {'deleted_for': g.current_user_id}}
        )
    
    if result.modified_count > 0 or result.deleted_count > 0:
        return jsonify({'message': 'Message supprimé'}), 200
    
    return jsonify({'error': 'Erreur lors de la suppression'}), 500


# ===================== RÉACTIONS =====================

@messages_bp.route('/<conversation_id>/messages/<message_id>/react', methods=['POST'])
@require_auth
@swag_from('../descriptions/messages/add_reaction.yml')
def add_reaction(conversation_id, message_id):
    """
    Ajoute une réaction à un message
    """
    data = request.get_json()
    reaction = data.get('reaction')
    
    if not reaction:
        return jsonify({'error': 'Réaction requise'}), 400
    
    # Vérifier l'accès à la conversation
    participant = execute_query(sql.SQL("""
        SELECT 1 FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
    """), (conversation_id, g.current_user_id), fetch_one=True)
    
    if not participant:
        return jsonify({'error': 'Non autorisé'}), 403
    
    # Vérifier que le message existe
    try:
        message = messages_col.find_one({'_id': ObjectId(message_id), 'conversation_id': conversation_id})
    except:
        return jsonify({'error': 'ID de message invalide'}), 400
    
    if not message:
        return jsonify({'error': 'Message non trouvé'}), 404
    
    # Remplacer la réaction de l'utilisateur en une seule opération atomique.
    messages_col.update_one(
        {'_id': ObjectId(message_id)},
        [
            {
                '$set': {
                    'reactions': {
                        '$concatArrays': [
                            {
                                '$filter': {
                                    'input': {'$ifNull': ['$reactions', []]},
                                    'as': 'existing_reaction',
                                    'cond': {
                                        '$ne': [
                                            '$$existing_reaction.user_id',
                                            g.current_user_id
                                        ]
                                    }
                                }
                            },
                            [{'user_id': g.current_user_id, 'reaction': reaction}]
                        ]
                    }
                }
            }
        ]
    )
    
    return jsonify({'message': 'Réaction ajoutée'}), 200


@messages_bp.route('/<conversation_id>/messages/<message_id>/react', methods=['DELETE'])
@require_auth
@swag_from('../descriptions/messages/remove_reaction.yml')
def remove_reaction(conversation_id, message_id):
    """
    Supprime une réaction
    """
    # Vérifier l'accès
    participant = execute_query(sql.SQL("""
        SELECT 1 FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
    """), (conversation_id, g.current_user_id), fetch_one=True)
    
    if not participant:
        return jsonify({'error': 'Non autorisé'}), 403
    
    # Supprimer la réaction
    result = messages_col.update_one(
        {'_id': ObjectId(message_id)},
        {'$pull': {'reactions': {'user_id': g.current_user_id}}}
    )
    
    return jsonify({'message': 'Réaction supprimée'}), 200


# ===================== TRANSFERT =====================

@messages_bp.route('/<conversation_id>/messages/<message_id>/forward', methods=['POST'])
@require_auth
@swag_from('../descriptions/messages/forward_message.yml')
def forward_message(conversation_id, message_id):
    """
    Transfère un message vers d'autres conversations
    """
    data = request.get_json()
    target_conversation_ids = data.get('conversation_ids', [])
    
    if not target_conversation_ids:
        return jsonify({'error': 'Liste de conversations cible requise'}), 400
    
    # Récupérer le message original
    try:
        original = messages_col.find_one({
            '_id': ObjectId(message_id),
            'conversation_id': conversation_id
        })
    except:
        return jsonify({'error': 'ID de message invalide'}), 400
    
    if not original:
        return jsonify({'error': 'Message non trouvé'}), 404
    
    forwarded_messages = []
    
    for target_conv_id in target_conversation_ids:
        # Vérifier l'accès à la conversation cible
        participant = execute_query(sql.SQL("""
            SELECT 1 FROM gbekoun.conversation_participants
            WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
        """), (target_conv_id, g.current_user_id), fetch_one=True)
        
        if not participant:
            continue
        
        # Créer le message transféré
        new_message = {
            'conversation_id': target_conv_id,
            'sender_id': g.current_user_id,
            'type': original['type'],
            'content': original['content'],
            'timestamp': datetime.utcnow(),
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
        forwarded_messages.append({
            'conversation_id': target_conv_id,
            'message_id': str(result.inserted_id)
        })
        
        # Incrémenter unread_count
        execute_query(sql.SQL("""
            UPDATE gbekoun.conversation_participants
            SET unread_count = unread_count + 1
            WHERE conversation_id = %s AND user_id != %s
        """), (target_conv_id, g.current_user_id))
    
    return jsonify({
        'forwarded': forwarded_messages,
        'count': len(forwarded_messages)
    }), 200


# ===================== MESSAGES ÉPINGLÉS =====================

@messages_bp.route('/starred', methods=['GET'])
@require_auth
@swag_from('../descriptions/messages/get_starred.yml')
def get_starred_messages():
    """
    Messages épinglés/importants
    """
    limit = request.args.get('limit', 50, type=int)
    
    # Récupérer les messages épinglés pour cet utilisateur
    # Note: Les messages épinglés sont stockés dans PostgreSQL
    starred = execute_query(sql.SQL("""
        SELECT sm.message_id, sm.conversation_id, sm.starred_at,
               c.name as conversation_name, c.type as conversation_type
        FROM gbekoun.starred_messages sm
        JOIN gbekoun.conversations c ON c.id = sm.conversation_id
        WHERE sm.user_id = %s
        ORDER BY sm.starred_at DESC
        LIMIT %s
    """), (g.current_user_id, limit), fetch_all=True)
    
    # Récupérer le contenu des messages depuis MongoDB
    for item in starred:
        try:
            message = messages_col.find_one({'_id': ObjectId(item['message_id'])})
            if message:
                item['content'] = message.get('content')
                item['type'] = message.get('type')
                item['sender_id'] = message.get('sender_id')
                item['timestamp'] = message.get('timestamp')
        except:
            item['content'] = '[Message non trouvé]'
    
    return jsonify(starred), 200


@messages_bp.route('/<conversation_id>/messages/<message_id>/star', methods=['POST'])
@require_auth
@swag_from('../descriptions/messages/star_message.yml')
def star_message(conversation_id, message_id):
    """
    Épingle un message
    """
    # Vérifier l'accès à la conversation
    participant = execute_query(sql.SQL("""
        SELECT 1 FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
    """), (conversation_id, g.current_user_id), fetch_one=True)
    
    if not participant:
        return jsonify({'error': 'Non autorisé'}), 403
    
    # Vérifier que le message existe
    try:
        message = messages_col.find_one({'_id': ObjectId(message_id), 'conversation_id': conversation_id})
    except:
        return jsonify({'error': 'ID de message invalide'}), 400
    
    if not message:
        return jsonify({'error': 'Message non trouvé'}), 404
    
    # Ajouter aux épinglés
    execute_query(sql.SQL("""
        INSERT INTO gbekoun.starred_messages (user_id, conversation_id, message_id)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, message_id) DO NOTHING
    """), (g.current_user_id, conversation_id, message_id))
    
    return jsonify({'message': 'Message épinglé'}), 200


@messages_bp.route('/<conversation_id>/messages/<message_id>/star', methods=['DELETE'])
@require_auth
@swag_from('../descriptions/messages/unstar_message.yml')
def unstar_message(conversation_id, message_id):
    """
    Désépingle un message
    """
    execute_query(sql.SQL("""
        DELETE FROM gbekoun.starred_messages
        WHERE user_id = %s AND conversation_id = %s AND message_id = %s
    """), (g.current_user_id, conversation_id, message_id))
    
    return jsonify({'message': 'Message désépinglé'}), 200


# ===================== MENTIONS =====================

@messages_bp.route('/mentions', methods=['GET'])
@require_auth
@swag_from('../descriptions/messages/get_mentions.yml')
def get_mentions():
    """
    Messages où l'utilisateur est mentionné
    """
    limit = request.args.get('limit', 50, type=int)
    only_unread = request.args.get('only_unread', 'false').lower() == 'true'
    
    # Recherche des mentions dans MongoDB
    # Format de mention: @[user_id] ou @username
    query = {
        'deleted_for': {'$ne': g.current_user_id},
        'content': {'$regex': f'@\\[?{g.current_user_id}\\]?|@{g.current_user_id}'}
    }
    
    cursor = messages_col.find(query).sort('timestamp', -1).limit(limit)
    messages = list(cursor)
    
    # Filtrer les messages non lus si demandé
    if only_unread:
        messages = [m for m in messages if g.current_user_id not in m.get('read_by', [])]
    
    # Ajouter les infos de conversation
    for msg in messages:
        msg['_id'] = str(msg['_id'])
        conv = execute_query(sql.SQL("""
            SELECT name, type FROM gbekoun.conversations WHERE id = %s
        """), (msg['conversation_id'],), fetch_one=True)
        msg['conversation'] = conv
    
    return jsonify(messages), 200