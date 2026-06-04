import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool
from contextlib import contextmanager
from app.config import Config

# Pool de connexions
pg_pool = SimpleConnectionPool(
    minconn=1,
    maxconn=20,
    host=Config.PG_HOST,
    port=Config.PG_PORT,
    database=Config.PG_DATABASE,
    user=Config.PG_USER,
    password=Config.PG_PASSWORD
)

@contextmanager
def get_cursor():
    """Context manager pour obtenir un cursor PostgreSQL"""
    conn = pg_pool.getconn()
    try:
        yield conn.cursor(cursor_factory=RealDictCursor)
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        pg_pool.putconn(conn)

def execute_query(query, params=None):
    """Exécute une requête et retourne les résultats"""
    with get_cursor() as cursor:
        cursor.execute(query, params)
        if query.strip().upper().startswith(('SELECT', 'RETURNING')):
            return cursor.fetchall()
        return None

def init_postgres():
    """Initialise toutes les tables PostgreSQL"""
    
    # Table users
    execute_query("""
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            phone_number VARCHAR(20) UNIQUE NOT NULL,
            display_name VARCHAR(100),
            username VARCHAR(50) UNIQUE,
            bio TEXT,
            avatar_url TEXT,
            is_online BOOLEAN DEFAULT FALSE,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Table invite_codes (générés par admin)
    execute_query("""
        CREATE TABLE IF NOT EXISTS invite_codes (
            id SERIAL PRIMARY KEY,
            code VARCHAR(10) UNIQUE NOT NULL,
            used_by UUID NULL REFERENCES users(id) ON DELETE SET NULL,
            used_at TIMESTAMP NULL,
            created_by UUID NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL
        )
    """)
    
    # Table conversations
    execute_query("""
        CREATE TABLE IF NOT EXISTS conversations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            type VARCHAR(10) NOT NULL CHECK (type IN ('direct', 'group')),
            name VARCHAR(100),
            avatar_url TEXT,
            created_by UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Table conversation_participants
    execute_query("""
        CREATE TABLE IF NOT EXISTS conversation_participants (
            conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
            user_id UUID REFERENCES users(id) ON DELETE CASCADE,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            unread_count INTEGER DEFAULT 0,
            last_read_message_id VARCHAR(24),
            is_admin BOOLEAN DEFAULT FALSE,
            muted_until TIMESTAMP NULL,
            PRIMARY KEY (conversation_id, user_id)
        )
    """)
    
    # Table blocked_users
    execute_query("""
        CREATE TABLE IF NOT EXISTS blocked_users (
            blocker_id UUID REFERENCES users(id) ON DELETE CASCADE,
            blocked_id UUID REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (blocker_id, blocked_id)
        )
    """)
    
    # Index pour performances
    execute_query("CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone_number)")
    execute_query("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
    execute_query("CREATE INDEX IF NOT EXISTS idx_invite_codes_code ON invite_codes(code)")
    execute_query("CREATE INDEX IF NOT EXISTS idx_invite_codes_expires ON invite_codes(expires_at)")
    execute_query("CREATE INDEX IF NOT EXISTS idx_participants_user ON conversation_participants(user_id)")
    execute_query("CREATE INDEX IF NOT EXISTS idx_participants_conversation ON conversation_participants(conversation_id)")
    
    print("✅ PostgreSQL initialized with all tables")