# blueprints/devices.py
"""Activation d'un nouvel appareil (un seul appareil actif par compte).

Deux chemins :
1. Validation par l'ancien appareil
   nouveau  : POST /api/auth/device/requests               → request_id
   ancien   : reçoit `device_approval_requested` (socket + push)
   ancien   : POST /api/auth/device/requests/<id>/approve   (ou /reject)
   nouveau  : POST /api/auth/device/requests/<id>/status    → token quand approuvé
2. Code donné par un administrateur (téléphone perdu, ancien appareil inaccessible)
   admin    : POST /api/admin/users/<user_id>/activation-code
   nouveau  : POST /api/auth/device/activate               → token
"""
import uuid

from flask import Blueprint, g, jsonify, request
from flasgger import swag_from

from core.database import execute_query, transaction
from core.extensions import socketio
from core.http import ApiError, error_response
from core.notifications import notify_device_approval_requested
from core.realtime import user_room
from core.security import generate_token, require_auth
from services.devices import (
    ACTIVATION_CODE_MAX_ATTEMPTS,
    APPROVAL_REQUEST_TTL,
    APPROVAL_REQUESTS_PER_HOUR,
    bind_device,
    disconnect_devices,
    hash_secret,
    lock_user_devices,
    normalize_activation_code,
    parse_device_payload,
    secrets_match,
)
from utils.message_helpers import to_utc_isoformat

devices_bp = Blueprint('devices', __name__, url_prefix='/api/auth/device')


def _iso(value):
    return to_utc_isoformat(value) if value else None


def _find_user_by_phone(phone_number):
    """Utilisateur actif pour ce numéro, sinon ApiError."""
    if not phone_number:
        raise ApiError('PHONE_REQUIRED', 'Téléphone requis')
    user = execute_query("""
        SELECT id, display_name, username, is_suspended
        FROM gbekoun.users
        WHERE phone_number = %s AND deleted_at IS NULL
    """, (phone_number,), fetch_one=True)
    if not user:
        raise ApiError('USER_NOT_FOUND', 'Utilisateur non trouvé', 404)
    if user.get('is_suspended'):
        raise ApiError('ACCOUNT_SUSPENDED', 'Compte suspendu', 403)
    return user


def _session_payload(user, device_id):
    return {
        'token': generate_token(user['id'], device_id),
        'user_id': str(user['id']),
        'device_id': device_id,
        'display_name': user['display_name'],
        'username': user['username'],
    }


# ===================== APPAREIL COURANT =====================

@devices_bp.route('', methods=['GET'])
@require_auth
@swag_from('../descriptions/devices/get_current.yml')
def get_current_device():
    device = execute_query("""
        SELECT id, device_name, platform, created_at, last_seen_at
        FROM gbekoun.user_devices
        WHERE id = %s AND user_id = %s
    """, (g.current_device_id, g.current_user_id), fetch_one=True)
    return jsonify({
        'device_id': str(device['id']),
        'device_name': device['device_name'],
        'platform': device['platform'],
        'activated_at': _iso(device['created_at']),
        'last_seen_at': _iso(device['last_seen_at']),
    }), 200


# ===================== ACTIVATION PAR CODE ADMIN =====================

@devices_bp.route('/activate', methods=['POST'])
@swag_from('../descriptions/devices/activate.yml')
def activate_with_code():
    data = request.get_json(silent=True) or {}
    device = parse_device_payload(data)
    code = normalize_activation_code(data.get('activation_code'))
    if not code:
        raise ApiError('ACTIVATION_CODE_REQUIRED', 'Code d\'activation requis')

    user = _find_user_by_phone(data.get('phone_number'))
    user_id = str(user['id'])

    # Le compteur d'essais doit être enregistré même en cas d'échec : on ne
    # lève donc pas d'exception dans la transaction (elle serait annulée).
    outcome, attempts_left, device_id, revoked_ids = None, 0, None, []
    with transaction() as cur:
        lock_user_devices(cur, user_id)
        cur.execute("""
            SELECT id, code_hash, attempts
            FROM gbekoun.device_activation_codes
            WHERE user_id = %s AND used_at IS NULL AND expires_at > NOW() AND attempts < %s
            ORDER BY created_at DESC
            LIMIT 1
            FOR UPDATE
        """, (user_id, ACTIVATION_CODE_MAX_ATTEMPTS))
        stored = cur.fetchone()

        if not stored:
            outcome = 'no_code'
        elif not secrets_match(code, stored['code_hash']):
            cur.execute("""
                UPDATE gbekoun.device_activation_codes
                SET attempts = attempts + 1 WHERE id = %s
                RETURNING attempts
            """, (stored['id'],))
            attempts_left = max(0, ACTIVATION_CODE_MAX_ATTEMPTS - cur.fetchone()['attempts'])
            outcome = 'wrong_code'
        else:
            cur.execute("""
                UPDATE gbekoun.device_activation_codes SET used_at = NOW() WHERE id = %s
            """, (stored['id'],))
            device_id, revoked_ids = bind_device(cur, user_id, device, 'replaced_by_admin_code')
            outcome = 'ok'

    if outcome == 'no_code':
        return error_response(
            'Aucun code d\'activation valide. Demandez un nouveau code à l\'administrateur.',
            403, 'INVALID_ACTIVATION_CODE', attempts_left=0,
        )
    if outcome == 'wrong_code':
        return error_response(
            'Code d\'activation incorrect', 403, 'INVALID_ACTIVATION_CODE',
            attempts_left=attempts_left,
        )

    disconnect_devices(revoked_ids)
    return jsonify(_session_payload(user, device_id)), 200


# ===================== VALIDATION PAR L'ANCIEN APPAREIL =====================

@devices_bp.route('/requests', methods=['POST'])
@swag_from('../descriptions/devices/create_request.yml')
def create_approval_request():
    """Le nouvel appareil demande l'accord de l'appareil actif."""
    data = request.get_json(silent=True) or {}
    device = parse_device_payload(data)
    user = _find_user_by_phone(data.get('phone_number'))
    user_id = str(user['id'])
    request_id = str(uuid.uuid4())

    with transaction() as cur:
        lock_user_devices(cur, user_id)

        cur.execute("""
            SELECT secret_hash FROM gbekoun.user_devices
            WHERE user_id = %s AND revoked_at IS NULL
        """, (user_id,))
        active = cur.fetchone()
        if not active:
            raise ApiError(
                'NO_ACTIVE_DEVICE',
                'Aucun appareil actif pour valider. Demandez un code à l\'administrateur.', 409,
            )
        if active['secret_hash'] == device['secret_hash']:
            raise ApiError('DEVICE_ALREADY_ACTIVE', 'Cet appareil est déjà actif : connectez-vous.', 409)

        # Limite anti-harcèlement : on ne peut pas inonder l'ancien téléphone de demandes.
        cur.execute("""
            SELECT COUNT(*) AS n FROM gbekoun.device_approval_requests
            WHERE user_id = %s AND created_at > NOW() - INTERVAL '1 hour'
        """, (user_id,))
        if cur.fetchone()['n'] >= APPROVAL_REQUESTS_PER_HOUR:
            raise ApiError('TOO_MANY_REQUESTS', 'Trop de demandes. Réessayez plus tard.', 429)

        # Une seule demande en attente à la fois : la nouvelle remplace l'ancienne.
        cur.execute("""
            UPDATE gbekoun.device_approval_requests
            SET status = 'expired', decided_at = NOW()
            WHERE user_id = %s AND status = 'pending'
        """, (user_id,))

        cur.execute("""
            INSERT INTO gbekoun.device_approval_requests
                (id, user_id, secret_hash, device_name, platform, expires_at)
            VALUES (%s, %s, %s, %s, %s, NOW() + %s)
            RETURNING created_at, expires_at
        """, (request_id, user_id, device['secret_hash'], device['device_name'],
              device['platform'], APPROVAL_REQUEST_TTL))
        created = cur.fetchone()

    payload = {
        'request_id': request_id,
        'device_name': device['device_name'],
        'platform': device['platform'],
        'requested_at': _iso(created['created_at']),
        'expires_at': _iso(created['expires_at']),
    }
    socketio.emit('device_approval_requested', payload, room=user_room(user_id))
    notify_device_approval_requested(user_id, payload)

    return jsonify({
        'request_id': request_id,
        'status': 'pending',
        'expires_at': payload['expires_at'],
    }), 201


@devices_bp.route('/requests/<request_id>/status', methods=['POST'])
@swag_from('../descriptions/devices/request_status.yml')
def approval_request_status(request_id):
    """Le nouvel appareil interroge l'état de sa demande (toutes les 2-3 s)."""
    data = request.get_json(silent=True) or {}
    secret = data.get('device_secret')
    if not isinstance(secret, str):
        raise ApiError('INVALID_DEVICE_SECRET', 'device_secret requis')

    try:
        uuid.UUID(request_id)
    except ValueError:
        raise ApiError('REQUEST_NOT_FOUND', 'Demande introuvable', 404)

    row = execute_query("""
        SELECT r.id, r.user_id, r.secret_hash, r.approved_device_id,
               CASE WHEN r.status = 'pending' AND r.expires_at <= NOW() THEN 'expired'
                    ELSE r.status END AS status,
               u.display_name, u.username
        FROM gbekoun.device_approval_requests r
        JOIN gbekoun.users u ON u.id = r.user_id
        WHERE r.id = %s
    """, (request_id,), fetch_one=True)

    # Seul l'appareil qui a fait la demande (même secret) peut la consulter.
    if not row or not secrets_match(secret, row['secret_hash']):
        raise ApiError('REQUEST_NOT_FOUND', 'Demande introuvable', 404)

    if row['status'] != 'approved':
        return jsonify({'request_id': request_id, 'status': row['status']}), 200

    device = execute_query("""
        SELECT id FROM gbekoun.user_devices
        WHERE id = %s AND secret_hash = %s AND revoked_at IS NULL
    """, (row['approved_device_id'], hash_secret(secret)), fetch_one=True)
    if not device:
        # Approuvé puis remplacé entre-temps par un autre appareil.
        return jsonify({'request_id': request_id, 'status': 'expired'}), 200

    user = {'id': row['user_id'], 'display_name': row['display_name'], 'username': row['username']}
    return jsonify({'request_id': request_id, 'status': 'approved',
                    **_session_payload(user, str(device['id']))}), 200


@devices_bp.route('/requests/pending', methods=['GET'])
@require_auth
@swag_from('../descriptions/devices/pending_requests.yml')
def pending_approval_requests():
    """L'appareil actif récupère les demandes en attente (à l'ouverture de l'appli)."""
    rows = execute_query("""
        SELECT id, device_name, platform, created_at, expires_at
        FROM gbekoun.device_approval_requests
        WHERE user_id = %s AND status = 'pending' AND expires_at > NOW()
        ORDER BY created_at DESC
    """, (g.current_user_id,), fetch_all=True)
    return jsonify([{
        'request_id': str(r['id']),
        'device_name': r['device_name'],
        'platform': r['platform'],
        'requested_at': _iso(r['created_at']),
        'expires_at': _iso(r['expires_at']),
    } for r in rows]), 200


def _decide(request_id, approve):
    try:
        uuid.UUID(request_id)
    except ValueError:
        raise ApiError('REQUEST_NOT_FOUND', 'Demande introuvable', 404)

    revoked_ids = []
    with transaction() as cur:
        lock_user_devices(cur, g.current_user_id)
        cur.execute("""
            SELECT id, secret_hash, device_name, platform, status, expires_at <= NOW() AS expired
            FROM gbekoun.device_approval_requests
            WHERE id = %s AND user_id = %s
            FOR UPDATE
        """, (request_id, g.current_user_id))
        pending = cur.fetchone()

        if not pending:
            raise ApiError('REQUEST_NOT_FOUND', 'Demande introuvable', 404)
        if pending['status'] != 'pending' or pending['expired']:
            raise ApiError('REQUEST_NOT_PENDING', 'Cette demande n\'est plus en attente', 409)

        if approve:
            device_id, revoked_ids = bind_device(cur, g.current_user_id, pending, 'replaced_by_approval')
            cur.execute("""
                UPDATE gbekoun.device_approval_requests
                SET status = 'approved', approved_device_id = %s, decided_at = NOW()
                WHERE id = %s
            """, (device_id, request_id))
        else:
            cur.execute("""
                UPDATE gbekoun.device_approval_requests
                SET status = 'rejected', decided_at = NOW()
                WHERE id = %s
            """, (request_id,))

    # L'appareil qui vient d'approuver est lui-même révoqué : ses sockets sont fermés.
    disconnect_devices(revoked_ids)
    return jsonify({'request_id': request_id, 'status': 'approved' if approve else 'rejected'}), 200


@devices_bp.route('/requests/<request_id>/approve', methods=['POST'])
@require_auth
@swag_from('../descriptions/devices/approve_request.yml')
def approve_request(request_id):
    return _decide(request_id, approve=True)


@devices_bp.route('/requests/<request_id>/reject', methods=['POST'])
@require_auth
@swag_from('../descriptions/devices/reject_request.yml')
def reject_request(request_id):
    return _decide(request_id, approve=False)
