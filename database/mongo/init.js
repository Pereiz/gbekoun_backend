// =============================================================
// GBEKOUN - Initialisation MongoDB (collections, validation, index)
//
// Exécuté automatiquement par docker-compose au premier démarrage.
// Exécution manuelle :
//   mongosh "<MONGO_URI>" database/mongo/init.js
//
// Idempotent : les collections déjà présentes ne sont pas recréées.
// Pas de création d'utilisateur ni de mot de passe dans ce fichier.
// =============================================================

const dbName = (typeof process !== 'undefined' && process.env.MONGO_INITDB_DATABASE)
    || 'gbekoundb_messages';
const target = db.getSiblingDB(dbName);
const existing = target.getCollectionNames();

// Types autorisés : doit rester aligné avec services/messaging.py (MESSAGE_TYPES).
const messagesValidator = {
    $jsonSchema: {
        bsonType: 'object',
        required: ['conversation_id', 'sender_id', 'content', 'timestamp'],
        properties: {
            conversation_id: { bsonType: 'string', description: 'UUID de la conversation (PostgreSQL)' },
            sender_id: { bsonType: 'string', description: "UUID de l'expéditeur" },
            type: {
                bsonType: 'string',
                enum: ['text', 'image', 'video', 'audio', 'file', 'location', 'contact']
            },
            content: { bsonType: 'string', description: 'Texte ou URL du fichier ("" si supprimé)' },
            reply_to_id: { bsonType: 'objectId', description: 'Message auquel on répond' },
            reactions: {
                bsonType: 'array',
                items: {
                    bsonType: 'object',
                    properties: {
                        user_id: { bsonType: 'string' },
                        reaction: { bsonType: 'string' }
                    }
                }
            },
            read_by: { bsonType: 'array', items: { bsonType: 'string' } },
            deleted_for: { bsonType: 'array', items: { bsonType: 'string' } },
            edited_at: { bsonType: 'date' },
            is_deleted: { bsonType: 'bool', description: 'Supprimé pour tout le monde' },
            deleted_at: { bsonType: 'date' },
            deleted_by: { bsonType: 'string' },
            timestamp: { bsonType: 'date', description: "Date d'envoi (UTC)" },
            metadata: { bsonType: 'object' }
        }
    }
};

if (!existing.includes('messages')) {
    target.createCollection('messages', { validator: messagesValidator });
}

// Pagination de l'historique d'une conversation
target.messages.createIndex({ conversation_id: 1, timestamp: -1 }, { name: 'idx_conversation_timestamp' });
target.messages.createIndex({ sender_id: 1, timestamp: -1 }, { name: 'idx_sender_timestamp' });
target.messages.createIndex({ reply_to_id: 1 }, { name: 'idx_reply_to' });

// NB : l'ancien script créait un index TTL qui SUPPRIME automatiquement les
// messages de plus d'un an (idx_ttl_timestamp). Il n'est volontairement plus
// créé ici. Voir docs/CORRECTIONS.md pour le retirer d'une base existante.

print(`MongoDB initialisée : base ${dbName}, collections : ${target.getCollectionNames().join(', ')}`);
