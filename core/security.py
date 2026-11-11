"""Tokens JWT et décorateurs d'accès.

Chaque token est lié à un appareil (`did`). Une requête n'est acceptée que si :
- le token est valide,
- le compte existe, n'est ni supprimé ni suspendu,
- l'appareil du token est TOUJOURS l'appareil actif du compte.
Activer un nouvel appareil invalide donc immédiatement les tokens de l'ancien.

Session web (PC d'un admin) : en plus, le compte doit être admin, la session
expire au bout d'1 h (côté serveur, quel que soit le token) et seules les
routes d'administration sont accessibles (WEB_ADMIN_ONLY sinon).
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
SESSION_EXPIRED = 'session_expired'     # PC : 1 h écoulée sans prolongation
SESSION_IDLE = 'session_idle'           # PC : aucune requête depuis 5 min

KIND_MOBILE = 'mobile'
KIND_WEB = 'web'
WEB_TOKEN_TTL = timedelta(hours=1)
WEB_IDLE_TIMEOUT = timedelta(minutes=5)

# Routes accessibles depuis le PC d'un admin (tout le reste → 403 WEB_ADMIN_ONLY).
# /api/media/ : l'interface admin affiche les photos de profil.
_WEB_ALLOWED_PREFIXES = ('/api/admin/', '/api/auth/invite-codes', '/api/media/')
_WEB_ALLOWED_ROUTES = {
    ('GET', '/api/auth/verify'),
    ('POST', '/api/auth/logout'),
    ('GET', '/api/auth/device'),
    ('POST', '/api/auth/device/web/revoke'),
    ('POST', '/api/auth/device/web/extend'),
    ('GET', '/api/users/me'),
}


def generate_token(user_id, device_id, kind=KIND_MOBILE):
    now = datetime.now(timezone.utc)
    lifetime = WEB_TOKEN_TTL if kind == KIND_WEB else timedelta(days=Config.JWT_EXPIRATION_DAYS)
    payload = {
        'user_id': str(user_id),
        'did': str(device_id),
        'kind': kind,
        'exp': now + lifetime,
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
    """État de la session (compte + appareil) en une seule requête.

    Retourne (état, type_d_appareil). Le type vient de la base, pas du token.
    """
    row = execute_query("""
        SELECT u.is_suspended, u.deleted_at, u.is_admin,
               d.kind AS device_kind,
               d.revoked_at IS NOT NULL AS device_revoked,
               COALESCE(d.expires_at <= NOW(), FALSE) AS session_expired,
               (d.kind = 'web' AND COALESCE(d.last_seen_at < NOW() - %s, TRUE)) AS session_idle
        FROM gbekoun.users u
        LEFT JOIN gbekoun.user_devices d ON d.id = %s AND d.user_id = u.id
        WHERE u.id = %s
    """, (WEB_IDLE_TIMEOUT, device_id, user_id), fetch_one=True)

    if not row or row.get('deleted_at'):
        return SESSION_ACCOUNT_MISSING, None
    if row.get('is_suspended'):
        return SESSION_SUSPENDED, None
    kind = row.get('device_kind')
    if not kind or row.get('device_revoked'):
        return SESSION_DEVICE_REVOKED, None
    if kind == KIND_WEB:
        if not row.get('is_admin'):
            # Plus admin : l'accès PC tombe automatiquement.
            return SESSION_DEVICE_REVOKED, None
        if row.get('session_expired'):
            return SESSION_EXPIRED, None
        if row.get('session_idle'):
            return SESSION_IDLE, None
    return SESSION_ACTIVE, kind


def touch_web_session(device_id):
    """Chaque requête du PC repousse le délai d'inactivité de 5 min."""
    execute_query(
        "UPDATE gbekoun.user_devices SET last_seen_at = NOW() WHERE id = %s",
        (device_id,)
    )


def _web_route_allowed():
    path = request.path.rstrip('/') or '/'
    if path.startswith(_WEB_ALLOWED_PREFIXES):
        return True
    return (request.method, path) in _WEB_ALLOWED_ROUTES


_SESSION_ERRORS = {
    SESSION_ACCOUNT_MISSING: ('Compte introuvable ou supprimé', 401, 'ACCOUNT_NOT_FOUND'),
    SESSION_SUSPENDED: ('Compte suspendu', 403, 'ACCOUNT_SUSPENDED'),
    SESSION_DEVICE_REVOKED: (
        'Cet appareil n\'est plus autorisé (remplacé ou révoqué).', 401, 'DEVICE_REVOKED'
    ),
    SESSION_EXPIRED: (
        'Session d\'administration expirée (1 h). Validez une nouvelle connexion sur votre téléphone.',
        401, 'SESSION_EXPIRED'
    ),
    SESSION_IDLE: (
        'Session d\'administration fermée après 5 minutes d\'inactivité.', 401, 'SESSION_IDLE'
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

        state, kind = get_session_state(claims['user_id'], claims['device_id'])
        if state != SESSION_ACTIVE:
            message, status, code = _SESSION_ERRORS[state]
            return error_response(message, status, code)

        if kind == KIND_WEB and not _web_route_allowed():
            return error_response(
                'Depuis un PC, seule l\'administration est accessible.', 403, 'WEB_ADMIN_ONLY'
            )

        if kind == KIND_WEB:
            touch_web_session(claims['device_id'])

        g.current_user_id = claims['user_id']
        g.current_device_id = claims['device_id']
        g.current_device_kind = kind
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
