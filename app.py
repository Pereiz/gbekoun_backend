# app.py
import os
from datetime import datetime, timedelta
from functools import wraps
import certifi
import jwt
#import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool
from pymongo import MongoClient
from flask import Flask, request, jsonify, g
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room
from dotenv import load_dotenv
from http import HTTPStatus
from flasgger import Swagger, swag_from

load_dotenv()

# -------------------------------
# Configuration
# -------------------------------
class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key')
    JWT_SECRET = os.getenv('JWT_SECRET', 'jwt-secret-key')
    JWT_EXPIRATION_DAYS = 30

    PG_HOST = os.getenv('PG_HOST', 'localhost')
    PG_PORT = os.getenv('PG_PORT', '5435')
    PG_DATABASE = os.getenv('PG_DATABASE', 'gbekoundb')
    PG_USER = os.getenv('PG_USER', 'gbekoundb_user')
    PG_PASSWORD = os.getenv('PG_PASSWORD', 'postgres_password_2024')
    PG_SCHEMA = 'gbekoun'

    MONGO_URI = os.getenv('MONGO_URI', 'mongodb://gbekoundb_user:gbekoundb_password_2024@localhost:27018/gbekoundb_messages?authSource=gbekoundb_messages')
    MONGO_DB = os.getenv('MONGO_DB', 'gbekoundb_messages')

    UPLOAD_FOLDER = 'uploads'
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024

# -------------------------------
# PostgreSQL pool
# -------------------------------
pg_pool = SimpleConnectionPool(
    minconn=1,
    maxconn=10,
    host=Config.PG_HOST,
    port=Config.PG_PORT,
    dbname=Config.PG_DATABASE,
    user=Config.PG_USER,
    password=Config.PG_PASSWORD
)

def get_db_connection():
    return pg_pool.getconn()

def put_db_connection(conn):
    pg_pool.putconn(conn)

def execute_query(query, params=None, fetch_one=False, fetch_all=False):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            if fetch_one:
                result = cur.fetchone()
            elif fetch_all:
                result = cur.fetchall()
            else:
                result = None
            conn.commit()
            return result
    finally:
        put_db_connection(conn)

# -------------------------------
# MongoDB connection
# -------------------------------
mongo_client = MongoClient(Config.MONGO_URI)
mongo_db = mongo_client[Config.MONGO_DB]
messages_col = mongo_db['messages']
media_col = mongo_db['media']

# -------------------------------
# JWT helpers
# -------------------------------
def generate_token(user_id):
    payload = {
        'user_id': str(user_id),
        'exp': datetime.utcnow() + timedelta(days=Config.JWT_EXPIRATION_DAYS),
        'iat': datetime.utcnow()
    }
    return jwt.encode(payload, Config.JWT_SECRET, algorithm='HS256')

def verify_token(token):
    try:
        if token.startswith('Bearer '):
            token = token[7:]
        payload = jwt.decode(token, Config.JWT_SECRET, algorithms=['HS256'])
        return payload['user_id']
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'error': 'Token manquant'}), 401
        user_id = verify_token(token)
        if not user_id:
            return jsonify({'error': 'Token invalide ou expiré'}), 401
        g.current_user_id = user_id
        return f(*args, **kwargs)
    return decorated

# -------------------------------
# Flask app & SocketIO
# -------------------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = Config.SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = Config.MAX_CONTENT_LENGTH
CORS(app, resources={r"/*": {"origins": "*"}})
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

from sockets.events import *

# Configuration Swagger
app.config['SWAGGER'] = {
    'title': 'GBEKOUN Messaging API',
    'version': '1.0.0',
    'description': '''
    API de messagerie temps réel pour GBEKOUN.
    
    ## Fonctionnalités
    - Authentification par codes d'invitation
    - Messages texte et médias
    - Conversations 1-1 et groupes
    - WebSocket pour le temps réel
    
    ## Authentification
    Utilisez le token JWT obtenu via `/api/auth/login` ou `/api/auth/register`
    dans l'en-tête: `Authorization: Bearer votre_token`
    ''',
    'termsOfService': '/terms',
    'contact': {
        'name': 'Support GBEKOUN',
        'email': 'interfceprimus@gmail.com'
    },
    'license': {
        'name': 'MIT',
        'url': 'https://opensource.org/licenses/MIT'
    },
    'securityDefinitions': {
        'BearerAuth': {
            'type': 'apiKey',
            'name': 'Authorization',
            'in': 'header',
            'description': 'JWT Token (ex: Bearer votre_token_ici)'
        }
    },
    'security': [
        {
            'BearerAuth': []
        }
    ],
    'specs': [
        {
            'endpoint': 'apispec',
            'route': '/apispec.json',
            'rule_filter': lambda rule: True,
            'model_filter': lambda tag: True,
        }
    ],
    'static_url_path': '/flasgger_static',
    'swagger_ui': True,
    'specs_route': '/apidocs/'
}

swagger = Swagger(app)

# -------------------------------
# Import des blueprints (routes)
# -------------------------------
from blueprints.auth import auth_bp
from blueprints.conversations import conversations_bp
from blueprints.messages import messages_bp
from blueprints.admin import admin_bp
from blueprints.users import users_bp
from blueprints.status import status_bp
from blueprints.utils import utils_bp


# # -------------------------------
# # Enregistrement des blueprints
# # -------------------------------
app.register_blueprint(auth_bp, url_prefix='/api/auth')
app.register_blueprint(conversations_bp, url_prefix='/api/conversations')
app.register_blueprint(messages_bp, url_prefix='/api/messages')
app.register_blueprint(admin_bp)
app.register_blueprint(users_bp)
app.register_blueprint(status_bp)
app.register_blueprint(utils_bp)


# -------------------------------
# Routes simples
# -------------------------------
@app.route("/gbekoun/welcome", methods=['GET'])
def welcome():
    return jsonify({"Message": "Bonjour, vous êtes sur GBEKOUN !"}), HTTPStatus.OK

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'}), 200

# -------------------------------
# WebSocket events
# -------------------------------
active_users = {}

@socketio.on('connect')
def handle_connect():
    token = request.args.get('token')
    if not token:
        return False
    
    user_id = verify_token(token)
    if not user_id:
        return False
    
    if user_id not in active_users:
        active_users[user_id] = []
    active_users[user_id].append(request.sid)
    
    join_room(f"user_{user_id}")
    
    convs = execute_query(
        "SELECT conversation_id FROM gbekoun.conversation_participants WHERE user_id = %s",
        (user_id,), fetch_all=True
    )
    for conv in convs:
        join_room(f"conv_{conv['conversation_id']}")
    
    execute_query(
        "UPDATE gbekoun.users SET is_online = TRUE, last_seen = NOW() WHERE id = %s",
        (user_id,)
    )
    
    emit('connected', {'message': 'Connecté', 'user_id': user_id})
    return True

@socketio.on('disconnect')
def handle_disconnect():
    for uid, sids in active_users.items():
        if request.sid in sids:
            sids.remove(request.sid)
            if not sids:
                del active_users[uid]
                execute_query(
                    "UPDATE gbekoun.users SET is_online = FALSE, last_seen = NOW() WHERE id = %s",
                    (uid,)
                )
            break

@socketio.on('send_message')
def handle_send_message(data):
    token = request.args.get('token')
    user_id = verify_token(token)
    if not user_id:
        emit('error', {'message': 'Non authentifié'})
        return
    
    conversation_id = data.get('conversation_id')
    msg_type = data.get('type', 'text')
    content = data.get('content')
    
    part = execute_query(
        "SELECT 1 FROM gbekoun.conversation_participants WHERE conversation_id = %s AND user_id = %s",
        (conversation_id, user_id), fetch_one=True
    )
    if not part:
        emit('error', {'message': 'Non autorisé'})
        return
    
    message = {
        'conversation_id': conversation_id,
        'sender_id': user_id,
        'type': msg_type,
        'content': content,
        'timestamp': datetime.utcnow(),
        'read_by': [],
        'deleted_for': []
    }
    result = messages_col.insert_one(message)
    msg_id = str(result.inserted_id)
    message['_id'] = msg_id
    
    execute_query(
        "UPDATE gbekoun.conversation_participants SET unread_count = unread_count + 1 WHERE conversation_id = %s AND user_id != %s",
        (conversation_id, user_id)
    )
    
    emit('new_message', message, room=f"conv_{conversation_id}", include_self=False)
    emit('message_sent', {'message_id': msg_id, 'timestamp': message['timestamp']})

@socketio.on('typing')
def handle_typing(data):
    token = request.args.get('token')
    user_id = verify_token(token)
    if not user_id:
        return
    
    conversation_id = data.get('conversation_id')
    is_typing = data.get('is_typing', True)
    
    emit('user_typing', {
        'user_id': user_id,
        'conversation_id': conversation_id,
        'is_typing': is_typing
    }, room=f"conv_{conversation_id}", include_self=False)

@socketio.on('mark_read')
def handle_mark_read(data):
    token = request.args.get('token')
    user_id = verify_token(token)
    if not user_id:
        return
    
    conversation_id = data.get('conversation_id')
    message_id = data.get('message_id')
    
    if message_id:
        from bson.objectid import ObjectId
        messages_col.update_one(
            {'_id': ObjectId(message_id)},
            {'$addToSet': {'read_by': user_id}}
        )
    
    execute_query(
        "UPDATE gbekoun.conversation_participants SET unread_count = 0, last_read_message_id = %s WHERE conversation_id = %s AND user_id = %s",
        (message_id, conversation_id, user_id)
    )
    
    emit('message_read', {'conversation_id': conversation_id, 'user_id': user_id, 'message_id': message_id},
         room=f"conv_{conversation_id}", include_self=False)


# # Événement WebSocket pour la présence
# @socketio.on('presence')
# def handle_presence(data):
#     """Gère la présence en temps réel via WebSocket"""
#     token = request.args.get('token')
#     user_id = verify_token(token)
#     if not user_id:
#         return
    
#     is_online = data.get('is_online', True)
    
#     # Mettre à jour le statut
#     if is_online:
#         heartbeats[user_id] = datetime.utcnow()
    
#     # Notifier les conversations
#     conversations = execute_query("""
#         SELECT DISTINCT conversation_id 
#         FROM gbekoun.conversation_participants 
#         WHERE user_id = %s
#     """, (user_id,), fetch_all=True)
    
#     for conv in conversations:
#         emit('user_presence', {
#             'user_id': user_id,
#             'is_online': is_online,
#             'last_seen': datetime.utcnow().isoformat()
#         }, room=f"conv_{conv['conversation_id']}", include_self=False)


# Nettoyage automatique des utilisateurs inactifs
import threading
import time
from blueprints.status import cleanup_inactive_users

def start_cleanup_thread():
    """Thread de nettoyage des utilisateurs inactifs"""
    while True:
        time.sleep(60)  # Nettoyer toutes les minutes
        cleanup_inactive_users()

# Démarrer le thread de nettoyage
cleanup_thread = threading.Thread(target=start_cleanup_thread, daemon=True)
cleanup_thread.start()


# -------------------------------
# Main
# -------------------------------
if __name__ == '__main__':
    print("Démarrage de l'application Flask avec SocketIO")
    print("Serveur démarré sur http://0.0.0.0:8880")
    socketio.run(app, host='0.0.0.0', port=8880, debug=True)