"""
Database Module - PostgreSQL with Connection Pooling
=====================================================
Supports both PostgreSQL (production) and SQLite (development/testing).
Features:
- Connection pooling for PostgreSQL
- Async operations
- Automatic migration
- Health checks
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional, Any, List, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class DatabasePool:
    """
    Database connection pool manager.
    
    Automatically detects database type from DATABASE_URL environment variable.
    Uses psycopg_pool for PostgreSQL connection pooling.
    Falls back to SQLite for development.
    """
    
    def __init__(self, database_url: str = None):
        self.database_url = database_url or os.getenv('DATABASE_URL', '')
        self._pool = None
        self._is_postgresql = self.database_url.startswith('postgresql://')
        self._sqlite_path = 'inspection_db.sqlite' if not self._is_postgresql else None
    
    async def create_pool(self, min_size: int = 2, max_size: int = 10) -> bool:
        """Create connection pool"""
        try:
            if self._is_postgresql:
                from psycopg_pool import AsyncConnectionPool
                
                self._pool = AsyncConnectionPool(
                    conninfo=self.database_url,
                    min_size=min_size,
                    max_size=max_size,
                    open=False,
                    kwargs={"autocommit": False}
                )
                await self._pool.open()
                logger.info(f"✅ PostgreSQL pool created (min={min_size}, max={max_size})")
                return True
            else:
                logger.info("Using SQLite (no pooling needed)")
                return True
        except Exception as e:
            logger.error(f"Failed to create database pool: {e}")
            return False
    
    async def close_pool(self):
        """Close connection pool"""
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("Database pool closed")
    
    @asynccontextmanager
    async def get_connection(self):
        """Get connection from pool"""
        if self._is_postgresql:
            if not self._pool:
                raise RuntimeError("Database pool not initialized")
            
            async with self._pool.connection() as conn:
                yield conn
        else:
            # SQLite fallback
            import sqlite3
            conn = sqlite3.connect(self._sqlite_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                yield conn
            finally:
                conn.close()
    
    async def execute_query(self, query: str, params: tuple = (), 
                           fetch: bool = False, fetch_one: bool = False) -> Any:
        """Execute a database query"""
        async with self.get_connection() as conn:
            if self._is_postgresql:
                async with conn.cursor() as cursor:
                    await cursor.execute(query, params)
                    
                    if fetch:
                        return await cursor.fetchall()
                    elif fetch_one:
                        return await cursor.fetchone()
                    
                    await conn.commit()
            else:
                # SQLite
                cursor = conn.cursor()
                cursor.execute(query, params)
                
                if fetch:
                    result = cursor.fetchall()
                    conn.close()
                    return result
                elif fetch_one:
                    result = cursor.fetchone()
                    conn.close()
                    return result
                
                conn.commit()
                conn.close()
    
    async def health_check(self) -> dict:
        """Check database health"""
        status = {
            'connected': False,
            'type': 'postgresql' if self._is_postgresql else 'sqlite',
            'latency_ms': None,
            'error': None
        }
        
        try:
            start = datetime.utcnow()
            await self.execute_query("SELECT 1")
            end = datetime.utcnow()
            status['connected'] = True
            status['latency_ms'] = (end - start).total_seconds() * 1000
        except Exception as e:
            status['error'] = str(e)
        
        return status
    
    def is_postgresql(self) -> bool:
        """Check if using PostgreSQL"""
        return self._is_postgresql


# Global pool instance
_db_pool: Optional[DatabasePool] = None


def get_db_pool() -> DatabasePool:
    """Get database pool instance"""
    global _db_pool
    if _db_pool is None:
        _db_pool = DatabasePool()
    return _db_pool


async def init_database(min_size: int = 2, max_size: int = 10) -> DatabasePool:
    """Initialize database pool"""
    pool = get_db_pool()
    await pool.create_pool(min_size, max_size)
    await run_migrations(pool)
    return pool


async def run_migrations(pool: DatabasePool):
    """Run database migrations"""
    logger.info("Running database migrations...")
    
    # Create tables
    await pool.execute_query("""
        CREATE TABLE IF NOT EXISTS Visits (
            id SERIAL PRIMARY KEY,
            institution_name TEXT NOT NULL,
            visit_date TEXT,
            manager_id INTEGER,
            leader_id INTEGER,
            status TEXT DEFAULT 'مفتوحة',
            scheduled_date TEXT DEFAULT NULL,
            reminder_sent INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            closed_at TIMESTAMP DEFAULT NULL
        )
    """)
    
    await pool.execute_query("""
        CREATE TABLE IF NOT EXISTS Visit_Members (
            id SERIAL PRIMARY KEY,
            visit_id INTEGER NOT NULL REFERENCES Visits(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL,
            user_name TEXT,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(visit_id, user_id)
        )
    """)
    
    await pool.execute_query("""
        CREATE TABLE IF NOT EXISTS Reports (
            id SERIAL PRIMARY KEY,
            visit_id INTEGER NOT NULL REFERENCES Visits(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL,
            axis_name TEXT NOT NULL,
            section_name TEXT NOT NULL,
            notes TEXT,
            rec_destination TEXT,
            recommendations TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    await pool.execute_query("""
        CREATE TABLE IF NOT EXISTS Attachments (
            id SERIAL PRIMARY KEY,
            visit_id INTEGER NOT NULL REFERENCES Visits(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL,
            user_name TEXT,
            file_id TEXT NOT NULL,
            file_type TEXT,
            file_name TEXT,
            caption TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    await pool.execute_query("""
        CREATE TABLE IF NOT EXISTS Drafts (
            id SERIAL PRIMARY KEY,
            visit_id INTEGER NOT NULL REFERENCES Visits(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL,
            user_name TEXT,
            state TEXT,
            payload JSONB,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(visit_id, user_id)
        )
    """)
    
    await pool.execute_query("""
        CREATE TABLE IF NOT EXISTS Audit_Log (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            user_name TEXT,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id INTEGER,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    await pool.execute_query("""
        CREATE TABLE IF NOT EXISTS User_Sessions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL UNIQUE,
            first_name TEXT,
            last_name TEXT,
            username TEXT,
            language_code TEXT,
            is_bot INTEGER DEFAULT 0,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            consent_given INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    await pool.execute_query("""
        CREATE TABLE IF NOT EXISTS Schema_Migrations (
            id SERIAL PRIMARY KEY,
            migration_name TEXT UNIQUE NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Create indexes
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_visits_status ON Visits(status)",
        "CREATE INDEX IF NOT EXISTS idx_visits_date ON Visits(visit_date)",
        "CREATE INDEX IF NOT EXISTS idx_visits_scheduled ON Visits(scheduled_date, reminder_sent)",
        "CREATE INDEX IF NOT EXISTS idx_members_visit ON Visit_Members(visit_id)",
        "CREATE INDEX IF NOT EXISTS idx_members_user ON Visit_Members(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_reports_visit ON Reports(visit_id)",
        "CREATE INDEX IF NOT EXISTS idx_reports_user ON Reports(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_reports_axis ON Reports(axis_name)",
        "CREATE INDEX IF NOT EXISTS idx_attachments_visit ON Attachments(visit_id)",
        "CREATE INDEX IF NOT EXISTS idx_attachments_user ON Attachments(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_drafts_visit_user ON Drafts(visit_id, user_id)",
        "CREATE INDEX IF NOT EXISTS idx_audit_user ON Audit_Log(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_audit_action ON Audit_Log(action)",
        "CREATE INDEX IF NOT EXISTS idx_audit_created ON Audit_Log(created_at)",
    ]
    
    for idx_sql in indexes:
        try:
            await pool.execute_query(idx_sql)
        except Exception as e:
            logger.warning(f"Index creation warning: {e}")
    
    # Record migrations
    migrations = [
        'add_rec_destination_to_reports',
        'add_scheduled_date_to_visits',
        'add_reminder_sent_to_visits',
        'add_closed_at_to_visits',
        'add_created_at_to_visits',
    ]
    
    for migration in migrations:
        try:
            await pool.execute_query(
                "INSERT INTO Schema_Migrations (migration_name) VALUES (%s) ON CONFLICT (migration_name) DO NOTHING",
                (migration,)
            )
        except Exception:
            pass  # Migration already exists or not applicable
    
    logger.info("✅ Database migrations completed")


# Convenience functions for backward compatibility
async def get_connection():
    """Get database connection (backward compatibility)"""
    pool = get_db_pool()
    return pool.get_connection()


async def execute_query(query, params=(), fetch=False):
    """Execute query (backward compatibility)"""
    pool = get_db_pool()
    return await pool.execute_query(query, params, fetch)


async def cleanup_old_data(days: int = 30) -> int:
    """Clean up old data"""
    pool = get_db_pool()
    
    # Delete old drafts for closed visits
    result = await pool.execute_query("""
        DELETE FROM Drafts 
        WHERE visit_id IN (
            SELECT id FROM Visits 
            WHERE status = 'مغلقة' AND closed_at < NOW() - INTERVAL '%s days'
        )
    """, (str(days),), fetch=True)
    
    deleted = len(result) if result else 0
    
    if deleted > 0:
        await pool.execute_query(
            "INSERT INTO Audit_Log (action, details) VALUES (%s, %s)",
            ('cleanup', f'Deleted {deleted} old drafts')
        )
        logger.info(f"🧹 Cleaned up {deleted} old drafts")
    
    return deleted


async def upsert_user_session(user_id: int, first_name: str, last_name: str,
                             username: str, language_code: str, is_bot: bool = False):
    """Upsert user session"""
    pool = get_db_pool()
    await pool.execute_query("""
        INSERT INTO User_Sessions 
        (user_id, first_name, last_name, username, language_code, is_bot, last_seen, consent_given)
        VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, 1)
        ON CONFLICT(user_id) DO UPDATE SET
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            username = EXCLUDED.username,
            language_code = EXCLUDED.language_code,
            last_seen = CURRENT_TIMESTAMP
    """, (user_id, first_name, last_name, username, language_code, 1 if is_bot else 0))


async def delete_user_data(user_id: int):
    """Delete user data (Right to be forgotten)"""
    pool = get_db_pool()
    
    await pool.execute_query("DELETE FROM Visit_Members WHERE user_id = %s", (user_id,))
    await pool.execute_query("DELETE FROM Drafts WHERE user_id = %s", (user_id,))
    await pool.execute_query("UPDATE Reports SET user_id = 0 WHERE user_id = %s", (user_id,))
    await pool.execute_query(
        "UPDATE Attachments SET user_id = 0, user_name = 'Deleted User' WHERE user_id = %s",
        (user_id,)
    )
    await pool.execute_query(
        "UPDATE Audit_Log SET user_name = 'Deleted User' WHERE user_id = %s",
        (user_id,)
    )
    await pool.execute_query("DELETE FROM User_Sessions WHERE user_id = %s", (user_id,))
    
    logger.info(f"🗑️ Deleted data for user {user_id}")
