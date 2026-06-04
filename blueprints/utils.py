# blueprints/utils.py
import os
import re
import platform
from datetime import datetime
from flask import Blueprint, request, jsonify
from flasgger import swag_from
from psycopg2 import sql
from app import socketio

utils_bp = Blueprint('utils', __name__, url_prefix='/api')


# ===================== CONFIGURATION =====================

@utils_bp.route('/config', methods=['GET'])
@swag_from('../descriptions/utils/config.yml')
def get_config():
    """
    Configuration client (limites, features)
    """
    config = {
        'app': {
            'name': 'GBEKOUN Messaging',
            'version': '1.0.0',
            'environment': os.getenv('FLASK_ENV', 'development')
        },
        'limits': {
            'max_message_length': 5000,
            'max_media_size_mb': 10,
            'max_group_participants': 100,
            'max_conversations_per_user': 500,
            'message_edit_timeout_seconds': 3600,  # 1 heure
            'message_delete_timeout_seconds': 86400,  # 24 heures
            'typing_indicator_timeout_seconds': 5,
            'heartbeat_interval_seconds': 30,
            'offline_timeout_seconds': 60
        },
        'features': {
            'voice_messages': True,
            'video_calls': False,
            'file_sharing': True,
            'location_sharing': True,
            'contact_sharing': True,
            'group_calls': False,
            'end_to_end_encryption': False,
            'message_reactions': True,
            'message_forwarding': True,
            'message_starring': True,
            'message_mentions': True,
            'read_receipts': True,
            'typing_indicators': True,
            'push_notifications': False,  # Désactivé volontairement
            'backup_export': True,
            'account_deletion': True
        },
        'media': {
            'allowed_images': ['png', 'jpg', 'jpeg', 'gif', 'webp'],
            'allowed_videos': ['mp4', 'webm', 'mov'],
            'allowed_audio': ['mp3', 'aac', 'ogg', 'wav'],
            'allowed_documents': ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'txt'],
            'max_avatar_size_mb': 2,
            'avatar_dimensions': {'width': 512, 'height': 512}
        },
        'pagination': {
            'default_limit': 50,
            'max_limit': 200,
            'messages_default_limit': 50,
            'messages_max_limit': 200,
            'conversations_default_limit': 50,
            'conversations_max_limit': 200
        },
        'websocket': {
            'url': '/socket.io/',
            'transports': ['websocket', 'polling'],
            'reconnection_attempts': 5,
            'reconnection_delay': 1000,
            'reconnection_delay_max': 5000,
            'timeout': 20000
        }
    }
    
    return jsonify(config), 200


# ===================== SANTÉ =====================

@utils_bp.route('/health', methods=['GET'])
@swag_from('../descriptions/utils/health.yml')
def health_check():
    """
    Vérification santé du backend
    """
    status = {
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'services': {}
    }
    
    # Vérifier PostgreSQL
    try:
        from app import execute_query
        result = execute_query(sql.SQL("""SELECT 1 as connected"""), fetch_one=True)
        status['services']['postgresql'] = {
            'status': 'up' if result and result.get('connected') == 1 else 'down',
            'message': 'Connected'
        }
    except Exception as e:
        status['services']['postgresql'] = {
            'status': 'down',
            'message': str(e)
        }
        status['status'] = 'degraded'
    
    # Vérifier MongoDB
    try:
        from app import mongo_client
        mongo_client.admin.command('ping')
        status['services']['mongodb'] = {
            'status': 'up',
            'message': 'Connected'
        }
    except Exception as e:
        status['services']['mongodb'] = {
            'status': 'down',
            'message': str(e)
        }
        status['status'] = 'degraded'
    
    # Vérifier WebSocket
    try:
        from app import socketio
        status['services']['websocket'] = {
            'status': 'up',
            'message': 'SocketIO ready'
        }
    except Exception as e:
        status['services']['websocket'] = {
            'status': 'down',
            'message': str(e)
        }
        status['status'] = 'degraded'
    
    # Vérifier l'espace disque (optionnel)
    try:
        import shutil
        disk_usage = shutil.disk_usage('/')
        free_gb = disk_usage.free / (1024**3)
        status['disk'] = {
            'free_gb': round(free_gb, 2),
            'total_gb': round(disk_usage.total / (1024**3), 2),
            'percent_used': round((1 - disk_usage.free / disk_usage.total) * 100, 2)
        }
    except:
        pass
    
    return jsonify(status), 200


# ===================== VERSION =====================

@utils_bp.route('/version', methods=['GET'])
@swag_from('../descriptions/utils/version.yml')
def get_version():
    """
    Version de l'API
    """
    return jsonify({
        'version': '1.0.0',
        'api_version': 'v1',
        'build_date': datetime.utcnow().isoformat(),
        'environment': os.getenv('FLASK_ENV', 'development'),
        'python_version': platform.python_version(),
        'flask_version': '3.0.0',
        'features': {
            'rest_api': True,
            'websocket': True,
            'database': 'PostgreSQL + MongoDB',
            'authentication': 'JWT + Invite Codes'
        }
    }), 200


# ===================== VALIDATION TÉLÉPHONE =====================

def validate_phone_number(phone):
    """
    Valide un numéro de téléphone international
    Formats acceptés:
    - +229 XX XX XX XX (Bénin)
    - +33 X XX XX XX XX (France)
    - Autres formats internationaux
    """
    if not phone:
        return False, "Numéro de téléphone requis"
    
    # Nettoyer le numéro
    phone = phone.strip()
    
    # Vérifier le format international (+ suivi de chiffres)
    pattern = r'^\+\d{1,3}\d{6,14}$'
    if not re.match(pattern, phone):
        return False, "Format invalide. Utilisez le format international (+229XXXXXXXXX)"
    
    # Extraire l'indicatif
    country_code_match = re.match(r'^\+(\d{1,3})', phone)
    if not country_code_match:
        return False, "Indicatif pays invalide"
    
    country_code = country_code_match.group(1)
    
    # Vérifier la longueur selon l'indicatif
    phone_without_code = phone[len(country_code) + 1:]
    length = len(phone_without_code)
    
    country_info = {
        '229': {'name': 'Bénin', 'min': 8, 'max': 8},
        '33': {'name': 'France', 'min': 9, 'max': 9},
        '221': {'name': 'Sénégal', 'min': 9, 'max': 9},
        '225': {'name': 'Côte d\'Ivoire', 'min': 8, 'max': 8},
        '228': {'name': 'Togo', 'min': 8, 'max': 8},
        '226': {'name': 'Burkina Faso', 'min': 8, 'max': 8},
        '223': {'name': 'Mali', 'min': 8, 'max': 8},
        '227': {'name': 'Niger', 'min': 8, 'max': 8},
        '234': {'name': 'Nigéria', 'min': 10, 'max': 10},
        '1': {'name': 'USA/Canada', 'min': 10, 'max': 10},
        '44': {'name': 'Royaume-Uni', 'min': 10, 'max': 10},
        '49': {'name': 'Allemagne', 'min': 10, 'max': 11},
        '39': {'name': 'Italie', 'min': 10, 'max': 10},
        '34': {'name': 'Espagne', 'min': 9, 'max': 9}
    }
    
    if country_code in country_info:
        info = country_info[country_code]
        if length < info['min'] or length > info['max']:
            return False, f"{info['name']} : {info['min']} chiffres requis après l'indicatif"
    else:
        # Format générique pour les autres pays
        if length < 6 or length > 14:
            return False, f"Longueur invalide pour l'indicatif +{country_code}"
    
    return True, "Numéro valide"


@utils_bp.route('/validate/phone', methods=['POST'])
@swag_from('../descriptions/utils/validate_phone.yml')
def validate_phone():
    """
    Valide le format d'un numéro de téléphone
    """
    data = request.get_json()
    phone = data.get('phone') if data else None
    
    if not phone:
        return jsonify({
            'valid': False,
            'error': 'Numéro de téléphone requis'
        }), 400
    
    is_valid, message = validate_phone_number(phone)
    
    if is_valid:
        # Nettoyer le format (enlever les espaces, etc.)
        cleaned = re.sub(r'[\s\-\(\)]', '', phone)
        return jsonify({
            'valid': True,
            'message': message,
            'phone': phone,
            'phone_clean': cleaned,
            'country_code': re.match(r'^\+(\d{1,3})', phone).group(1) if re.match(r'^\+(\d{1,3})', phone) else None
        }), 200
    else:
        return jsonify({
            'valid': False,
            'error': message,
            'phone': phone
        }), 400


# ===================== WEBSOCKET INFO =====================

@utils_bp.route('/websocket/info', methods=['GET'])
@swag_from('../descriptions/utils/websocket_info.yml')
def websocket_info():
    """
    Informations de connexion WebSocket
    """
    # Récupérer l'URL de base
    scheme = request.scheme
    host = request.host
    
    # Pour WebSocket, remplacer http:// par ws:// et https:// par wss://
    ws_scheme = 'wss' if scheme == 'https' else 'ws'
    
    return jsonify({
        'url': f"{ws_scheme}://{host}/socket.io/",
        'protocols': ['websocket'],
        'transports': ['websocket', 'polling'],
        'options': {
            'reconnection': True,
            'reconnection_attempts': 5,
            'reconnection_delay': 1000,
            'reconnection_delay_max': 5000,
            'timeout': 20000,
            'auto_connect': True
        },
        'events': {
            'client_to_server': [
                'connect',
                'disconnect',
                'send_message',
                'edit_message',
                'delete_message',
                'typing',
                'mark_read',
                'react',
                'remove_reaction',
                'ping'
            ],
            'server_to_client': [
                'connected',
                'new_message',
                'message_edited',
                'message_deleted',
                'user_typing',
                'message_read',
                'reaction_added',
                'reaction_removed',
                'user_presence',
                'pong',
                'error'
            ]
        },
        'authentication': {
            'method': 'query_parameter',
            'parameter': 'token',
            'description': 'Le token JWT doit être passé en paramètre de la connexion WebSocket'
        },
        'example_connection': f"""
            // JavaScript
            const socket = io('{ws_scheme}://{host}', {{
                transports: ['websocket'],
                query: {{ token: 'YOUR_JWT_TOKEN' }}
            }});
            
            socket.on('connect', () => {{
                console.log('Connecté au serveur');
            }});
            
            socket.emit('send_message', {{
                conversation_id: 'uuid',
                type: 'text',
                content: 'Hello World!'
            }});
        """
    }), 200


# ===================== ROUTES SUPPLÉMENTAIRES UTILES =====================

@utils_bp.route('/ping', methods=['GET'])
def ping():
    """
    Ping simple pour tester la connectivité
    """
    return jsonify({
        'pong': True,
        'timestamp': datetime.utcnow().isoformat()
    }), 200


@utils_bp.route('/time', methods=['GET'])
def server_time():
    """
    Heure du serveur
    """
    return jsonify({
        'timestamp': datetime.utcnow().isoformat(),
        'timezone': 'UTC',
        'unix_timestamp': int(datetime.utcnow().timestamp())
    }), 200


@utils_bp.route('/echo', methods=['POST'])
def echo():
    """
    Echo endpoint pour tester les requêtes
    """
    data = request.get_json() if request.is_json else {}
    return jsonify({
        'echo': data,
        'method': request.method,
        'headers': dict(request.headers),
        'timestamp': datetime.utcnow().isoformat()
    }), 200