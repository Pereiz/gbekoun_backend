-- =============================================================
-- Compte PostgreSQL de l'application (droits limités)
--
-- L'API se connecte avec ce compte : elle peut lire et modifier les LIGNES
-- du schéma gbekoun, mais pas créer/modifier/supprimer de tables, ni toucher
-- aux autres bases, ni créer de comptes. Le compte propriétaire (POSTGRES_USER)
-- reste réservé au schéma, aux migrations et aux sauvegardes.
--
-- À exécuter par le propriétaire du schéma, avec deux variables psql :
--   psql -v app_user=gbekoun_app -v app_password=... -f app_user.sql
-- Idempotent : rejouable (met à jour le mot de passe et les droits).
-- =============================================================
\set ON_ERROR_STOP on

SELECT format('CREATE ROLE %I LOGIN', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
\gexec

SELECT format(
    'ALTER ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
    :'app_user', :'app_password'
)
\gexec

SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'app_user')
\gexec

GRANT USAGE ON SCHEMA gbekoun TO :"app_user";
REVOKE CREATE ON SCHEMA gbekoun FROM :"app_user";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA gbekoun TO :"app_user";
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA gbekoun TO :"app_user";
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA gbekoun TO :"app_user";

-- Tables créées plus tard par le propriétaire (nouvelles migrations) : mêmes droits.
ALTER DEFAULT PRIVILEGES IN SCHEMA gbekoun
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA gbekoun
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO :"app_user";

-- Pas de création d'objets dans le schéma public non plus.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

SELECT format('Compte applicatif %I prêt (droits limités au schéma gbekoun).', :'app_user') AS status;
