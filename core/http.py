"""Format commun des réponses d'erreur de l'API.

    {"error": "Message lisible", "code": "CODE_MACHINE", ...champs optionnels}

Le champ `code` permet à l'application mobile de réagir sans analyser le texte
(ex. DEVICE_REVOKED → afficher l'écran d'activation).
"""
from flask import jsonify


class ApiError(Exception):
    """Erreur métier convertie en réponse JSON."""

    def __init__(self, code, message, http_status=400, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.extra = extra

    def to_response(self):
        return error_response(self.message, self.http_status, self.code, **self.extra)


def error_response(message, http_status, code=None, **extra):
    body = {'error': message}
    if code:
        body['code'] = code
    body.update(extra)
    return jsonify(body), http_status
