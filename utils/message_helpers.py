from datetime import datetime, timezone

from bson import ObjectId


def to_utc_datetime(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def to_utc_isoformat(value):
    value = to_utc_datetime(value)
    return value.isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def serialize_message_value(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return to_utc_isoformat(value)
    if isinstance(value, dict):
        return {key: serialize_message_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize_message_value(item) for item in value]
    return value


def parse_object_id(value):
    """Convertit en ObjectId, ou retourne None si l'identifiant est invalide."""
    if isinstance(value, str) and ObjectId.is_valid(value):
        return ObjectId(value)
    return None


def set_user_reaction(messages_col, message_object_id, user_id, reaction):
    """Remplace la réaction de l'utilisateur en une seule opération atomique.

    Un `$pull` + `$push` sur le même champ dans une seule requête est refusé par
    MongoDB (conflit de chemin) : on passe donc par un pipeline d'agrégation.
    """
    return messages_col.update_one(
        {'_id': message_object_id},
        [{
            '$set': {
                'reactions': {
                    '$concatArrays': [
                        {
                            '$filter': {
                                'input': {'$ifNull': ['$reactions', []]},
                                'as': 'existing_reaction',
                                'cond': {'$ne': ['$$existing_reaction.user_id', user_id]},
                            }
                        },
                        [{
                            'user_id': user_id,
                            'reaction': reaction,
                            'timestamp': datetime.now(timezone.utc),
                        }],
                    ]
                }
            }
        }]
    )


def validate_reply_to(messages_col, conversation_id, user_id, reply_to_id):
    if reply_to_id is None:
        return None, None

    if not isinstance(reply_to_id, str) or not ObjectId.is_valid(reply_to_id):
        return None, 'ID du message auquel répondre invalide'

    reply_object_id = ObjectId(reply_to_id)
    replied_message = messages_col.find_one({
        '_id': reply_object_id,
        'conversation_id': conversation_id,
        'deleted_for': {'$ne': user_id},
        'is_deleted': {'$ne': True}
    })
    if not replied_message:
        return None, 'Message auquel répondre introuvable dans cette conversation'

    return reply_object_id, None
