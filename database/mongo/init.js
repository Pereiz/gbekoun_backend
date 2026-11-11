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

// Compte de l'application à droits limités, sur CETTE base uniquement.
// Rôle sur mesure plutôt que « readWrite » : readWrite permet aussi de
// supprimer une collection entière (dropCollection) ou de modifier son
// validateur. L'API n'a besoin que de lire/ajouter/modifier/supprimer des
// documents. Le compte root reste réservé à l'initialisation et aux sauvegardes.
const appUser = typeof process !== 'undefined' ? process.env.MONGO_APP_USER : undefined;
const appPassword = typeof process !== 'undefined' ? process.env.MONGO_APP_PASSWORD : undefined;
if (appUser && appPassword) {
    const roleName = 'gbekounApp';
    // Uniquement la collection « messages » (la seule utilisée par l'API) :
    // pas de création d'autres collections.
    const privileges = [{
        resource: { db: dbName, collection: 'messages' },
        actions: ['find', 'insert', 'update', 'remove'],
    }];
    if (target.getRole(roleName)) {
        target.updateRole(roleName, { privileges, roles: [] });
    } else {
        target.createRole({ role: roleName, privileges, roles: [] });
    }
    const roles = [{ role: roleName, db: dbName }];
    if (target.getUser(appUser)) {
        target.updateUser(appUser, { pwd: appPassword, roles });
    } else {
        target.createUser({ user: appUser, pwd: appPassword, roles });
    }
    print(`Compte applicatif ${appUser} prêt (lecture/écriture de documents sur ${dbName} uniquement).`);
} else {
    print('MONGO_APP_USER / MONGO_APP_PASSWORD absents : compte applicatif non créé.');
}

print(`MongoDB initialisée : base ${dbName}, collections : ${target.getCollectionNames().join(', ')}`);
