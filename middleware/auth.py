from functools import wraps
from flask import request, jsonify
from app.utils.jwt_utils import verify_token

def require_auth(f):
    """Décorateur pour routes protégées"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        
        if not token:
            return jsonify({'error': 'Token manquant'}), 401
        
        user_id = verify_token(token)
        if not user_id:
            return jsonify({'error': 'Token invalide ou expiré'}), 401
        
        return f(user_id, *args, **kwargs)
    
    return decorated