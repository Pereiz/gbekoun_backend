"""Accès à PostgreSQL (pool de connexions) et à MongoDB."""
import threading
from contextlib import contextmanager

from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool
from pymongo import MongoClient

from core.config import Config

# -------------------------------
# PostgreSQL
# -------------------------------
# Le pool est créé à la première requête : l'import du module ne nécessite donc
# pas de base démarrée (utile pour les tests et pour un démarrage plus robuste).
_pg_pool = None
_pg_pool_lock = threading.Lock()


def _get_pool():
    global _pg_pool
    if _pg_pool is None:
        with _pg_pool_lock:
            if _pg_pool is None:
                _pg_pool = ThreadedConnectionPool(
                    minconn=1,
                    maxconn=Config.PG_POOL_MAX,
                    host=Config.PG_HOST,
                    port=Config.PG_PORT,
                    dbname=Config.PG_DATABASE,
                    user=Config.PG_USER,
                    password=Config.PG_PASSWORD,
                )
    return _pg_pool


@contextmanager
def transaction():
    """Ouvre une transaction : commit si tout passe, rollback sinon.

    Toujours rendre la connexion au pool dans un état propre. Sans rollback, une
    connexion en erreur restait « transaction aborted » et faisait échouer toutes
    les requêtes suivantes qui la réutilisaient.
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        pool.putconn(conn, close=bool(conn.closed))


def execute_query(query, params=None, fetch_one=False, fetch_all=False):
    """Exécute une requête dans sa propre transaction."""
    with transaction() as cur:
        cur.execute(query, params)
        if fetch_one:
            return cur.fetchone()
        if fetch_all:
            return cur.fetchall()
        return None


# -------------------------------
# MongoDB (la connexion réelle est ouverte à la première opération)
# -------------------------------
# Délais bornés : sans eux, une base MongoDB qui gèle (coupure réseau) bloque
# les threads du serveur indéfiniment (socketTimeoutMS est illimité par défaut).
mongo_client = MongoClient(
    Config.MONGO_URI,
    serverSelectionTimeoutMS=5000,   # serveur introuvable (défaut : 30 s)
    connectTimeoutMS=5000,           # ouverture de connexion (défaut : 20 s)
    socketTimeoutMS=15000,           # réponse à une opération (défaut : illimité)
)
mongo_db = mongo_client[Config.MONGO_DB]
messages_col = mongo_db['messages']


_health_client = None


def mongo_health_client():
    """Client séparé, très court, pour /api/health : répond en 3 s même si MongoDB gèle."""
    global _health_client
    if _health_client is None:
        _health_client = MongoClient(
            Config.MONGO_URI, serverSelectionTimeoutMS=3000, connectTimeoutMS=3000,
            socketTimeoutMS=3000, maxPoolSize=1,
        )
    return _health_client
