"""Tokens JWT et décorateurs d'accès.

Chaque token est lié à un appareil (`did`). Une requête n'est acceptée que si :
- le token est valide,
- le compte existe, n'est ni supprimé ni suspendu,
- l'appareil du token est TOUJOURS l'appareil actif du compte.
Activer un nouvel appareil invalide donc immédiatement les tokens de l'ancien.
"""
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import g, request

from core.config import Config
from core.database import execute_query
from core.http import error_response

SESSION_ACTIVE = 'active'
SESSION_SUSPENDED = 'suspended'
SESSION_ACCOUNT_MISSING = 'account_missing'
SESSION_DEVICE_REVOKED = 'device_revoked'


def generate_token(user_id, device_id):
    now = datetime.now(timezone.utc)
    payload = {
        'user_id': str(user_id),
        'did': str(device_id),
        'exp': now + timedelta(days=Config.JWT_EXPIRATION_DAYS),
        'iat': now,
    }
    return jwt.encode(payload, Config.JWT_SECRET, algorithm='HS256')


def decode_token(token):
    """Retourne {'user_id', 'device_id'} si la signature est valide, sinon None."""
    if not token:
        return None
    if token.startswith('Bearer '):
        token = token[7:]
    try:
        payload = jwt.decode(token, Config.JWT_SECRET, algorithms=['HS256'])
    except jwt.InvalidTokenError:
        return None
    if not payload.get('user_id'):
        return None
    return {'user_id': payload['user_id'], 'device_id': payload.get('did')}


def verify_token(token):
    """Raccourci : user_id du token (signature seulement), ou None."""
    claims = decode_token(token)
    return claims['user_id'] if claims else None


def get_session_state(user_id, device_id):
    """État de la session (compte + appareil) en une seule requête."""
    row = execute_query("""
        SELECT u.is_suspended, u.deleted_at,
               EXISTS (
                   SELECT 1 FROM gbekoun.user_devices d
                   WHERE d.id = %s AND d.user_id = u.id AND d.revoked_at IS NULL
               ) AS device_active
        FROM gbekoun.users u
        WHERE u.id = %s
    """, (device_id, user_id), fetch_one=True)

    if not row or row.get('deleted_at'):
        return SESSION_ACCOUNT_MISSING
    if row.get('is_suspended'):
        return SESSION_SUSPENDED
    if not row.get('device_active'):
        return SESSION_DEVICE_REVOKED
    return SESSION_ACTIVE


_SESSION_ERRORS = {
    SESSION_ACCOUNT_MISSING: ('Compte introuvable ou supprimé', 401, 'ACCOUNT_NOT_FOUND'),
    SESSION_SUSPENDED: ('Compte suspendu', 403, 'ACCOUNT_SUSPENDED'),
    SESSION_DEVICE_REVOKED: (
        'Cet appareil n\'est plus autorisé. Activez-le à nouveau.', 401, 'DEVICE_REVOKED'
    ),
}


def require_auth(f):
    """Exige un token valide, un compte actif et l'appareil actif du compte."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return error_response('Token manquant', 401, 'TOKEN_MISSING')

        claims = decode_token(token)
        if not claims:
            return error_response('Token invalide ou expiré', 401, 'TOKEN_INVALID')

        state = get_session_state(claims['user_id'], claims['device_id'])
        if state != SESSION_ACTIVE:
            message, status, code = _SESSION_ERRORS[state]
            return error_response(message, status, code)

        g.current_user_id = claims['user_id']
        g.current_device_id = claims['device_id']
        return f(*args, **kwargs)
    return decorated


def is_platform_admin(user_id):
    user = execute_query(
        "SELECT is_admin FROM gbekoun.users WHERE id = %s",
        (user_id,), fetch_one=True
    )
    return bool(user and user.get('is_admin') is True)


def require_admin(f):
    """À placer après @require_auth : exige un administrateur de la plateforme."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_platform_admin(g.current_user_id):
            return error_response(
                'Accès non autorisé. Droits administrateur requis.', 403, 'ADMIN_REQUIRED'
            )
        return f(*args, **kwargs)
    return decorated
