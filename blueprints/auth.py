# blueprints/auth.py
from flask import Blueprint, request, jsonify, g
from flasgger import swag_from
import uuid
import secrets
from datetime import datetime, timedelta
from functools import wraps
from psycopg2 import sql
from app import execute_query, generate_token, verify_token, require_auth

auth_bp = Blueprint('auth', __name__)


# ===================== ROUTES PUBLIQUES =====================

@auth_bp.route('/register', methods=['POST'])
@swag_from('../descriptions/auth/register.yml')
def register():
    data = request.get_json()
    phone_number = data.get('phone_number')
    invite_code = data.get('invite_code')
    display_name = data.get('display_name', phone_number)
    username = data.get('username', phone_number)

    if not phone_number or not invite_code:
        return jsonify({'error': 'Téléphone et code d\'invitation requis'}), 400

    existing = execute_query(
        sql.SQL("""SELECT id FROM gbekoun.users WHERE phone_number = %s"""),
        (phone_number,), fetch_one=True
    )
    if existing:
        return jsonify({'error': 'Ce numéro est déjà inscrit'}), 409

    code_valid = execute_query(
        sql.SQL("""SELECT id, expires_at FROM gbekoun.invite_codes 
           WHERE code = %s AND used_by IS NULL AND expires_at > NOW()"""),
        (invite_code,), fetch_one=True
    )
    if not code_valid:
        return jsonify({'error': 'Code invalide ou expiré'}), 403

    user_id = str(uuid.uuid4())
    execute_query(
        sql.SQL("""INSERT INTO gbekoun.users (id, phone_number, display_name, username) 
           VALUES (%s, %s, %s, %s)"""),
        (user_id, phone_number, display_name, username)
    )

    execute_query(
        sql.SQL("""UPDATE gbekoun.invite_codes SET used_by = %s, used_at = NOW() WHERE code = %s"""),
        (user_id, invite_code)
    )

    token = generate_token(str(user_id))

    return jsonify({
        'token': token,
        'user_id': str(user_id),
        'display_name': display_name
    }), 201


@auth_bp.route('/login', methods=['POST'])
@swag_from('../descriptions/auth/login.yml')
def login():
    data = request.get_json()
    phone_number = data.get('phone_number')

    if not phone_number:
        return jsonify({'error': 'Téléphone requis'}), 400

    user = execute_query(
        sql.SQL("""SELECT id, display_name, username FROM gbekoun.users WHERE phone_number = %s"""),
        (phone_number,), fetch_one=True
    )

    if not user:
        return jsonify({'error': 'Utilisateur non trouvé'}), 404

    execute_query(
        sql.SQL("""UPDATE gbekoun.users SET last_seen = NOW() WHERE id = %s"""),
        (user['id'],)
    )

    token = generate_token(str(user['id']))

    return jsonify({
        'token': token,
        'user_id': str(user['id']),
        'display_name': user['display_name'],
        'username': user['username']
    }), 200


# ===================== ROUTES PROTÉGÉES =====================

@auth_bp.route('/logout', methods=['POST'])
@require_auth
@swag_from('../descriptions/auth/logout.yml')
def logout():
    execute_query(
        sql.SQL("""UPDATE gbekoun.users SET is_online = FALSE, last_seen = NOW() WHERE id = %s"""),
        (g.current_user_id,)
    )
    return jsonify({'message': 'Déconnecté avec succès'}), 200


@auth_bp.route('/verify', methods=['GET'])
@require_auth
@swag_from('../descriptions/auth/verify.yml')
def verify_token_route():
    user = execute_query(
        sql.SQL("""SELECT id, phone_number, display_name, username FROM gbekoun.users WHERE id = %s"""),
        (g.current_user_id,), fetch_one=True
    )

    return jsonify({
        'valid': True,
        'user_id': g.current_user_id,
        'phone_number': user['phone_number'] if user else None,
        'display_name': user['display_name'] if user else None,
        'username': user['username'] if user else None
    }), 200


# ===================== ROUTES ADMIN =====================
# def is_admin(user_id):
#     admin = execute_query(
#         "SELECT id FROM gbekoun.users ORDER BY created_at ASC LIMIT 1",
#         fetch_one=True
#     )

#     return admin and str(admin['id']) == str(user_id)
def is_admin(user_id):
    """Vérifie si un utilisateur est administrateur"""
    admin = execute_query(
        sql.SQL("""SELECT id, is_admin FROM gbekoun.users WHERE id = %s"""),
        (user_id,), fetch_one=True
    )
    
    return admin and str(admin['id']) == str(user_id) and admin.get('is_admin') is True


@auth_bp.route('/invite-codes', methods=['POST'])
@require_auth
@swag_from('../descriptions/auth/invite_codes_post.yml')
def generate_invite_codes():
    if not is_admin(g.current_user_id):
        return jsonify({'error': 'Seul l\'administrateur peut générer des codes'}), 403

    data = request.get_json()
    count = data.get('count', 1)
    expires_days = data.get('expires_days', 30)

    if count > 100:
        return jsonify({'error': 'Maximum 100 codes par génération'}), 400

    codes = []
    for _ in range(count):
        code = secrets.token_hex(3).upper()
        expires_at = datetime.utcnow() + timedelta(days=expires_days)

        execute_query(
            sql.SQL("""INSERT INTO gbekoun.invite_codes (code, created_by, expires_at) 
               VALUES (%s, %s, %s)"""),
            (code, g.current_user_id, expires_at)
        )
        codes.append(code)

    return jsonify({
        'codes': codes,
        'count': len(codes),
        'expires_days': expires_days
    }), 201


@auth_bp.route('/invite-codes', methods=['GET'])
@require_auth
@swag_from('../descriptions/auth/invite_codes_get.yml')
def list_invite_codes():
    if not is_admin(g.current_user_id):
        return jsonify({'error': 'Accès non autorisé'}), 403

    include_used = request.args.get('include_used', 'true').lower() == 'true'
    limit = request.args.get('limit', 100, type=int)

    query = """
        SELECT ic.code, ic.used_by, ic.used_at, ic.created_at, ic.expires_at,
               u.display_name as used_by_name
        FROM gbekoun.invite_codes ic
        LEFT JOIN gbekoun.users u ON u.id = ic.used_by
    """
    params = []

    if not include_used:
        query += " WHERE ic.used_by IS NULL"

    query += "ORDER BY ic.created_at DESC LIMIT %s"
    params.append(limit)

    codes = execute_query(sql.SQL(query), tuple(params), fetch_all=True)

    return jsonify(codes), 200


@auth_bp.route('/invite-codes/<code>', methods=['DELETE'])
@require_auth
@swag_from('../descriptions/auth/invite_codes_delete.yml')
def delete_invite_code(code):
    if not is_admin(g.current_user_id):
        return jsonify({'error': 'Accès non autorisé'}), 403

    code_data = execute_query(
        sql.SQL("""SELECT used_by FROM gbekoun.invite_codes WHERE code = %s"""),
        (code,), fetch_one=True
    )

    if not code_data:
        return jsonify({'error': 'Code non trouvé'}), 404

    if code_data['used_by']:
        return jsonify({'error': 'Impossible de supprimer un code déjà utilisé'}), 400

    execute_query(
        sql.SQL("""DELETE FROM gbekoun.invite_codes WHERE code = %s"""),
        (code,)
    )

    return jsonify({'message': 'Code supprimé avec succès'}), 200


@auth_bp.route('/invite-codes/stats', methods=['GET'])
@require_auth
@swag_from('../descriptions/auth/invite_codes_stats.yml')
def invite_codes_stats():
    if not is_admin(g.current_user_id):
        return jsonify({'error': 'Non autorisé'}), 403

    stats = execute_query(sql.SQL("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN used_by IS NOT NULL THEN 1 ELSE 0 END) as used,
            SUM(CASE WHEN used_by IS NULL AND expires_at > NOW() THEN 1 ELSE 0 END) as unused,
            SUM(CASE WHEN used_by IS NULL AND expires_at <= NOW() THEN 1 ELSE 0 END) as expired
        FROM gbekoun.invite_codes
    """), fetch_one=True)

    total_users = execute_query(
        sql.SQL("""SELECT COUNT(*) as count FROM gbekoun.users"""),
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