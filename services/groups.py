"""Départ d'une conversation et succession des administrateurs de groupe.

Règle : un groupe qui a encore des membres a toujours au moins un admin.
Quand le dernier admin part (départ volontaire, « supprimer la discussion »,
retrait, suppression du compte), le membre le plus ancien devient admin.
"""
from core.realtime import conversation_room, remove_users_from_conversation
from core.extensions import socketio


def lock_conversation(cur, conversation_id):
    """Sérialise les départs/promotions d'une même conversation.

    Sans verrou, deux admins qui partent en même temps verraient chacun
    l'autre encore admin, et le groupe se retrouverait sans admin.
    """
    cur.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f'conversation:{conversation_id}',)
    )


def leave_conversation(cur, conversation_id, user_id):
    """Retire user_id de la conversation (dans une transaction).

    Retourne None s'il n'était pas membre actif, sinon
    {'conversation_id', 'type', 'user_id', 'promoted_user_id'}.
    """
    lock_conversation(cur, conversation_id)
    cur.execute("""
        SELECT c.type, cp.is_admin
        FROM gbekoun.conversation_participants cp
        JOIN gbekoun.conversations c ON c.id = cp.conversation_id
        WHERE cp.conversation_id = %s AND cp.user_id = %s AND cp.deleted_at IS NULL
    """, (conversation_id, user_id))
    member = cur.fetchone()
    if not member:
        return None

    cur.execute("""
        UPDATE gbekoun.conversation_participants
        SET deleted_at = NOW(), is_admin = FALSE, unread_count = 0
        WHERE conversation_id = %s AND user_id = %s
    """, (conversation_id, user_id))

    promoted = None
    if member['type'] == 'group' and member['is_admin']:
        cur.execute("""
            SELECT 1 FROM gbekoun.conversation_participants
            WHERE conversation_id = %s AND is_admin = TRUE AND deleted_at IS NULL
            LIMIT 1
        """, (conversation_id,))
        if not cur.fetchone():
            cur.execute("""
                UPDATE gbekoun.conversation_participants
                SET is_admin = TRUE
                WHERE conversation_id = %s AND user_id = (
                    SELECT user_id FROM gbekoun.conversation_participants
                    WHERE conversation_id = %s AND deleted_at IS NULL
                    ORDER BY joined_at ASC, user_id ASC
                    LIMIT 1
                )
                RETURNING user_id
            """, (conversation_id, conversation_id))
            row = cur.fetchone()
            promoted = str(row['user_id']) if row else None

    return {
        'conversation_id': str(conversation_id),
        'type': member['type'],
        'user_id': str(user_id),
        'promoted_user_id': promoted,
    }


def active_conversation_ids(cur, user_id):
    cur.execute("""
        SELECT conversation_id FROM gbekoun.conversation_participants
        WHERE user_id = %s AND deleted_at IS NULL
    """, (user_id,))
    return [str(row['conversation_id']) for row in cur.fetchall()]


def leave_all_conversations(cur, user_id):
    """Suppression de compte : quitte toutes les conversations (avec succession)."""
    departures = []
    for conversation_id in active_conversation_ids(cur, user_id):
        result = leave_conversation(cur, conversation_id, user_id)
        if result:
            departures.append(result)
    return departures


def announce_departures(departures, removed_by=None):
    """À appeler APRÈS le commit : sockets retirés de la room + événements temps réel."""
    for departure in departures:
        conversation_id = departure['conversation_id']
        remove_users_from_conversation([departure['user_id']], conversation_id)
        if departure['type'] != 'group':
            continue
        socketio.emit('participant_left', {
            'conversation_id': conversation_id,
            'user_id': departure['user_id'],
            'removed_by': removed_by,
        }, room=conversation_room(conversation_id))
        if departure['promoted_user_id']:
            socketio.emit('admin_changed', {
                'conversation_id': conversation_id,
                'user_id': departure['promoted_user_id'],
                'is_admin': True,
                'reason': 'last_admin_left',
            }, room=conversation_room(conversation_id))
