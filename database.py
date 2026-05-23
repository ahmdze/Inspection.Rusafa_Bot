"""
وحدة إدارة قاعدة البيانات المحسنة
تدعم SQLite و PostgreSQL مع فهارس وتهجيرات رسمية وتنظيف تلقائي
"""
import os
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

import sqlite3

DATABASE_URL = os.getenv('DATABASE_URL')
DB_PATH = 'inspection_db.sqlite'

if DATABASE_URL:
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        USE_POSTGRES = True
        logger.info("✅ Using PostgreSQL database")
    except ImportError:
        logger.error("❌ psycopg2 not installed.")
        USE_POSTGRES = False
else:
    USE_POSTGRES = False
    logger.info("✅ Using SQLite database")


@contextmanager
def get_connection():
    conn = None
    try:
        if USE_POSTGRES and DATABASE_URL:
            conn = psycopg2.connect(DATABASE_URL)
            yield conn
            conn.commit()
        else:
            conn = sqlite3.connect(DB_PATH, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
    except Exception as e:
        logger.error(f"Database error: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()


def init_db():
    """إنشاء قاعدة البيانات مع الفهارس والهجرات الرسمية"""
    with get_connection() as conn:
        cursor = conn.cursor()

        if USE_POSTGRES:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Visits (
                    id SERIAL PRIMARY KEY,
                    institution_name TEXT NOT NULL,
                    visit_date TEXT,
                    visit_type TEXT DEFAULT 'تفتيشية',
                    manager_id BIGINT,
                    leader_id BIGINT,
                    status TEXT DEFAULT 'مفتوحة',
                    scheduled_date TEXT DEFAULT NULL,
                    reminder_sent INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    closed_at TIMESTAMP DEFAULT NULL
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Visit_Members (
                    id SERIAL PRIMARY KEY,
                    visit_id INTEGER NOT NULL,
                    user_id BIGINT NOT NULL,
                    user_name TEXT,
                    full_name TEXT,
                    job_title TEXT,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (visit_id) REFERENCES Visits (id) ON DELETE CASCADE
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Reports (
                    id SERIAL PRIMARY KEY,
                    visit_id INTEGER NOT NULL,
                    user_id BIGINT NOT NULL,
                    axis_name TEXT NOT NULL,
                    section_name TEXT NOT NULL,
                    notes TEXT,
                    rec_destination TEXT,
                    recommendations TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (visit_id) REFERENCES Visits (id) ON DELETE CASCADE
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Attachments (
                    id SERIAL PRIMARY KEY,
                    visit_id INTEGER NOT NULL,
                    user_id BIGINT NOT NULL,
                    user_name TEXT,
                    file_id TEXT NOT NULL,
                    file_type TEXT,
                    file_name TEXT,
                    caption TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (visit_id) REFERENCES Visits (id) ON DELETE CASCADE
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Drafts (
                    id SERIAL PRIMARY KEY,
                    visit_id INTEGER NOT NULL,
                    user_id BIGINT NOT NULL,
                    user_name TEXT,
                    state TEXT,
                    payload TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (visit_id) REFERENCES Visits (id) ON DELETE CASCADE,
                    UNIQUE(visit_id, user_id)
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Audit_Log (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    user_name TEXT,
                    action TEXT NOT NULL,
                    target_type TEXT,
                    target_id INTEGER,
                    details TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS User_Sessions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL UNIQUE,
                    first_name TEXT,
                    last_name TEXT,
                    username TEXT,
                    language_code TEXT,
                    is_bot INTEGER DEFAULT 0,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    consent_given INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
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
        else:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Visits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    institution_name TEXT NOT NULL,
                    visit_date TEXT,
                    visit_type TEXT DEFAULT 'تفتيشية',
                    manager_id BIGINT,
                    leader_id BIGINT,
                    status TEXT DEFAULT 'مفتوحة',
                    scheduled_date TEXT DEFAULT NULL,
                    reminder_sent INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    closed_at TIMESTAMP DEFAULT NULL
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Visit_Members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    visit_id INTEGER NOT NULL,
                    user_id BIGINT NOT NULL,
                    user_name TEXT,
                    full_name TEXT,
                    job_title TEXT,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (visit_id) REFERENCES Visits (id) ON DELETE CASCADE
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    visit_id INTEGER NOT NULL,
                    user_id BIGINT NOT NULL,
                    axis_name TEXT NOT NULL,
                    section_name TEXT NOT NULL,
                    notes TEXT,
                    rec_destination TEXT,
                    recommendations TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (visit_id) REFERENCES Visits (id) ON DELETE CASCADE
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    visit_id INTEGER NOT NULL,
                    user_id BIGINT NOT NULL,
                    user_name TEXT,
                    file_id TEXT NOT NULL,
                    file_type TEXT,
                    file_name TEXT,
                    caption TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (visit_id) REFERENCES Visits (id) ON DELETE CASCADE
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Drafts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    visit_id INTEGER NOT NULL,
                    user_id BIGINT NOT NULL,
                    user_name TEXT,
                    state TEXT,
                    payload TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (visit_id) REFERENCES Visits (id) ON DELETE CASCADE,
                    UNIQUE(visit_id, user_id)
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Audit_Log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id BIGINT,
                    user_name TEXT,
                    action TEXT NOT NULL,
                    target_type TEXT,
                    target_id INTEGER,
                    details TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS User_Sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id BIGINT NOT NULL UNIQUE,
                    first_name TEXT,
                    last_name TEXT,
                    username TEXT,
                    language_code TEXT,
                    is_bot INTEGER DEFAULT 0,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    consent_given INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
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
            cursor.execute(idx_sql)

        conn.commit()
        logger.info("✅ تم إنشاء قاعدة البيانات والفهارس بنجاح")

        # تشغيل الهجرات
        run_migrations(conn)


def run_migrations(conn):
    """تشغيل الهجرات الرسمية مع تسجيلها في جدول خاص"""
    cursor = conn.cursor()

    if USE_POSTGRES:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS Schema_Migrations (
                id SERIAL PRIMARY KEY,
                migration_name TEXT UNIQUE NOT NULL,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    else:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS Schema_Migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                migration_name TEXT UNIQUE NOT NULL,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

    conn.commit()

    # ✅ قائمة الهجرات - تم حذف fix_user_id_types_to_bigint لأنها كانت None وتسبب خطأ
    migrations = [
        ('add_rec_destination_to_reports',
         "ALTER TABLE Reports ADD COLUMN rec_destination TEXT"),
        ('add_scheduled_date_to_visits',
         "ALTER TABLE Visits ADD COLUMN scheduled_date TEXT DEFAULT NULL"),
        ('add_reminder_sent_to_visits',
         "ALTER TABLE Visits ADD COLUMN reminder_sent INTEGER DEFAULT 0"),
        ('add_closed_at_to_visits',
         "ALTER TABLE Visits ADD COLUMN closed_at TIMESTAMP"),
        ('add_created_at_to_visits',
         "ALTER TABLE Visits ADD COLUMN created_at TIMESTAMP"),
        ('add_visit_type_to_visits',
         "ALTER TABLE Visits ADD COLUMN visit_type TEXT DEFAULT 'تفتيشية'"),
        ('add_job_title_to_visit_members',
         "ALTER TABLE Visit_Members ADD COLUMN job_title TEXT"),
        ('add_full_name_to_visit_members',
         "ALTER TABLE Visit_Members ADD COLUMN full_name TEXT"),
    ]

    # هجرة خاصة بـ PostgreSQL فقط
    if USE_POSTGRES:
        migrations.append(
            ('remove_unique_constraint_visit_members',
             "ALTER TABLE Visit_Members DROP CONSTRAINT IF EXISTS visit_members_visit_id_user_id_key")
        )

    for migration_name, sql in migrations:
        # التحقق من تطبيق الهجرة مسبقاً
        try:
            if USE_POSTGRES:
                cursor.execute(
                    "SELECT id FROM Schema_Migrations WHERE migration_name = %s",
                    (migration_name,)
                )
            else:
                cursor.execute(
                    "SELECT id FROM Schema_Migrations WHERE migration_name = ?",
                    (migration_name,)
                )
            if cursor.fetchone():
                logger.warning(f"⚠️ Migration skipped (already exists): {migration_name}")
                continue
        except Exception as check_error:
            error_msg = str(check_error).lower()
            if 'does not exist' in error_msg or 'current transaction is aborted' in error_msg:
                conn.rollback()
            else:
                raise

        try:
            cursor.execute(sql)
            if USE_POSTGRES:
                cursor.execute(
                    "INSERT INTO Schema_Migrations (migration_name) VALUES (%s)",
                    (migration_name,)
                )
            else:
                cursor.execute(
                    "INSERT INTO Schema_Migrations (migration_name) VALUES (?)",
                    (migration_name,)
                )
            conn.commit()
            logger.info(f"✅ Migration applied: {migration_name}")
        except Exception as e:
            error_msg = str(e).lower()
            if USE_POSTGRES:
                conn.rollback()
            if "duplicate column" in error_msg or "already exists" in error_msg or "duplicate column name" in error_msg:
                logger.warning(f"⚠️ Migration skipped (already exists): {migration_name}")
                continue
            elif "duplicate key" in error_msg or "unique constraint" in error_msg:
                logger.warning(f"⚠️ Migration already recorded: {migration_name}")
                continue
            else:
                logger.error(f"Migration failed {migration_name}: {e}")
                raise

    conn.commit()


def cleanup_old_data(days=30):
    with get_connection() as conn:
        cursor = conn.cursor()
        cutoff_date = datetime.now() - timedelta(days=days)

        if USE_POSTGRES:
            cursor.execute('''
                DELETE FROM Drafts 
                WHERE visit_id IN (
                    SELECT id FROM Visits 
                    WHERE status = %s AND closed_at < %s
                )
            ''', ('مغلقة', cutoff_date.isoformat()))
        else:
            cursor.execute('''
                DELETE FROM Drafts 
                WHERE visit_id IN (
                    SELECT id FROM Visits 
                    WHERE status = 'مغلقة' AND closed_at < ?
                )
            ''', (cutoff_date.isoformat(),))

        deleted_drafts = cursor.rowcount

        if deleted_drafts > 0:
            if USE_POSTGRES:
                cursor.execute(
                    "INSERT INTO Audit_Log (action, details) VALUES (%s, %s)",
                    ('cleanup', f'Deleted {deleted_drafts} old drafts')
                )
            else:
                cursor.execute(
                    "INSERT INTO Audit_Log (action, details) VALUES (?, ?)",
                    ('cleanup', f'Deleted {deleted_drafts} old drafts')
                )
            conn.commit()
            logger.info(f"🧹 Cleaned up {deleted_drafts} old drafts")

        return deleted_drafts


def upsert_user_session(user_id, first_name, last_name, username, language_code, is_bot=False):
    with get_connection() as conn:
        cursor = conn.cursor()
        if USE_POSTGRES:
            cursor.execute('''
                INSERT INTO User_Sessions 
                (user_id, first_name, last_name, username, language_code, is_bot, last_seen, consent_given)
                VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s)
                ON CONFLICT(user_id) DO UPDATE SET
                    first_name = EXCLUDED.first_name,
                    last_name = EXCLUDED.last_name,
                    username = EXCLUDED.username,
                    language_code = EXCLUDED.language_code,
                    last_seen = CURRENT_TIMESTAMP
            ''', (user_id, first_name, last_name, username, language_code, 1 if is_bot else 0, 1))
        else:
            cursor.execute('''
                INSERT INTO User_Sessions 
                (user_id, first_name, last_name, username, language_code, is_bot, last_seen, consent_given)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 1)
                ON CONFLICT(user_id) DO UPDATE SET
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    username = excluded.username,
                    language_code = excluded.language_code,
                    last_seen = CURRENT_TIMESTAMP
            ''', (user_id, first_name, last_name, username, language_code, 1 if is_bot else 0))
        conn.commit()


def delete_user_data(user_id):
    with get_connection() as conn:
        cursor = conn.cursor()
        if USE_POSTGRES:
            cursor.execute("DELETE FROM Visit_Members WHERE user_id = %s", (user_id,))
            cursor.execute("DELETE FROM Drafts WHERE user_id = %s", (user_id,))
            cursor.execute('UPDATE Reports SET user_id = 0 WHERE user_id = %s', (user_id,))
            cursor.execute("UPDATE Attachments SET user_id = 0, user_name = 'Deleted User' WHERE user_id = %s", (user_id,))
            cursor.execute("UPDATE Audit_Log SET user_name = 'Deleted User' WHERE user_id = %s", (user_id,))
            cursor.execute("DELETE FROM User_Sessions WHERE user_id = %s", (user_id,))
        else:
            cursor.execute("DELETE FROM Visit_Members WHERE user_id = ?", (user_id,))
            cursor.execute("DELETE FROM Drafts WHERE user_id = ?", (user_id,))
            cursor.execute('UPDATE Reports SET user_id = 0 WHERE user_id = ?', (user_id,))
            cursor.execute("UPDATE Attachments SET user_id = 0, user_name = 'Deleted User' WHERE user_id = ?", (user_id,))
            cursor.execute("UPDATE Audit_Log SET user_name = 'Deleted User' WHERE user_id = ?", (user_id,))
            cursor.execute("DELETE FROM User_Sessions WHERE user_id = ?", (user_id,))
        conn.commit()
        logger.info(f"🗑️ Deleted data for user {user_id}")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("✅ Database initialized successfully with indexes and migrations.")
