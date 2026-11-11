"""Notifications push Expo envoyées à la réception d'un message."""
import logging

from core.database import execute_query
from core.extensions import socketio
from utils.expo_push import send_expo_messages

logger = logging.getLogger(__name__)

EXPO_BATCH_SIZE = 100


def _deliver_message_pushes(conversation_id, sender_id, message_id):
    try:
        devices = execute_query("""
            SELECT pd.expo_push_token
            FROM gbekoun.conversation_participants cp
            JOIN gbekoun.push_devices pd ON pd.user_id = cp.user_id
            WHERE cp.conversation_id = %s
              AND cp.user_id != %s
              AND cp.deleted_at IS NULL
              AND (cp.muted_until IS NULL OR cp.muted_until <= NOW())
              AND pd.is_active = TRUE
        """, (conversation_id, sender_id), fetch_all=True)

        if not devices:
            return

        push_messages = [{
            'to': device['expo_push_token'],
            'title': 'GBEKOUN',
            'body': 'Vous avez reçu un nouveau message.',
            'sound': 'default',
            'data': {
                'type': 'message',
                'conversation_id': str(conversation_id),
                'message_id': str(message_id),
            },
        } for device in devices]

        for offset in range(0, len(push_messages), EXPO_BATCH_SIZE):
            batch = push_messages[offset:offset + EXPO_BATCH_SIZE]
            response = send_expo_messages(batch)
            tickets = response.get('data', [])
            for push_message, ticket in zip(batch, tickets):
                if ticket.get('status') != 'error':
                    continue

                error = ticket.get('details', {}).get('error')
                if error == 'DeviceNotRegistered':
                    execute_query("""
                        UPDATE gbekoun.push_devices
                        SET is_active = FALSE, updated_at = NOW()
                        WHERE expo_push_token = %s
                    """, (push_message['to'],))
                else:
                    logger.warning('Expo push rejected a message: %s', error)
    except Exception:
        logger.exception('Failed to deliver Expo push notifications')


def _deliver_device_approval_push(user_id, request_payload):
    try:
        devices = execute_query("""
            SELECT expo_push_token FROM gbekoun.push_devices
            WHERE user_id = %s AND is_active = TRUE
        """, (user_id,), fetch_all=True)
        if not devices:
            return
        device_label = request_payload.get('device_name') or 'un nouvel appareil'
        if request_payload.get('kind') == 'web':
            body = f'Autoriser l\'accès administration sur {device_label} (1 h) ?'
        else:
            body = f'Autoriser la connexion sur {device_label} ? Ce téléphone sera déconnecté.'
        send_expo_messages([{
            'to': device['expo_push_token'],
            'title': 'GBEKOUN - Nouvelle connexion',
            'body': body,
            'sound': 'default',
            'priority': 'high',
            'data': {'type': 'device_approval', **request_payload},
        } for device in devices])
    except Exception:
        logger.exception('Failed to deliver device approval push')


def notify_device_approval_requested(user_id, request_payload):
    """Prévient l'appareil actif qu'un nouvel appareil demande à se connecter."""
    socketio.start_background_task(_deliver_device_approval_push, user_id, request_payload)


def notify_new_message(conversation_id, sender_id, message_id):
    """Envoie les push en arrière-plan pour ne pas ralentir l'envoi du message."""
    socketio.start_background_task(
        _deliver_message_pushes, conversation_id, sender_id, message_id
    )
