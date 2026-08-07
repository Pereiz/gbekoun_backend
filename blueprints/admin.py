# blueprints/admin.py
import os
import json
import shutil
import subprocess
from datetime import datetime, timedelta
from functools import wraps
from flask import Blueprint, request, jsonify, g
from flasgger import swag_from
from bson.objectid import ObjectId
from psycopg2 import sql
from app import execute_query, require_auth, messages_col, mongo_client

admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')


# ===================== DÉCORATEUR ADMIN =====================

def require_admin(f):
    """Décorateur pour vérifier que l'utilisateur est admin"""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Vérifier si l'utilisateur est admin
        user = execute_query(
            sql.SQL("""SELECT is_admin FROM gbekoun.users WHERE id = %s"""),
            (g.current_user_id,), fetch_one=True
        )
        if not user or not user.get('is_admin'):
            return jsonify({'error': 'Accès non autorisé. Droits administrateur requis.'}), 403
        return f(*args, **kwargs)
    return decorated


# ===================== STATISTIQUES =====================

@admin_bp.route('/stats/users', methods=['GET'])
@require_auth
@require_admin
@swag_from('../descriptions/admin/stats_users.yml')
def stats_users():
    """
    Statistiques utilisateurs (total, actifs, nouveaux)
    """
    # Total des utilisateurs
    total = execute_query(
        sql.SQL("""SELECT COUNT(*) as count FROM gbekoun.users WHERE deleted_at IS NULL"""),
        fetch_one=True
    )
    
    # Utilisateurs actifs aujourd'hui (qui ont eu une activité)
    today_active = execute_query(sql.SQL("""
        SELECT COUNT(DISTINCT user_id) as count 
        FROM gbekoun.conversation_participants 
        WHERE joined_at >= NOW() - INTERVAL '1 day'
    """), fetch_one=True)
    
    # Nouveaux utilisateurs (7 derniers jours)
    new_users = execute_query(sql.SQL("""
        SELECT COUNT(*) as count 
        FROM gbekoun.users 
        WHERE created_at >= NOW() - INTERVAL '7 days'
    """), fetch_one=True)
    
    # Utilisateurs suspendus
    suspended = execute_query(
        sql.SQL("""SELECT COUNT(*) as count FROM gbekoun.users WHERE is_suspended = TRUE"""),
        fetch_one=True
    )
    
    # Utilisateurs en ligne actuellement
    online = execute_query(
        sql.SQL("""SELECT COUNT(*) as count FROM gbekoun.users WHERE is_online = TRUE"""),
        fetch_one=True
    )
    
    return jsonify({
        'total': total['count'] if total else 0,
        'active_today': today_active['count'] if today_active else 0,
        'new_last_7_days': new_users['count'] if new_users else 0,
        'suspended': suspended['count'] if suspended else 0,
        'online_now': online['count'] if online else 0
    }), 200


@admin_bp.route('/stats/messages', methods=['GET'])
@require_auth
@require_admin
@swag_from('../descriptions/admin/stats_messages.yml')
def stats_messages():
    """
    Statistiques messages (par jour, total)
    """
    try:

        # Total des messages
        total_messages = messages_col.count_documents({})
        
        # Messages aujourd'hui
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_messages = messages_col.count_documents({
            'timestamp': {'$gte': today_start}
        })
        
        # Messages cette semaine
        week_ago = datetime.utcnow() - timedelta(days=7)
        week_messages = messages_col.count_documents({
            'timestamp': {'$gte': week_ago}
        })
        
        # Messages par type
        messages_by_type = list(messages_col.aggregate([
            {'$group': {'_id': '$type', 'count': {'$sum': 1}}}
        ]))
        
        # Messages par jour (7 derniers jours)
        daily_stats = []
        for i in range(7, -1, -1):
            day = datetime.utcnow() - timedelta(days=i)
            day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            count = messages_col.count_documents({
                'timestamp': {'$gte': day_start, '$lt': day_end}
            })
            daily_stats.append({
                'date': day.strftime('%Y-%m-%d'),
                'count': count
            })
        
        return jsonify({
            'total': total_messages,
            'today': today_messages,
            'last_7_days': week_messages,
            'by_type': {item['_id']: item['count'] for item in messages_by_type if item['_id']},
            'daily': daily_stats
        }), 200
    except Exception as e:
        # Log l'erreur (avec import logging)
        import logging
        logging.error(f"Erreur dans stats_messages: {str(e)}")
        # Renvoyer une réponse détaillée en développement, ou 500 avec message
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/stats/conversations', methods=['GET'])
@require_auth
@require_admin
@swag_from('../descriptions/admin/stats_conversations.yml')
def stats_conversations():
    """
    Statistiques conversations
    """
    # Total des conversations
    total = execute_query(
        sql.SQL("""SELECT COUNT(*) as count FROM gbekoun.conversations"""),
        fetch_one=True
    )
    
    # Conversations par type
    by_type = execute_query(sql.SQL("""
        SELECT type, COUNT(*) as count 
        FROM gbekoun.conversations 
        GROUP BY type
    """), fetch_all=True)
    
    # Conversations actives (messages dans les 30 derniers jours)
    active = execute_query(sql.SQL("""
        SELECT COUNT(DISTINCT c.id) as count
        FROM gbekoun.conversations c
        WHERE c.updated_at >= NOW() - INTERVAL '30 days'
    """), fetch_one=True)
    
    # Messages par conversation (moyenne)
    avg_messages = messages_col.aggregate([
        {'$group': {'_id': '$conversation_id', 'count': {'$sum': 1}}},
        {'$group': {'_id': None, 'avg': {'$avg': '$count'}}}
    ])
    avg = list(avg_messages)
    
    return jsonify({
        'total': total['count'] if total else 0,
        'by_type': by_type if by_type else [],
        'active_last_30_days': active['count'] if active else 0,
        'avg_messages_per_conversation': round(avg[0]['avg'], 2) if avg else 0
    }), 200


# ===================== GESTION DES UTILISATEURS =====================

@admin_bp.route('/users', methods=['GET'])
@require_auth
@require_admin
@swag_from('../descriptions/admin/list_users.yml')
def list_users():
    """
    Liste tous les utilisateurs
    """
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)
    search = request.args.get('search', '')
    status = request.args.get('status', '')  # active, suspended, all
    
    query = """
        SELECT id, phone_number, display_name, username, 
               avatar_url, is_suspended, is_admin, is_online,
               created_at, last_seen
        FROM gbekoun.users
        WHERE deleted_at IS NULL
    """
    params = []
    
    if search:
        query += " AND (phone_number ILIKE %s OR display_name ILIKE %s OR username ILIKE %s)"
        search_param = f'%{search}%'
        params.extend([search_param, search_param, search_param])
    
    if status == 'suspended':
        query += " AND is_suspended = TRUE"
    elif status == 'active':
        query += " AND is_suspended = FALSE"
    
    query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    
    users = execute_query(sql.SQL(query), tuple(params), fetch_all=True)
    
    # Compter le total
    count_query = sql.SQL("""SELECT COUNT(*) as count FROM gbekoun.users WHERE deleted_at IS NULL""")
    total = execute_query(count_query, fetch_one=True)
    
    return jsonify({
        'users': users,
        'total': total['count'] if total else 0,
        'limit': limit,
        'offset': offset
    }), 200


@admin_bp.route('/users/<user_id>/suspend', methods=['POST'])
@require_auth
@require_admin
@swag_from('../descriptions/admin/suspend_user.yml')
def suspend_user(user_id):
    """
    Suspend un compte
    """
    data = request.get_json()
    reason = data.get('reason', 'Aucune raison fournie')
    
    # Vérifier que l'utilisateur existe
    user = execute_query(
        sql.SQL("""SELECT id FROM gbekoun.users WHERE id = %s AND deleted_at IS NULL"""),
        (user_id,), fetch_one=True
    )
    if not user:
        return jsonify({'error': 'Utilisateur non trouvé'}), 404
    
    # Ne pas se suspendre soi-même
    if user_id == g.current_user_id:
        return jsonify({'error': 'Vous ne pouvez pas suspendre votre propre compte'}), 400
    
    # Suspendre l'utilisateur
    execute_query(sql.SQL("""
        UPDATE gbekoun.users 
        SET is_suspended = TRUE, suspended_at = NOW(), suspended_reason = %s
        WHERE id = %s
    """), (reason, user_id))
    
    # Déconnecter l'utilisateur (forcer offline)
    execute_query(sql.SQL("""
        UPDATE gbekoun.users SET is_online = FALSE WHERE id = %s
    """), (user_id,))
    
    return jsonify({
        'message': 'Utilisateur suspendu avec succès',
        'user_id': user_id,
        'reason': reason
    }), 200


@admin_bp.route('/users/<user_id>/unsuspend', methods=['POST'])
@require_auth
@require_admin
@swag_from('../descriptions/admin/unsuspend_user.yml')
def unsuspend_user(user_id):
    """
    Réactive un compte suspendu
    """
    user = execute_query(
        sql.SQL("""SELECT id FROM gbekoun.users WHERE id = %s"""),
        (user_id,), fetch_one=True
    )
    if not user:
        return jsonify({'error': 'Utilisateur non trouvé'}), 404
    
    execute_query(sql.SQL("""
        UPDATE gbekoun.users 
        SET is_suspended = FALSE, suspended_at = NULL, suspended_reason = NULL
        WHERE id = %s
    """), (user_id,))
    
    return jsonify({
        'message': 'Compte réactivé avec succès',
        'user_id': user_id
    }), 200


@admin_bp.route('/users/<user_id>/delete', methods=['DELETE'])
@require_auth
@require_admin
@swag_from('../descriptions/admin/delete_user.yml')
def delete_user(user_id):
    """
    Supprime définitivement un compte
    """
    # Vérifier que l'utilisateur existe
    user = execute_query(
        sql.SQL("""SELECT id FROM gbekoun.users WHERE id = %s"""),
        (user_id,), fetch_one=True
    )
    if not user:
        return jsonify({'error': 'Utilisateur non trouvé'}), 404
    
    # Ne pas se supprimer soi-même
    if user_id == g.current_user_id:
        return jsonify({'error': 'Vous ne pouvez pas supprimer votre propre compte via l\'admin'}), 400
    
    # Supprimer tous les messages de l'utilisateur dans MongoDB
    messages_col.delete_many({'sender_id': user_id})
    
    # Supprimer l'utilisateur de PostgreSQL (hard delete)
    execute_query(sql.SQL("""DELETE FROM gbekoun.users WHERE id = %s"""), (user_id,))
    
    return jsonify({
        'message': 'Compte supprimé définitivement',
        'user_id': user_id
    }), 200


# ===================== GESTION DES SIGNALEMENTS =====================

@admin_bp.route('/reports', methods=['GET'])
@require_auth
@require_admin
@swag_from('../descriptions/admin/list_reports.yml')
def list_reports():
    """
    Liste tous les signalements
    """
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)
    status_filter = request.args.get('status', 'pending')  # pending, resolved, all
    
    query = """
        SELECT r.*, 
               reporter.display_name as reporter_name,
               reporter.phone_number as reporter_phone,
               reported.display_name as reported_name,
               reported.phone_number as reported_phone
        FROM gbekoun.reports r
        LEFT JOIN gbekoun.users reporter ON reporter.id = r.reporter_id
        LEFT JOIN gbekoun.users reported ON reported.id = r.reported_id
        WHERE 1=1
    """
    params = []
    
    if status_filter != 'all':
        query += " AND r.status = %s"
        params.append(status_filter)
    
    query += " ORDER BY r.created_at DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    
    reports = execute_query(sql.SQL(query), tuple(params), fetch_all=True)
    
    # Compter le total par statut
    stats = execute_query(sql.SQL("""
        SELECT status, COUNT(*) as count 
        FROM gbekoun.reports 
        GROUP BY status
    """), fetch_all=True)
    
    return jsonify({
        'reports': reports,
        'stats': stats if stats else [],
        'limit': limit,
        'offset': offset
    }), 200


@admin_bp.route('/reports/<int:report_id>/resolve', methods=['POST'])
@require_auth
@require_admin
@swag_from('../descriptions/admin/resolve_report.yml')
def resolve_report(report_id):
    """
    Résout un signalement
    """
    data = request.get_json()
    action = data.get('action', 'ignored')  # ignored, warned, suspended, banned
    
    # Vérifier que le signalement existe
    report = execute_query(
        sql.SQL("""SELECT * FROM gbekoun.reports WHERE id = %s"""),
        (report_id,), fetch_one=True
    )
    if not report:
        return jsonify({'error': 'Signalement non trouvé'}), 404
    
    # Mettre à jour le signalement
    execute_query(sql.SQL("""
        UPDATE gbekoun.reports 
        SET status = 'resolved', resolved_by = %s, resolved_at = NOW()
        WHERE id = %s
    """), (g.current_user_id, report_id))
    
    # Appliquer l'action si nécessaire
    if action == 'suspended' and report.get('reported_id'):
        execute_query(sql.SQL("""
            UPDATE gbekoun.users 
            SET is_suspended = TRUE, suspended_at = NOW(), 
                suspended_reason = 'Signalement validé par admin'
            WHERE id = %s
        """), (report['reported_id'],))
    
    return jsonify({
        'message': 'Signalement résolu',
        'action': action,
        'report_id': report_id
    }), 200


# ===================== SAUVEGARDE ET RESTAURATION =====================

@admin_bp.route('/backup/create', methods=['POST'])
@require_auth
@require_admin
@swag_from('../descriptions/admin/backup_create.yml')
def create_backup():
    """
    Crée un backup complet du système
    """
    backup_dir = f"backups/backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    os.makedirs(backup_dir, exist_ok=True)
    
    # 1. Backup PostgreSQL
    pg_dump_cmd = f"""
        pg_dump -h {os.getenv('PG_HOST', 'localhost')} \
                 -p {os.getenv('PG_PORT', '5435')} \
                 -U {os.getenv('PG_USER', 'gbekoundb_user')} \
                 -d {os.getenv('PG_DATABASE', 'gbekoundb')} \
                 -F c -f {backup_dir}/postgresql_backup.dump
    """
    
    # 2. Backup MongoDB
    mongodump_cmd = f"""
        mongodump --uri="{os.getenv('MONGO_URI', 'mongodb://localhost:27017/')}" \
                  --out={backup_dir}/mongodb_backup
    """
    
    # 3. Backup des fichiers uploadés
    uploads_dir = "uploads"
    if os.path.exists(uploads_dir):
        shutil.copytree(uploads_dir, f"{backup_dir}/uploads")
    
    # Créer une archive
    archive_name = f"{backup_dir}.tar.gz"
    shutil.make_archive(backup_dir, 'gztar', backup_dir)
    
    # Nettoyer le dossier temporaire
    shutil.rmtree(backup_dir)
    
    return jsonify({
        'message': 'Backup créé avec succès',
        'backup_file': archive_name,
        'created_at': datetime.utcnow().isoformat()
    }), 200


@admin_bp.route('/backup/restore', methods=['POST'])
@require_auth
@require_admin
@swag_from('../descriptions/admin/backup_restore.yml')
def restore_backup():
    """
    Restaure un backup système
    """
    data = request.get_json()
    backup_file = data.get('backup_file')
    
    if not backup_file or not os.path.exists(backup_file):
        return jsonify({'error': 'Fichier de backup non trouvé'}), 404
    
    # Cette fonctionnalité nécessite une implémentation plus complexe
    # pour éviter la perte de données. Recommandation : utiliser un script séparé.
    
    return jsonify({
        'message': 'Restauration manuelle requise. Contactez l\'administrateur système.',
        'backup_file': backup_file
    }), 200


# ===================== MÉTRIQUES SYSTÈME =====================

@admin_bp.route('/metrics', methods=['GET'])
@require_auth
@require_admin
@swag_from('../descriptions/admin/metrics.yml')
def get_metrics():
    """
    Métriques système (CPU, mémoire, uptime)
    """
    import psutil
    
    # CPU
    cpu_percent = psutil.cpu_percent(interval=1)
    cpu_count = psutil.cpu_count()
    
    # Mémoire
    memory = psutil.virtual_memory()
    
    # Disque
    disk = psutil.disk_usage('/')
    
    # Uptime
    boot_time = psutil.boot_time()
    uptime = datetime.utcnow() - datetime.fromtimestamp(boot_time)
    
    # Processus Flask
    flask_process = None
    for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
        if 'python' in proc.info['name'] and 'flask' in ' '.join(proc.cmdline()):
            flask_process = proc.info
            break
    
    return jsonify({
        'cpu': {
            'percent': cpu_percent,
            'cores': cpu_count,
            'load_average': psutil.getloadavg() if hasattr(psutil, 'getloadavg') else None
        },
        'memory': {
            'total': memory.total,
            'available': memory.available,
            'percent': memory.percent,
            'used': memory.used,
            'free': memory.free
        },
        'disk': {
            'total': disk.total,
            'used': disk.used,
            'free': disk.free,
            'percent': disk.percent
        },
        'uptime': {
            'seconds': uptime.total_seconds(),
            'formatted': str(uptime).split('.')[0]
        },
        'flask_process': flask_process,
        'timestamp': datetime.utcnow().isoformat()
    }), 200


# ===================== FONCTIONS UTILITAIRES =====================

@admin_bp.route('/promote/<user_id>', methods=['POST'])
@require_auth
@require_admin
def promote_to_admin(user_id):
    """
    Promouvoit un utilisateur en administrateur
    """
    # Vérifier que l'utilisateur existe
    user = execute_query(
        sql.SQL("""SELECT id FROM gbekoun.users WHERE id = %s"""),
        (user_id,), fetch_one=True
    )
    if not user:
        return jsonify({'error': 'Utilisateur non trouvé'}), 404
    
    execute_query(sql.SQL("""
        UPDATE gbekoun.users SET is_admin = TRUE WHERE id = %s
    """), (user_id,))
    
    return jsonify({
        'message': 'Utilisateur promu administrateur',
        'user_id': user_id
    }), 200


@admin_bp.route('/demote/<user_id>', methods=['POST'])
@require_auth
@require_admin
def demote_from_admin(user_id):
    """
    Rétrograde un administrateur
    """
    # Vérifier que l'utilisateur existe
    user = execute_query(
        sql.SQL("""SELECT id FROM gbekoun.users WHERE id = %s"""),
        (user_id,), fetch_one=True
    )
    if not user:
        return jsonify({'error': 'Utilisateur non trouvé'}), 404
    
    # Ne pas se rétrograder soi-même
    if user_id == g.current_user_id:
        return jsonify({'error': 'Vous ne pouvez pas vous rétrograder vous-même'}), 400
    
    execute_query(sql.SQL("""
        UPDATE gbekoun.users SET is_admin = FALSE WHERE id = %s
    """), (user_id,))
    
    return jsonify({
        'message': 'Administrateur rétrogradé',
        'user_id': user_id
    }), 200