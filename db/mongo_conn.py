from pymongo import MongoClient, ASCENDING, DESCENDING
from app.config import Config

class MongoDB:
    def __init__(self):
        self.client = None
        self.db = None
        self.messages = None
    
    def connect(self):
        self.client = MongoClient(Config.MONGO_URI)
        self.db = self.client[Config.MONGO_DB]
        self.messages = self.db['messages']
        
        # Index
        self.messages.create_index([("conversation_id", ASCENDING), ("timestamp", DESCENDING)])
        self.messages.create_index([("sender_id", ASCENDING)])
        self.messages.create_index([("timestamp", DESCENDING)])
        
        return self
    
    def save_message(self, message_data):
        """Sauvegarde un message"""
        result = self.messages.insert_one(message_data)
        return result.inserted_id
    
    def get_messages(self, conversation_id, limit=50, before_id=None):
        """Récupère messages avec pagination"""
        query = {"conversation_id": conversation_id}
        if before_id:
            from bson.objectid import ObjectId
            query["_id"] = {"$lt": ObjectId(before_id)}
        
        messages = self.messages.find(query)\
            .sort("timestamp", DESCENDING)\
            .limit(limit)
        
        return list(messages)
    
    def delete_message(self, message_id, user_id, for_everyone=False):
        """Supprime un message"""
        from bson.objectid import ObjectId
        if for_everyone:
            result = self.messages.delete_one({"_id": ObjectId(message_id)})
        else:
            # Soft delete: ajouter deleted_for
            result = self.messages.update_one(
                {"_id": ObjectId(message_id)},
                {"$addToSet": {"deleted_for": user_id}}
            )
        return result.modified_count > 0
    
    def edit_message(self, message_id, new_content):
        """Modifie un message"""
        from bson.objectid import ObjectId
        result = self.messages.update_one(
            {"_id": ObjectId(message_id)},
            {"$set": {"content": new_content, "edited_at": datetime.utcnow()}}
        )
        return result.modified_count > 0

# Instance globale
mongo_db = MongoDB()

def init_mongodb():
    mongo_db.connect()
    print("✅ MongoDB initialized")