"""Point d'entrée de l'API GBEKOUN (Flask + Socket.IO).

Démarrage : `python app.py` en local, ou `gunicorn ... app:app` en production.
"""
import logging
import threading
import time
from http import HTTPStatus

from flask import Flask, jsonify
from flask_cors import CORS
from flasgger import Swagger

from core.config import Config, cors_origins, validate_config
from core.extensions import socketio
from core.http import ApiError

logging.basicConfig(level=logging.INFO)
validate_config()

# -------------------------------
# Application Flask & Socket.IO
# -------------------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = Config.SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = Config.MAX_CONTENT_LENGTH
# Avant : "*" (n'importe quel site pouvait appeler l'API depuis un navigateur).
CORS(app, resources={r"/*": {"origins": cors_origins()}})
socketio.init_app(app, cors_allowed_origins=cors_origins(), async_mode='threading')

if Config.TRUST_PROXY:
    # Un seul proxy de confiance (Nginx) : vraie IP du client, https, nom de domaine.
    # Appliqué après Socket.IO pour que /socket.io/ en profite aussi.
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Enregistre les événements Socket.IO (un seul endroit : sockets/events.py).
import sockets.events  # noqa: E402,F401

# -------------------------------
# Documentation Swagger (/apidocs/)
# -------------------------------
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
    'security': [{'BearerAuth': []}],
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
# Routes (blueprints)
# -------------------------------
from blueprints.admin import admin_bp  # noqa: E402
from blueprints.auth import auth_bp  # noqa: E402
from blueprints.conversations import conversations_bp  # noqa: E402
from blueprints.devices import devices_bp  # noqa: E402
from blueprints.media import media_bp  # noqa: E402
from blueprints.messages import messages_bp  # noqa: E402
from blueprints.status import cleanup_inactive_users, status_bp  # noqa: E402
from blueprints.users import users_bp  # noqa: E402
from blueprints.utils import utils_bp  # noqa: E402

app.register_blueprint(auth_bp, url_prefix='/api/auth')
app.register_blueprint(devices_bp)
app.register_blueprint(conversations_bp, url_prefix='/api/conversations')
app.register_blueprint(messages_bp, url_prefix='/api/messages')
app.register_blueprint(admin_bp)
app.register_blueprint(users_bp)
app.register_blueprint(status_bp)
app.register_blueprint(utils_bp)
app.register_blueprint(media_bp)


@app.errorhandler(ApiError)
def handle_api_error(error):
    """Les erreurs métier (ApiError) deviennent {"error", "code", ...}."""
    return error.to_response()


@app.errorhandler(404)
def handle_not_found(_error):
    return jsonify({'error': 'Ressource introuvable', 'code': 'NOT_FOUND'}), 404


@app.errorhandler(500)
def handle_internal_error(_error):
    """Jamais de détail technique dans la réponse : il est dans les logs."""
    return jsonify({'error': 'Erreur interne du serveur', 'code': 'INTERNAL_ERROR'}), 500


@app.route("/", methods=['GET'])
def welcome():
    return jsonify({"Message": "Bonjour, vous etes sur GBEKOUN !"}), HTTPStatus.OK


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'}), 200


# -------------------------------
# Tâche de fond : passer hors ligne les utilisateurs sans heartbeat récent
# -------------------------------
logger = logging.getLogger(__name__)


def _cleanup_loop():
    while True:
        time.sleep(60)
        try:
            cleanup_inactive_users()
        except Exception:
            logger.exception('Échec du nettoyage des utilisateurs inactifs')


threading.Thread(target=_cleanup_loop, daemon=True, name='cleanup-inactive-users').start()


if __name__ == '__main__':
    print(f"Serveur démarré sur http://0.0.0.0:{Config.PORT}")
    socketio.run(
        app, host='0.0.0.0', port=Config.PORT, debug=Config.DEBUG,
        allow_unsafe_werkzeug=True,
    )
