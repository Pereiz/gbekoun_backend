"""Règles de visibilité des utilisateurs (numéros de téléphone, contacts, blocages)."""
import re

from core.database import execute_query
from core.http import ApiError

_PHONE_QUERY = re.compile(r'\+?\d{8,15}')


def mask_phone(phone):
    """+22991000056 → +229••••••56 (permet de distinguer deux homonymes sans révéler le numéro)."""
    if not phone or len(phone) < 7:
        return None
    return phone[:4] + '•' * (len(phone) - 6) + phone[-2:]


def phone_candidates(text):
    """Si `text` est un numéro complet, retourne les écritures possibles en base, sinon []."""
    compact = re.sub(r'[\s\-().]', '', text or '')
    if not _PHONE_QUERY.fullmatch(compact):
        return []
    return [compact] if compact.startswith('+') else [compact, '+' + compact]


def escape_like(text):
    """Le texte saisi est cherché tel quel : % et _ ne sont plus des jokers SQL."""
    return text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def are_contacts(user_a, user_b):
    """Vrai si les deux utilisateurs partagent au moins une conversation active."""
    if str(user_a) == str(user_b):
        return True
    return bool(execute_query("""
        SELECT 1
        FROM gbekoun.conversation_participants cp1
        JOIN gbekoun.conversation_participants cp2 ON cp2.conversation_id = cp1.conversation_id
        WHERE cp1.user_id = %s AND cp2.user_id = %s
          AND cp1.deleted_at IS NULL AND cp2.deleted_at IS NULL
        LIMIT 1
    """, (user_a, user_b), fetch_one=True))


def is_blocked_between(user_a, user_b):
    """Vrai si l'un des deux a bloqué l'autre."""
    return bool(execute_query("""
        SELECT 1 FROM gbekoun.blocked_users
        WHERE (blocker_id = %s AND blocked_id = %s) OR (blocker_id = %s AND blocked_id = %s)
        LIMIT 1
    """, (user_a, user_b, user_b, user_a), fetch_one=True))


def resolve_user_id(data, id_field='user_id', phone_field='phone_number'):
    """Identifie un utilisateur par `user_id` (résultat de recherche) ou par numéro.

    Retourne l'id (str) d'un compte actif, ou lève ApiError.
    """
    user_id = data.get(id_field)
    phone = data.get(phone_field)
    if not user_id and not phone:
        raise ApiError('USER_REQUIRED', f'{id_field} ou {phone_field} requis')

    if user_id:
        if not isinstance(user_id, str):
            raise ApiError('USER_NOT_FOUND', 'Utilisateur non trouvé', 404)
        row = execute_query("""
            SELECT id FROM gbekoun.users
            WHERE id::text = %s AND deleted_at IS NULL
        """, (user_id,), fetch_one=True)
    else:
        row = execute_query("""
            SELECT id FROM gbekoun.users WHERE phone_number = %s AND deleted_at IS NULL
        """, (phone,), fetch_one=True)

    if not row:
        raise ApiError('USER_NOT_FOUND', 'Utilisateur non trouvé', 404)
    return str(row['id'])
