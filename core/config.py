"""Configuration lue depuis les variables d'environnement (fichier .env en local)."""
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _env_bool(name, default='0'):
    return os.getenv(name, default).strip().lower() in ('1', 'true', 'yes', 'on')


class Config:
    DEBUG = _env_bool('FLASK_DEBUG')

    SECRET_KEY = os.getenv('SECRET_KEY')
    JWT_SECRET = os.getenv('JWT_SECRET')
    JWT_EXPIRATION_DAYS = int(os.getenv('JWT_EXPIRATION_DAYS', '30'))

    PG_HOST = os.getenv('PG_HOST', 'localhost')
    PG_PORT = os.getenv('PG_PORT', '5432')
    PG_DATABASE = os.getenv('PG_DATABASE', 'gbekoundb')
    PG_USER = os.getenv('PG_USER', 'gbekoundb_user')
    PG_PASSWORD = os.getenv('PG_PASSWORD')
    PG_POOL_MAX = int(os.getenv('PG_POOL_MAX', '10'))

    MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/gbekoundb_messages')
    MONGO_DB = os.getenv('MONGO_DB', 'gbekoundb_messages')

    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    BACKUP_DIR = os.getenv('BACKUP_DIR', 'backups')
    PORT = int(os.getenv('PORT', '8880'))


# Variables sans lesquelles l'application refuse de démarrer en production.
_REQUIRED_IN_PRODUCTION = ('SECRET_KEY', 'JWT_SECRET', 'PG_PASSWORD')

# Valeurs de secours utilisées uniquement quand FLASK_DEBUG=1.
_DEV_FALLBACKS = {
    'SECRET_KEY': 'dev-only-secret-key',
    'JWT_SECRET': 'dev-only-jwt-secret',
}


def validate_config():
    """Bloque le démarrage en production si un secret manque.

    Avant, des secrets par défaut connus (ex. 'jwt-secret-key') étaient utilisés
    silencieusement : n'importe qui pouvait alors fabriquer un token valide.
    """
    missing = [name for name in _REQUIRED_IN_PRODUCTION if not getattr(Config, name)]
    if not missing:
        return

    if not Config.DEBUG:
        raise RuntimeError(
            'Variables d\'environnement manquantes : ' + ', '.join(missing)
            + '. Définissez-les (ou FLASK_DEBUG=1 pour le développement local).'
        )

    for name in missing:
        if name in _DEV_FALLBACKS:
            setattr(Config, name, _DEV_FALLBACKS[name])
            logger.warning('%s absent : valeur de développement utilisée (FLASK_DEBUG=1).', name)
