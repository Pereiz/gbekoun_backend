"""Liaison compte ↔ appareil (un seul appareil actif par compte).

Principe :
- l'application génère une clé secrète aléatoire (`device_secret`) au premier
  lancement et la garde dans le stockage sécurisé du téléphone ;
- le serveur n'en garde que l'empreinte SHA-256 (la clé est longue et
  aléatoire : un hachage lent type bcrypt n'apporte rien ici) ;
- se connecter exige le numéro ET cette clé ;
- un nouvel appareil s'active soit avec l'accord de l'ancien, soit avec un
  code donné par un administrateur. L'ancien appareil est alors révoqué.

Deux types d'appareil (`kind`) :
- `mobile` : le téléphone, un seul par compte, accès complet ;
- `web` : le PC d'un ADMINISTRATEUR, un seul par admin, limité aux routes
  d'administration, session de 1 h maximum. Activer le PC ne touche pas au
  téléphone, et c'est le téléphone qui valide chaque connexion du PC.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta

from core.http import ApiError
from core.realtime import disconnect_device

KIND_MOBILE = 'mobile'
KIND_WEB = 'web'
MOBILE_PLATFORMS = ('android', 'ios')
WEB_SESSION_TTL = timedelta(hours=1)
DEVICE_SECRET_MIN_LENGTH = 32
DEVICE_SECRET_MAX_LENGTH = 256
DEVICE_NAME_MAX_LENGTH = 100

# Codes d'activation remis par un administrateur
ACTIVATION_CODE_LENGTH = 8
ACTIVATION_CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'  # sans 0/O, 1/I/L
ACTIVATION_CODE_TTL = timedelta(minutes=30)
BOOTSTRAP_CODE_TTL = timedelta(hours=24)   # premier admin (scripts/create_admin.py)
ACTIVATION_CODE_MAX_ATTEMPTS = 5

# Demandes de validation par l'ancien appareil
APPROVAL_REQUEST_TTL = timedelta(minutes=10)
APPROVAL_REQUESTS_PER_HOUR = 5


def hash_secret(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def secrets_match(value, expected_hash):
    return hmac.compare_digest(hash_secret(value), expected_hash or '')


def parse_device_payload(data, kind=KIND_MOBILE):
    """Valide les champs appareil envoyés par l'application (ou l'interface web admin).

    Retourne {'secret_hash', 'device_name', 'platform'} ou lève ApiError.
    """
    secret = data.get('device_secret')
    if (
        not isinstance(secret, str)
        or not DEVICE_SECRET_MIN_LENGTH <= len(secret) <= DEVICE_SECRET_MAX_LENGTH
    ):
        raise ApiError(
            'INVALID_DEVICE_SECRET',
            f'device_secret requis ({DEVICE_SECRET_MIN_LENGTH} à '
            f'{DEVICE_SECRET_MAX_LENGTH} caractères aléatoires)',
        )

    name = data.get('device_name')
    if name is not None and (not isinstance(name, str) or len(name) > DEVICE_NAME_MAX_LENGTH):
        raise ApiError('INVALID_DEVICE_NAME', f'device_name : {DEVICE_NAME_MAX_LENGTH} caractères maximum')

    if kind == KIND_WEB:
        platform = 'web'
    else:
        platform = data.get('platform', 'android')
        if platform not in MOBILE_PLATFORMS:
            raise ApiError('INVALID_PLATFORM', 'platform doit valoir android ou ios')

    return {
        'secret_hash': hash_secret(secret),
        'device_name': name.strip() if isinstance(name, str) and name.strip() else None,
        'platform': platform,
        'kind': kind,
    }


def normalize_activation_code(code):
    if not isinstance(code, str):
        return ''
    return ''.join(ch for ch in code.upper() if ch.isalnum())


def lock_user_devices(cur, user_id):
    """Sérialise les changements d'appareil d'un même compte (activation, approbation)."""
    cur.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f'devices:{user_id}',)
    )


def bind_device(cur, user_id, device, reason):
    """Enregistre `device` comme seul appareil actif de son type (mobile ou web).

    À appeler dans une transaction, après lock_user_devices.
    Un PC (web) ne révoque que l'ancien PC ; un téléphone ne révoque que l'ancien téléphone.
    Retourne (nouvel_id, [ids_révoqués]).
    """
    kind = device.get('kind', KIND_MOBILE)
    cur.execute("""
        UPDATE gbekoun.user_devices
        SET revoked_at = NOW(), revoked_reason = %s
        WHERE user_id = %s AND kind = %s AND revoked_at IS NULL
        RETURNING id
    """, (reason, user_id, kind))
    revoked_ids = [str(row['id']) for row in cur.fetchall()]

    cur.execute("""
        INSERT INTO gbekoun.user_devices
            (user_id, secret_hash, device_name, platform, kind, last_seen_at, expires_at)
        VALUES (%s, %s, %s, %s, %s, NOW(), %s)
        RETURNING id
    """, (user_id, device['secret_hash'], device['device_name'], device['platform'], kind,
          None if kind == KIND_MOBILE else _web_expiry(cur)))
    device_id = str(cur.fetchone()['id'])

    if revoked_ids and kind == KIND_MOBILE:
        # L'ancien téléphone ne doit plus recevoir les notifications :
        # le nouvel appareil réenregistrera son jeton Expo après connexion.
        cur.execute("""
            UPDATE gbekoun.push_devices SET is_active = FALSE, updated_at = NOW()
            WHERE user_id = %s AND is_active = TRUE
        """, (user_id,))

    return device_id, revoked_ids


def _web_expiry(cur):
    cur.execute("SELECT NOW() + %s AS expires_at", (WEB_SESSION_TTL,))
    return cur.fetchone()['expires_at']


def revoke_active_device(cur, user_id, reason, kind=None):
    """Révoque sans remplacer (perte, vol, déconnexion du PC). kind=None : tous les types.

    Retourne les ids révoqués.
    """
    cur.execute("""
        UPDATE gbekoun.user_devices
        SET revoked_at = NOW(), revoked_reason = %s
        WHERE user_id = %s AND revoked_at IS NULL AND (%s::text IS NULL OR kind = %s)
        RETURNING id
    """, (reason, user_id, kind, kind))
    revoked_ids = [str(row['id']) for row in cur.fetchall()]
    if kind in (None, KIND_MOBILE):
        cur.execute("""
            UPDATE gbekoun.push_devices SET is_active = FALSE, updated_at = NOW()
            WHERE user_id = %s AND is_active = TRUE
        """, (user_id,))
    return revoked_ids


def disconnect_devices(device_ids):
    """À appeler APRÈS le commit : ferme les sockets des appareils révoqués."""
    for device_id in device_ids:
        disconnect_device(device_id)


def create_activation_code(cur, user_id, created_by, ttl=ACTIVATION_CODE_TTL):
    """Crée un code d'activation à usage unique ; invalide les codes précédents.

    Retourne (code_en_clair, expires_at). Le code n'est stocké que sous forme d'empreinte.
    """
    cur.execute("""
        UPDATE gbekoun.device_activation_codes
        SET expires_at = NOW()
        WHERE user_id = %s AND used_at IS NULL AND expires_at > NOW()
    """, (user_id,))

    code = ''.join(secrets.choice(ACTIVATION_CODE_ALPHABET) for _ in range(ACTIVATION_CODE_LENGTH))
    cur.execute("""
        INSERT INTO gbekoun.device_activation_codes (user_id, code_hash, created_by, expires_at)
        VALUES (%s, %s, %s, NOW() + %s)
        RETURNING expires_at
    """, (user_id, hash_secret(code), created_by, ttl))
    return code, cur.fetchone()['expires_at']


def format_activation_code(code):
    """ABCD2345 → ABCD-2345 (plus facile à dicter)."""
    half = len(code) // 2
    return f'{code[:half]}-{code[half:]}'
