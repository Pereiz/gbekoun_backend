# blueprints/status.py
from flask import Blueprint, request, jsonify, g
from flasgger import swag_from
from datetime import datetime, timedelta
from functools import wraps
from psycopg2 import sql
from app import execute_query, require_auth

status_bp = Blueprint('status', __name__, url_prefix='/api/status')


# ===================== CONFIGURATION =====================
# Délai d'inactivité avant de considérer l'utilisateur offline (secondes)
OFFLINE_TIMEOUT = 60  # 1 minute

# Stockage en mémoire des derniers heartbeats (peut être remplacé par Redis en production)
# Structure: { user_id: last_heartbeat_timestamp }
heartbeats = {}


# ===================== FONCTIONS UTILITAIRES =====================

def get_user_status(user_id, current_user_id=None):
    """
    Récupère le statut d'un utilisateur en respectant les paramètres de confidentialité
    """
    # Récupérer les infos de l'utilisateur
    user = execute_query(sql.SQL("""
        SELECT is_online, last_seen, privacy_last_seen, id
        FROM gbekoun.users 
        WHERE id = %s AND deleted_at IS NULL
    """), (user_id,), fetch_one=True)
    
    if not user:
        return None
    
    # Vérifier si l'utilisateur est en ligne via heartbeat
    is_online = user['is_online']
    if is_online and user_id in heartbeats:
        # Vérifier si le dernier heartbeat est récent
        last_heartbeat = heartbeats.get(user_id)
        if last_heartbeat and datetime.utcnow() - last_heartbeat > timedelta(seconds=OFFLINE_TIMEOUT):
            is_online = False
            # Mettre à jour la base de données
            execute_query(
                sql.SQL("""UPDATE gbekoun.users SET is_online = FALSE, last_seen = NOW() WHERE id = %s"""),
                (user_id,)
            )
    
    # Appliquer les règles de confidentialité
    show_last_seen = True
    if current_user_id and current_user_id != user_id:
        privacy = user['privacy_last_seen']
        if privacy == 'nobody':
            show_last_seen = False
        elif privacy == 'contacts':
            # Vérifier si les utilisateurs sont en contact (conversation directe)
            are_contacts = execute_query(sql.SQL("""
                SELECT 1 FROM gbekoun.conversation_participants cp1
                JOIN gbekoun.conversation_participants cp2 ON cp2.conversation_id = cp1.conversation_id
                JOIN gbekoun.conversations c ON c.id = cp1.conversation_id
                WHERE c.type = 'direct' 
                AND cp1.user_id = %s AND cp2.user_id = %s
            """), (current_user_id, user_id), fetch_one=True)
            
            if not are_contacts:
                show_last_seen = False
    
    return {
        'user_id': user_id,
        'is_online': is_online,
        'last_seen': user['last_seen'] if show_last_seen and user['last_seen'] else None
    }


def update_conversation_presence(conversation_id, user_id, is_online):
    """
    Met à jour la présence dans une conversation et notifie les participants
    """
    # Récupérer les participants de la conversation
    participants = execute_query(sql.SQL("""
        SELECT user_id FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id != %s AND deleted_at IS NULL
    """), (conversation_id, user_id), fetch_all=True)
    
    return [p['user_id'] for p in participants] if participants else []


# ===================== ROUTES =====================

@status_bp.route('/users/<user_id>', methods=['GET'])
@require_auth
@swag_from('../descriptions/status/get_user_status.yml')
def get_users_status(user_id):
    """
    Statut d'un utilisateur (online/last_seen)
    """
    # Vérifier si l'utilisateur existe
    user_exists = execute_query(
        sql.SQL("""SELECT id FROM gbekoun.users WHERE id = %s AND deleted_at IS NULL"""),
        (user_id,), fetch_one=True
    )
    if not user_exists:
        return jsonify({'error': 'Utilisateur non trouvé'}), 404
    
    # Vérifier si l'utilisateur courant a bloqué l'utilisateur demandé
    is_blocked = execute_query(sql.SQL("""
        SELECT 1 FROM gbekoun.blocked_users 
        WHERE blocker_id = %s AND blocked_id = %s
    """), (g.current_user_id, user_id), fetch_one=True)
    
    if is_blocked:
        return jsonify({'error': 'Utilisateur non trouvé'}), 404
    
    # Vérifier si l'utilisateur demandé a bloqué l'utilisateur courant
    is_blocker = execute_query(sql.SQL("""
        SELECT 1 FROM gbekoun.blocked_users 
        WHERE blocker_id = %s AND blocked_id = %s
    """), (user_id, g.current_user_id), fetch_one=True)
    
    if is_blocker:
        return jsonify({
            'user_id': user_id,
            'is_online': False,
            'last_seen': None,
            'blocked_you': True
        }), 200
    
    # Récupérer le statut
    status = get_user_status(user_id, g.current_user_id)
    
    return jsonify(status), 200


@status_bp.route('/conversation/<conversation_id>', methods=['GET'])
@require_auth
@swag_from('../descriptions/status/get_conversation_status.yml')
def get_conversation_status(conversation_id):
    """
    Statuts de tous les participants d'une conversation
    """
    # Vérifier que l'utilisateur fait partie de la conversation
    participant = execute_query(sql.SQL("""
        SELECT 1 FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND user_id = %s AND deleted_at IS NULL
    """), (conversation_id, g.current_user_id), fetch_one=True)
    
    if not participant:
        return jsonify({'error': 'Conversation non trouvée'}), 404
    
    # Récupérer tous les participants
    participants = execute_query(sql.SQL("""
        SELECT user_id FROM gbekoun.conversation_participants
        WHERE conversation_id = %s AND deleted_at IS NULL
    """), (conversation_id,), fetch_all=True)
    
    # Récupérer le statut de chaque participant
    statuses = []
    for p in participants:
        if p['user_id'] == g.current_user_id:
            continue
        
        # Vérifier les blocages
        is_blocked = execute_query(sql.SQL("""
            SELECT 1 FROM gbekoun.blocked_users 
            WHERE blocker_id = %s AND blocked_id = %s
        """), (g.current_user_id, p['user_id']), fetch_one=True)
        
        if not is_blocked:
            status = get_user_status(p['user_id'], g.current_user_id)
            if status:
                statuses.append(status)
    
    return jsonify({
        'conversation_id': conversation_id,
        'participants': statuses,
        'total': len(statuses)
    }), 200


@status_bp.route('/online', methods=['POST'])
@require_auth
@swag_from('../descriptions/status/update_online.yml')
def update_online():
    """
    Met à jour son statut online
    """
    data = request.get_json()
    is_online = data.get('is_online', True)
    
    # Mettre à jour la base de données
    if is_online:
        execute_query(sql.SQL("""
            UPDATE gbekoun.users 
            SET is_online = TRUE, last_seen = NOW() 
            WHERE id = %s
        """), (g.current_user_id,))
        
        # Mettre à jour le heartbeat
        heartbeats[g.current_user_id] = datetime.utcnow()
    else:
        execute_query(sql.SQL("""
            UPDATE gbekoun.users 
            SET is_online = FALSE, last_seen = NOW() 
            WHERE id = %s
        """), (g.current_user_id,))
        
        # Supprimer le heartbeat
        if g.current_user_id in heartbeats:
            del heartbeats[g.current_user_id]
    
    # Notifier les contacts que le statut a changé
    # Récupérer toutes les conversations de l'utilisateur
    conversations = execute_query(sql.SQL("""
        SELECT DISTINCT conversation_id 
        FROM gbekoun.conversation_participants 
        WHERE user_id = %s AND deleted_at IS NULL
    """), (g.current_user_id,), fetch_all=True)
    
    # Ici, vous pouvez utiliser WebSocket pour notifier les participants
    # from flask_socketio import emit
    # for conv in conversations:
    #     emit('user_presence', {
    #         'user_id': g.current_user_id,
    #         'is_online': is_online,
    #         'last_seen': datetime.utcnow().isoformat()
    #     }, room=f"conv_{conv['conversation_id']}", include_self=False)
    
    return jsonify({
        'message': f'Statut mis à jour: {"en ligne" if is_online else "hors ligne"}',
        'is_online': is_online,
        'last_seen': datetime.utcnow().isoformat()
    }), 200


@status_bp.route('/heartbeat', methods=['POST'])
@require_auth
@swag_from('../descriptions/status/heartbeat.yml')
def heartbeat():
    """
    Keep-alive pour maintenir le statut en ligne
    """
    # Mettre à jour le timestamp du dernier heartbeat
    heartbeats[g.current_user_id] = datetime.utcnow()
    
    # Mettre à jour last_seen dans la base de données
    execute_query(sql.SQL("""
        UPDATE gbekoun.users 
        SET last_seen = NOW() 
        WHERE id = %s AND is_online = TRUE
    """), (g.current_user_id,))
    
    return jsonify({
        'message': 'Heartbeat reçu',
        'timestamp': datetime.utcnow().isoformat(),
        'status': 'online'
    }), 200


# ===================== FONCTION DE NETTOYAGE (à appeler périodiquement) =====================

def cleanup_inactive_users():
    """
    Nettoie les utilisateurs inactifs (à appeler dans un thread séparé)
    """
    current_time = datetime.utcnow()
    inactive_users = []
    
    for user_id, last_heartbeat in list(heartbeats.items()):
        if current_time - last_heartbeat > timedelta(seconds=OFFLINE_TIMEOUT):
            inactive_users.append(user_id)
            del heartbeats[user_id]
    
    if inactive_users:
        # Mettre à jour la base de données
        for user_id in inactive_users:
            execute_query(sql.SQL("""
                UPDATE gbekoun.users 
                SET is_online = FALSE, last_seen = NOW() 
                WHERE id = %s
            """), (user_id,))
        
        print(f"Nettoyage: {len(inactive_users)} utilisateurs marqués hors ligne")
    
    return len(inactive_users)


# ===================== TÂCHE DE NETTOYAGE AUTOMATIQUE =====================
# Pour lancer un thread de nettoyage, ajoutez ceci dans app.py:
#
# import threading
# import time
# from blueprints.status import cleanup_inactive_users
#
# def start_cleanup_thread():
#     while True:
#         time.sleep(60)  # Nettoyer toutes les minutes
#         cleanup_inactive_users()
#
# cleanup_thread = threading.Thread(target=start_cleanup_thread, daemon=True)
# cleanup_thread.start()