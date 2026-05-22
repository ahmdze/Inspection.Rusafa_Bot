"""
وحدة إدارة قاعدة البيانات المحسنة
تدعم SQLite للتطوير المحلي و PostgreSQL للإنتاج (Railway)
"""
import os
import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# تحديد نوع قاعدة البيانات تلقائياً بناءً على متغير البيئة
DATABASE_URL = os.getenv('DATABASE_URL', '')
USE_POSTGRES = DATABASE_URL.startswith('postgresql://') if DATABASE_URL else False

if USE_POSTGRES:
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        DB_PATH = DATABASE_URL
        logger.info("✅ Using PostgreSQL database")
    except ImportError:
        logger.warning("psycopg2 not installed, falling back to SQLite")
        USE_POSTGRES = False
        DB_PATH = 'inspection_db.sqlite'
else:
    DB_PATH = 'inspection_db.sqlite'
    logger.info("✅ Using SQLite database")


@contextmanager
def get_connection():
    """إدارة اتصال قاعدة البيانات مع timeout محسّن"""
    conn = None
    try:
        if USE_POSTGRES:
            # اتصال PostgreSQL
            conn = psycopg2.connect(
                DATABASE_URL,
                sslmode='require' if 'railway' in DATABASE_URL else 'prefer'
            )
            yield conn
        else:
            # اتصال SQLite
            conn = sqlite3.connect(DB_PATH, timeout=30.0)
            conn.row_factory = sqlite3.Row
            # تفعيل المفاتيح الخارجية
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
    except (sqlite3.Error, psycopg2.Error) as e:
        logger.error(f"Database error: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            if USE_POSTGRES:
                conn.close()
            else:
                conn.close()


def init_db():
    """إنشاء قاعدة البيانات مع الفهارس والهجرات الرسمية"""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        if USE_POSTGRES:
            # PostgreSQL syntax (SERIAL instead of AUTOINCREMENT, different timestamp syntax)
            # 1. جدول الزيارات مع فهرسة
            cursor.execute('''
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
            ''')
            
            # 2. جدول أعضاء الفريق مع فهارس
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Visit_Members (
                    id SERIAL PRIMARY KEY,
                    visit_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    user_name TEXT,
                    full_name TEXT,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (visit_id) REFERENCES Visits (id) ON DELETE CASCADE,
                    UNIQUE(visit_id, user_id)
                )
            ''')
            
            # 3. جدول التقارير مع فهارس للبحث السريع
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Reports (
                    id SERIAL PRIMARY KEY,
                    visit_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    axis_name TEXT NOT NULL,
                    section_name TEXT NOT NULL,
                    notes TEXT,
                    rec_destination TEXT,
                    recommendations TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (visit_id) REFERENCES Visits (id) ON DELETE CASCADE
                )
            ''')
            
            # 4. جدول المرفقات مع فهارس
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Attachments (
                    id SERIAL PRIMARY KEY,
                    visit_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    user_name TEXT,
                    file_id TEXT NOT NULL,
                    file_type TEXT,
                    file_name TEXT,
                    caption TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (visit_id) REFERENCES Visits (id) ON DELETE CASCADE
                )
            ''')
            
            # 5. جدول المسودات مع تنظيف تلقائي
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Drafts (
                    id SERIAL PRIMARY KEY,
                    visit_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    user_name TEXT,
                    state TEXT,
                    payload TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (visit_id) REFERENCES Visits (id) ON DELETE CASCADE,
                    UNIQUE(visit_id, user_id)
                )
            ''')
            
            # 6. سجل التدقيق Audit Log مع فهارس
            cursor.execute('''
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
            ''')
            
            # 7. جدول جلسات المستخدمين للأمان والخصوصية
            cursor.execute('''
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
            ''')
            
            # إنشاء الفهارس لتحسين الأداء (PostgreSQL syntax)
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
            # SQLite syntax (AUTOINCREMENT)
            # 1. جدول الزيارات مع فهرسة
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Visits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            ''')
            
            # 2. جدول أعضاء الفريق مع فهارس
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Visit_Members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    visit_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    user_name TEXT,
                    full_name TEXT,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (visit_id) REFERENCES Visits (id) ON DELETE CASCADE,
                    UNIQUE(visit_id, user_id)
                )
            ''')
            
            # 3. جدول التقارير مع فهارس للبحث السريع
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    visit_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    axis_name TEXT NOT NULL,
                    section_name TEXT NOT NULL,
                    notes TEXT,
                    rec_destination TEXT,
                    recommendations TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (visit_id) REFERENCES Visits (id) ON DELETE CASCADE
                )
            ''')
            
            # 4. جدول المرفقات مع فهارس
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    visit_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    user_name TEXT,
                    file_id TEXT NOT NULL,
                    file_type TEXT,
                    file_name TEXT,
                    caption TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (visit_id) REFERENCES Visits (id) ON DELETE CASCADE
                )
            ''')
            
            # 5. جدول المسودات مع تنظيف تلقائي
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Drafts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    visit_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    user_name TEXT,
                    state TEXT,
                    payload TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (visit_id) REFERENCES Visits (id) ON DELETE CASCADE,
                    UNIQUE(visit_id, user_id)
                )
            ''')
            
            # 6. سجل التدقيق Audit Log مع فهارس
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Audit_Log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    user_name TEXT,
                    action TEXT NOT NULL,
                    target_type TEXT,
                    target_id INTEGER,
                    details TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 7. جدول جلسات المستخدمين للأمان والخصوصية
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS User_Sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            ''')
            
            # إنشاء الفهارس لتحسين الأداء (SQLite syntax)
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
    
    # جدول لتتبع الهجرات المطبقة
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
    
    # قائمة الهجرات
    migrations = [
        ('add_rec_destination_to_reports', 
         "ALTER TABLE Reports ADD COLUMN IF NOT EXISTS rec_destination TEXT"),
        ('add_scheduled_date_to_visits',
         "ALTER TABLE Visits ADD COLUMN IF NOT EXISTS scheduled_date TEXT DEFAULT NULL"),
        ('add_reminder_sent_to_visits',
         "ALTER TABLE Visits ADD COLUMN IF NOT EXISTS reminder_sent INTEGER DEFAULT 0"),
        ('add_closed_at_to_visits',
         "ALTER TABLE Visits ADD COLUMN IF NOT EXISTS closed_at TIMESTAMP"),
        ('add_created_at_to_visits',
         "ALTER TABLE Visits ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
        ('add_full_name_to_visit_members',
         "ALTER TABLE Visit_Members ADD COLUMN IF NOT EXISTS full_name TEXT"),
    ]
    
    for migration_name, sql in migrations:
        # التحقق مما إذا كانت الهجرة قد طُبقت مسبقاً
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
            logger.info(f"⏭️ Skipping migration (already applied): {migration_name}")
            continue
        
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
            if "duplicate column" in error_msg or "already exists" in error_msg:
                # العمود موجود بالفعل، نعتبر الهجرة مطبقة
                try:
                    if USE_POSTGRES:
                        cursor.execute(
                            "INSERT INTO Schema_Migrations (migration_name) VALUES (%s) ON CONFLICT DO NOTHING",
                            (migration_name,)
                        )
                    else:
                        cursor.execute(
                            "INSERT OR IGNORE INTO Schema_Migrations (migration_name) VALUES (?)",
                            (migration_name,)
                        )
                    conn.commit()
                    logger.info(f"⏭️ Migration skipped (column exists): {migration_name}")
                except Exception as insert_error:
                    logger.warning(f"Could not record migration {migration_name}: {insert_error}")
            else:
                logger.error(f"Migration failed {migration_name}: {e}")
                conn.rollback()
                raise
    
    conn.commit()


def cleanup_old_data(days=30):
    """تنظيف البيانات القديمة (مسودات وسجلات قديمة)"""
    with get_connection() as conn:
        cursor = conn.cursor()
        cutoff_date = datetime.now() - timedelta(days=days)
        
        # حذف المسودات القديمة للزيارات المغلقة
        cursor.execute('''
            DELETE FROM Drafts 
            WHERE visit_id IN (
                SELECT id FROM Visits 
                WHERE status = 'مغلقة' AND closed_at < ?
            )
        ''', (cutoff_date.isoformat(),))
        deleted_drafts = cursor.rowcount
        
        # حفظ سجل التنظيف
        if deleted_drafts > 0:
            cursor.execute('''
                INSERT INTO Audit_Log (action, details) VALUES (?, ?)
            ''', ('cleanup', f'Deleted {deleted_drafts} old drafts'))
            conn.commit()
            logger.info(f"🧹 Cleaned up {deleted_drafts} old drafts")
        
        return deleted_drafts


def upsert_user_session(user_id, first_name, last_name, username, language_code, is_bot=False):
    """تحديث أو إدخال جلسة المستخدم مع تسجيل الموافقة على الخصوصية"""
    with get_connection() as conn:
        cursor = conn.cursor()
        if USE_POSTGRES:
            cursor.execute('''
                INSERT INTO User_Sessions 
                (user_id, first_name, last_name, username, language_code, is_bot, last_seen, consent_given)
                VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, 1)
                ON CONFLICT(user_id) DO UPDATE SET
                    first_name = EXCLUDED.first_name,
                    last_name = EXCLUDED.last_name,
                    username = EXCLUDED.username,
                    language_code = EXCLUDED.language_code,
                    last_seen = CURRENT_TIMESTAMP
            ''', (user_id, first_name, last_name, username, language_code, 1 if is_bot else 0))
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


def update_member_full_name(visit_id, user_id, full_name):
    """تحديث الاسم الثلاثي للعضو في زيارة معينة"""
    with get_connection() as conn:
        cursor = conn.cursor()
        if USE_POSTGRES:
            cursor.execute(
                "UPDATE Visit_Members SET full_name = %s WHERE visit_id = %s AND user_id = %s",
                (full_name, visit_id, user_id)
            )
        else:
            cursor.execute(
                "UPDATE Visit_Members SET full_name = ? WHERE visit_id = ? AND user_id = ?",
                (full_name, visit_id, user_id)
            )
        conn.commit()
        return cursor.rowcount > 0


def delete_visit(visit_id):
    """حذف زيارة وجميع بياناتها المرتبطة من قاعدة البيانات"""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        if USE_POSTGRES:
            # حذف التقارير المرتبطة
            cursor.execute("DELETE FROM Reports WHERE visit_id = %s", (visit_id,))
            # حذف أعضاء الفريق
            cursor.execute("DELETE FROM Visit_Members WHERE visit_id = %s", (visit_id,))
            # حذف المرفقات
            cursor.execute("DELETE FROM Attachments WHERE visit_id = %s", (visit_id,))
            # حذف المسودات
            cursor.execute("DELETE FROM Drafts WHERE visit_id = %s", (visit_id,))
            # حذف الزيارة نفسها
            cursor.execute("DELETE FROM Visits WHERE id = %s", (visit_id,))
        else:
            # SQLite
            cursor.execute("DELETE FROM Reports WHERE visit_id = ?", (visit_id,))
            cursor.execute("DELETE FROM Visit_Members WHERE visit_id = ?", (visit_id,))
            cursor.execute("DELETE FROM Attachments WHERE visit_id = ?", (visit_id,))
            cursor.execute("DELETE FROM Drafts WHERE visit_id = ?", (visit_id,))
            cursor.execute("DELETE FROM Visits WHERE id = ?", (visit_id,))
        
        deleted_count = cursor.rowcount
        conn.commit()
        logger.info(f"🗑️ Deleted visit {visit_id} and all associated data")
        return deleted_count > 0


def delete_user_data(user_id):
    """حذف بيانات المستخدم استجابة لطلب الخصوصية - Right to be forgotten"""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # حذف من Visit_Members
        if USE_POSTGRES:
            cursor.execute("DELETE FROM Visit_Members WHERE user_id = %s", (user_id,))
            
            # حذف من Drafts
            cursor.execute("DELETE FROM Drafts WHERE user_id = %s", (user_id,))
            
            # تحديث السجلات الأخرى لإخفاء الهوية (بدلاً من الحذف الكامل للحفاظ على النزاهة)
            cursor.execute('''
                UPDATE Reports SET user_id = 0 WHERE user_id = %s
            ''', (user_id,))
            
            cursor.execute('''
                UPDATE Attachments SET user_id = 0, user_name = 'Deleted User' WHERE user_id = %s
            ''', (user_id,))
            
            cursor.execute('''
                UPDATE Audit_Log SET user_name = 'Deleted User' WHERE user_id = %s
            ''', (user_id,))
            
            # حذف من User_Sessions
            cursor.execute("DELETE FROM User_Sessions WHERE user_id = %s", (user_id,))
        else:
            # حذف من Visit_Members
            cursor.execute("DELETE FROM Visit_Members WHERE user_id = ?", (user_id,))
            
            # حذف من Drafts
            cursor.execute("DELETE FROM Drafts WHERE user_id = ?", (user_id,))
            
            # تحديث السجلات الأخرى لإخفاء الهوية (بدلاً من الحذف الكامل للحفاظ على النزاهة)
            cursor.execute('''
                UPDATE Reports SET user_id = 0 WHERE user_id = ?
            ''', (user_id,))
            
            cursor.execute('''
                UPDATE Attachments SET user_id = 0, user_name = 'Deleted User' WHERE user_id = ?
            ''', (user_id,))
            
            cursor.execute('''
                UPDATE Audit_Log SET user_name = 'Deleted User' WHERE user_id = ?
            ''', (user_id,))
            
            # حذف من User_Sessions
            cursor.execute("DELETE FROM User_Sessions WHERE user_id = ?", (user_id,))
        
        conn.commit()
        logger.info(f"🗑️ Deleted data for user {user_id}")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("✅ Database initialized successfully with indexes and migrations.")