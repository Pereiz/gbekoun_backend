"""Crée (ou promeut) un administrateur et lui donne un code d'activation d'appareil.

Nécessaire au tout premier démarrage : l'inscription exige un code
d'invitation, et seul un admin peut en générer. Sert aussi de secours si
l'administrateur a perdu son téléphone (relancer le script = nouveau code).

Usage (depuis la racine du projet, .env configuré) :
    python -m scripts.create_admin --phone +22990000000 --name "Admin GBEKOUN"

Le code affiché s'utilise dans l'application (écran « Code d'activation »),
ou via POST /api/auth/device/activate. Il est valable 24 h, usage unique.
"""
import argparse
import sys

from core.config import Config
from core.database import transaction
from services.devices import BOOTSTRAP_CODE_TTL, create_activation_code, format_activation_code


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--phone', required=True, help='Numéro au format international, ex. +22990000000')
    parser.add_argument('--name', default='Administrateur', help="Nom affiché")
    args = parser.parse_args()

    if not args.phone.startswith('+'):
        sys.exit('Le numéro doit être au format international (+229...).')

    # Affiché pour éviter de créer l'admin dans la mauvaise base par erreur.
    print(f"Base : {Config.PG_USER}@{Config.PG_HOST}:{Config.PG_PORT}/{Config.PG_DATABASE}")

    with transaction() as cur:
        cur.execute("""
            INSERT INTO gbekoun.users (phone_number, display_name, username, is_admin)
            VALUES (%s, %s, %s, TRUE)
            ON CONFLICT (phone_number) DO UPDATE SET is_admin = TRUE
            RETURNING id, (xmax = 0) AS created
        """, (args.phone, args.name, args.phone))
        user = cur.fetchone()
        code, expires_at = create_activation_code(cur, user['id'], None, BOOTSTRAP_CODE_TTL)

    action = 'créé' if user['created'] else 'promu administrateur'
    print(f"Compte {action} : {args.phone} (id {user['id']})")
    print(f"Code d'activation de l'appareil : {format_activation_code(code)}  "
          f"(valable jusqu'à {expires_at:%d/%m/%Y %H:%M} UTC, usage unique)")


if __name__ == '__main__':
    main()
