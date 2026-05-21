import sqlite3

def init_db():
    conn = sqlite3.connect('inspection_db.sqlite')
    cursor = conn.cursor()

    # 1. جدول الزيارات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            institution_name TEXT,
            visit_date TEXT,
            manager_id INTEGER,
            leader_id INTEGER,
            status TEXT DEFAULT 'مفتوحة',
            scheduled_date TEXT DEFAULT NULL
        )
    ''')

    # 2. جدول أعضاء الفريق
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Visit_Members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_id INTEGER,
            user_id INTEGER,
            user_name TEXT,
            FOREIGN KEY (visit_id) REFERENCES Visits (id)
        )
    ''')

    # 3. جدول التقارير مع حقل المرفقات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_id INTEGER,
            user_id INTEGER,
            axis_name TEXT,
            section_name TEXT,
            notes TEXT,
            rec_destination TEXT,
            recommendations TEXT,
            FOREIGN KEY (visit_id) REFERENCES Visits (id)
        )
    ''')

    # 4. جدول المرفقات (صور وملفات)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_id INTEGER,
            user_id INTEGER,
            user_name TEXT,
            file_id TEXT,
            file_type TEXT,
            file_name TEXT,
            caption TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (visit_id) REFERENCES Visits (id)
        )
    ''')

    # ترقية الجداول الموجودة إن احتاجت
    migrations = [
        "ALTER TABLE Reports ADD COLUMN rec_destination TEXT",
        "ALTER TABLE Visits ADD COLUMN scheduled_date TEXT DEFAULT NULL",
    ]
    for sql in migrations:
        try:
            cursor.execute(sql)
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()
    print("✅ تم إنشاء/تحديث قاعدة البيانات بنجاح.")

if __name__ == '__main__':
    init_db()