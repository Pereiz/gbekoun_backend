// =============================================================
// SCRIPT D'INSTALLATION - BASE DE DONNÉES DE MESSAGERIE
// MongoDB
// Base : gbekoundb_messages
// Utilisateur : gbekoundb_user
// =============================================================

// 1. SE CONNECTER AVEC UN UTILISATEUR ADMIN D'ABORD
// mongosh -u admin -p --authenticationDatabase admin
// OU si pas d'auth: mongosh

// 2. CRÉATION DE L'UTILISATEUR DÉDIÉ
// =============================================================

use admin;

// Créer l'utilisateur s'il n'existe pas
if (!db.getUser("gbekoundb_user")) {
    db.createUser({
        user: "gbekoundb_user",
        pwd: "VotreMotDePasseSecurise123!",
        roles: [
            { role: "readWrite", db: "gbekoundb_messages" },
            { role: "dbAdmin", db: "gbekoundb_messages" }
        ]
    });
    print("Utilisateur gbekoundb_user créé");
} else {
    print("Utilisateur gbekoundb_user existe déjà");
}

// 3. CRÉATION DE LA BASE DE DONNÉES
// =============================================================
use gbekoundb_messages;

// 4. CRÉATION DES COLLECTIONS AVEC VALIDATION
// =============================================================

// Collection : messages
db.createCollection("messages", {
    validator: {
        $jsonSchema: {
            bsonType: "object",
            required: ["conversation_id", "sender_id", "content", "timestamp"],
            properties: {
                conversation_id: {
                    bsonType: "string",
                    description: "ID de la conversation (UUID PostgreSQL)"
                },
                sender_id: {
                    bsonType: "string",
                    description: "ID de l'expéditeur (UUID)"
                },
                type: {
                    bsonType: "string",
                    enum: ["text", "image", "video", "audio", "file", "location", "contact"],
                    description: "Type de message"
                },
                content: {
                    bsonType: "string",
                    description: "Contenu du texte ou URL du fichier"
                },
                reply_to_id: {
                    bsonType: "objectId",
                    description: "ID du message original auquel on répond"
                },
                reactions: {
                    bsonType: "array",
                    items: {
                        bsonType: "object",
                        properties: {
                            user_id: { bsonType: "string" },
                            reaction: { bsonType: "string" }
                        }
                    }
                },
                read_by: {
                    bsonType: "array",
                    items: { bsonType: "string" },
                    description: "IDs des utilisateurs ayant lu"
                },
                deleted_for: {
                    bsonType: "array",
                    items: { bsonType: "string" },
                    description: "IDs des utilisateurs ayant supprimé pour eux"
                },
                edited_at: {
                    bsonType: "date",
                    description: "Date de dernière modification"
                },
                timestamp: {
                    bsonType: "date",
                    description: "Date d'envoi"
                },
                metadata: {
                    bsonType: "object",
                    description: "Métadonnées supplémentaires"
                }
            }
        }
    }
});

// 5. CRÉATION DES INDEX
// =============================================================

// Index principal pour la pagination des messages par conversation
db.messages.createIndex(
    { "conversation_id": 1, "timestamp": -1 },
    { name: "idx_conversation_timestamp" }
);

// Index pour récupérer les messages d'un expéditeur
db.messages.createIndex(
    { "sender_id": 1, "timestamp": -1 },
    { name: "idx_sender_timestamp" }
);

// Index pour le tri chronologique global
db.messages.createIndex(
    { "timestamp": -1 },
    { name: "idx_timestamp" }
);

// Index TTL pour supprimer les vieux messages (optionnel, 1 an)
db.messages.createIndex(
    { "timestamp": 1 },
    { expireAfterSeconds: 31536000, name: "idx_ttl_timestamp" }
);

// Index pour les messages non lus
db.messages.createIndex(
    { "read_by": 1 },
    { name: "idx_read_by" }
);

// Index pour les messages supprimés
db.messages.createIndex(
    { "deleted_for": 1 },
    { name: "idx_deleted_for" }
);

// Index pour les réponses aux messages
db.messages.createIndex(
    { "reply_to_id": 1 },
    { name: "idx_reply_to" }
);

// 6. COLLECTION POUR LES MÉDIAS (fichiers uploadés)
// =============================================================
db.createCollection("media", {
    validator: {
        $jsonSchema: {
            bsonType: "object",
            required: ["file_id", "filename", "uploaded_by", "uploaded_at"],
            properties: {
                file_id: { bsonType: "string", description: "ID unique du fichier" },
                filename: { bsonType: "string", description: "Nom original" },
                file_size: { bsonType: "number", description: "Taille en octets" },
                mime_type: { bsonType: "string", description: "Type MIME" },
                file_path: { bsonType: "string", description: "Chemin sur disque" },
                thumbnail_path: { bsonType: "string", description: "Chemin miniature" },
                duration: { bsonType: "number", description: "Durée (audio/vidéo)" },
                uploaded_by: { bsonType: "string", description: "ID utilisateur" },
                conversation_id: { bsonType: "string", description: "Conversation associée" },
                message_id: { bsonType: "objectId", description: "Message associé" },
                uploaded_at: { bsonType: "date", description: "Date upload" }
            }
        }
    }
});

// Index pour les médias
db.media.createIndex({ "file_id": 1 }, { unique: true, name: "idx_file_id" });
db.media.createIndex({ "uploaded_by": 1, "uploaded_at": -1 }, { name: "idx_user_media" });
db.media.createIndex({ "conversation_id": 1 }, { name: "idx_media_conversation" });

// 7. COLLECTION POUR LES TYPING INDICATORS (stockage temporaire)
// =============================================================
db.createCollection("typing_status");

// Index TTL pour effacer automatiquement après 5 secondes
db.typing_status.createIndex(
    { "updated_at": 1 },
    { expireAfterSeconds: 5, name: "idx_typing_ttl" }
);

// 8. COLLECTION POUR LES SESSIONS WEBSOCKET (backup)
// =============================================================
db.createCollection("ws_sessions");

db.ws_sessions.createIndex({ "user_id": 1 }, { name: "idx_ws_user" });
db.ws_sessions.createIndex({ "socket_id": 1 }, { name: "idx_ws_socket" });
db.ws_sessions.createIndex({ "last_heartbeat": 1 }, { name: "idx_ws_heartbeat" });

// 9. AFFICHAGE DES RÉSULTATS
// =============================================================

print("\n==========================================");
print("✅ BASE DE DONNÉES MONGODB CRÉÉE AVEC SUCCÈS");
print("==========================================");
print("Base : gbekoundb_messages");
print("Utilisateur : gbekoundb_user");
print("\nCollections créées :");
print("  - messages (" + db.messages.count() + " documents)");
print("  - media (" + db.media.count() + " documents)");
print("  - typing_status (" + db.typing_status.count() + " documents)");
print("  - ws_sessions (" + db.ws_sessions.count() + " documents)");
print("\nIndex créés sur messages :");
db.messages.getIndexes().forEach(idx => print("  - " + idx.name));
print("==========================================");

// =============================================================
// FIN DU SCRIPT MONGODB
// =============================================================
