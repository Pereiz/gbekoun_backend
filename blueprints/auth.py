# blueprints/auth.py
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, jsonify, request
from flasgger import swag_from
from psycopg2 import errors as pg_errors

from core.database import execute_query, transaction
from core.http import ApiError, error_response
from core.security import generate_token, require_admin, require_auth
from services.devices import bind_device, lock_user_devices, parse_device_payload

auth_bp = Blueprint('auth', __name__)

MAX_CODES_PER_REQUEST = 100


# ===================== ROUTES PUBLIQUES =====================

@auth_bp.route('/register', methods=['POST'])
@swag_from('../descriptions/auth/register.yml')
def register():
    """Inscription avec code d'invitation. L'appareil utilisé devient l'appareil du compte."""
    data = request.get_json(silent=True) or {}
    phone_number = data.get('phone_number')
    invite_code = data.get('invite_code')
    display_name = data.get('display_name') or phone_number
    username = data.get('username') or phone_number

    if not phone_number or not invite_code:
        return error_response('Téléphone et code d\'invitation requis', 400, 'MISSING_FIELDS')
    device = parse_device_payload(data)

    user_id = str(uuid.uuid4())
    try:
        # Compte + consommation du code + appareil dans UNE transaction.
        # Le code est « réservé » par l'UPDATE conditionnel : deux inscriptions
        # simultanées ne peuvent pas utiliser le même code.
        with transaction() as cur:
            cur.execute("""
                INSERT INTO gbekoun.users (id, phone_number, display_name, username)
                VALUES (%s, %s, %s, %s)
            """, (user_id, phone_number, display_name, username))

            cur.execute("""
                UPDATE gbekoun.invite_codes
                SET used_by = %s, used_at = NOW()
                WHERE code = %s AND used_by IS NULL AND expires_at > NOW()
                RETURNING id
            """, (user_id, invite_code))
            if cur.fetchone() is None:
                raise ApiError('INVALID_INVITE_CODE', 'Code invalide ou expiré', 403)

            lock_user_devices(cur, user_id)
            device_id, _ = bind_device(cur, user_id, device, 'registration')
    except pg_errors.UniqueViolation:
        return error_response(
            'Ce numéro ou ce nom d\'utilisateur est déjà inscrit', 409, 'ALREADY_REGISTERED'
        )

    return jsonify({
        'token': generate_token(user_id, device_id),
        'user_id': user_id,
        'device_id': device_id,
        'display_name': display_name
    }), 201


@auth_bp.route('/login', methods=['POST'])
@swag_from('../descriptions/auth/login.yml')
def login():
    """Connexion : numéro + secret de l'appareil enregistré.

    Le numéro seul ne suffit plus. Si l'appareil n'est pas reconnu, la réponse
    DEVICE_NOT_RECOGNIZED indique à l'application de lancer l'activation.
    """
    data = request.get_json(silent=True) or {}
    phone_number = data.get('phone_number')
    if not phone_number:
        return error_response('Téléphone requis', 400, 'PHONE_REQUIRED')
    device = parse_device_payload(data)

    user = execute_query("""
        SELECT u.id, u.display_name, u.username, u.is_suspended,
               d.id AS device_id,
               EXISTS (
                   SELECT 1 FROM gbekoun.user_devices a
                   WHERE a.user_id = u.id AND a.kind = 'mobile' AND a.revoked_at IS NULL
               ) AS has_active_device
        FROM gbekoun.users u
        -- Seul le téléphone se connecte avec son secret. Le PC d'un admin doit
        -- être validé par le téléphone à chaque session (/api/auth/device/web/requests).
        LEFT JOIN gbekoun.user_devices d
               ON d.user_id = u.id AND d.kind = 'mobile' AND d.revoked_at IS NULL
              AND d.secret_hash = %s
        WHERE u.phone_number = %s AND u.deleted_at IS NULL
    """, (device['secret_hash'], phone_number), fetch_one=True)

    if not user:
        return error_response('Utilisateur non trouvé', 404, 'USER_NOT_FOUND')

    if user.get('is_suspended'):
        return error_response('Compte suspendu', 403, 'ACCOUNT_SUSPENDED')

    if not user.get('device_id'):
        # has_active_device = True  → proposer « Confirmer sur l'ancien appareil » (ou code admin)
        # has_active_device = False → seul un code admin est possible
        return error_response(
            'Cet appareil n\'est pas autorisé pour ce compte.', 403, 'DEVICE_NOT_RECOGNIZED',
            has_active_device=bool(user.get('has_active_device')),
        )

    execute_query("UPDATE gbekoun.users SET last_seen = NOW() WHERE id = %s", (user['id'],))
    execute_query(
        "UPDATE gbekoun.user_devices SET last_seen_at = NOW() WHERE id = %s",
        (user['device_id'],)
    )

    return jsonify({
        'token': generate_token(user['id'], user['device_id']),
        'user_id': str(user['id']),
        'device_id': str(user['device_id']),
        'display_name': user['display_name'],
        'username': user['username']
    }), 200


# ===================== ROUTES PROTÉGÉES =====================

@auth_bp.route('/logout', methods=['POST'])
@require_auth
@swag_from('../descriptions/auth/logout.yml')
def logout():
    """Passe hors ligne. L'appareil reste enregistré : se reconnecter ne demande pas de code."""
    execute_query(
        "UPDATE gbekoun.users SET is_online = FALSE, last_seen = NOW() WHERE id = %s",
        (g.current_user_id,)
    )
    return jsonify({'message': 'Déconnecté avec succès'}), 200


@auth_bp.route('/verify', methods=['GET'])
@require_auth
@swag_from('../descriptions/auth/verify.yml')
def verify_token_route():
    user = execute_query(
        "SELECT id, phone_number, display_name, username FROM gbekoun.users WHERE id = %s",
        (g.current_user_id,), fetch_one=True
    )

    return jsonify({
        'valid': True,
        'user_id': g.current_user_id,
        'phone_number': user['phone_number'] if user else None,
        'display_name': user['display_name'] if user else None,
        'username': user['username'] if user else None
    }), 200


# ===================== CODES D'INVITATION (ADMIN) =====================

@auth_bp.route('/invite-codes', methods=['POST'])
@require_auth
@require_admin
@swag_from('../descriptions/auth/invite_codes_post.yml')
def generate_invite_codes():
    data = request.get_json(silent=True) or {}
    count = data.get('count', 1)
    expires_days = data.get('expires_days', 30)

    if not isinstance(count, int) or not 1 <= count <= MAX_CODES_PER_REQUEST:
        return jsonify({'error': f'count doit être entre 1 et {MAX_CODES_PER_REQUEST}'}), 400
    if not isinstance(expires_days, int) or expires_days < 1:
        return jsonify({'error': 'expires_days doit être un entier positif'}), 400

    expires_at = datetime.now(timezone.utc) + timedelta(days=expires_days)
    codes = []
    for _ in range(count):
        code = secrets.token_hex(3).upper()
        execute_query("""
            INSERT INTO gbekoun.invite_codes (code, created_by, expires_at)
            VALUES (%s, %s, %s)
        """, (code, g.current_user_id, expires_at))
        codes.append(code)

    return jsonify({
        'codes': codes,
        'count': len(codes),
        'expires_days': expires_days
    }), 201


@auth_bp.route('/invite-codes', methods=['GET'])
@require_auth
@require_admin
@swag_from('../descriptions/auth/invite_codes_get.yml')
def list_invite_codes():
    include_used = request.args.get('include_used', 'true').lower() == 'true'
    limit = request.args.get('limit', 100, type=int)

    query = """
        SELECT ic.code, ic.used_by, ic.used_at, ic.created_at, ic.expires_at,
               u.display_name as used_by_name
        FROM gbekoun.invite_codes ic
        LEFT JOIN gbekoun.users u ON u.id = ic.used_by
    """
    if not include_used:
        query += " WHERE ic.used_by IS NULL"
    # L'espace en tête est indispensable : sans lui on obtenait « IS NULLORDER BY ».
    query += " ORDER BY ic.created_at DESC LIMIT %s"

    codes = execute_query(query, (limit,), fetch_all=True)
    return jsonify(codes), 200


@auth_bp.route('/invite-codes/<code>', methods=['DELETE'])
@require_auth
@require_admin
@swag_from('../descriptions/auth/invite_codes_delete.yml')
def delete_invite_code(code):
    code_data = execute_query(
        "SELECT used_by FROM gbekoun.invite_codes WHERE code = %s",
        (code,), fetch_one=True
    )

    if not code_data:
        return jsonify({'error': 'Code non trouvé'}), 404

    if code_data['used_by']:
        return jsonify({'error': 'Impossible de supprimer un code déjà utilisé'}), 400

    execute_query("DELETE FROM gbekoun.invite_codes WHERE code = %s", (code,))
    return jsonify({'message': 'Code supprimé avec succès'}), 200


@auth_bp.route('/invite-codes/stats', methods=['GET'])
@require_auth
@require_admin
@swag_from('../descriptions/auth/invite_codes_stats.yml')
def invite_codes_stats():
    stats = execute_query("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE used_by IS NOT NULL) as used,
            COUNT(*) FILTER (WHERE used_by IS NULL AND expires_at > NOW()) as unused,
            COUNT(*) FILTER (WHERE used_by IS NULL AND expires_at <= NOW()) as expired
        FROM gbekoun.invite_codes
    """, fetch_one=True)

    total_users = execute_query(
        "SELECT COUNT(*) as count FROM gbekoun.users",
        fetch_one=True
    )

    used_percentage = 0
    if stats['total'] > 0:
        used_percentage = round((stats['used'] / stats['total']) * 100, 2)

    return jsonify({
        'total': stats['total'],
        'used': stats['used'],
        'unused': stats['unused'],
        'expired': stats['expired'],
        'used_percentage': used_percentage,
        'total_users': total_users['count'] if total_users else 0
    }), 200
