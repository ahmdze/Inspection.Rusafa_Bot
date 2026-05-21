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
            status TEXT DEFAULT 'مفتوحة'
        )
    ''')

    # 2. جدول أعضاء الفريق الموزعين على الزيارات
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Visit_Members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_id INTEGER,
            user_id INTEGER,
            user_name TEXT,
            FOREIGN KEY (visit_id) REFERENCES Visits (id)
        )
    ''')

    # 3. جدول تقارير الأعضاء (المحاور، الأقسام، الملاحظات، التوصيات)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_id INTEGER,
            user_id INTEGER,
            axis_name TEXT,
            section_name TEXT,
            notes TEXT,
            recommendations TEXT,
            FOREIGN KEY (visit_id) REFERENCES Visits (id)
        )
    ''')

    conn.commit()
    conn.close()
    print("تم إنشاء الجداول بنجاح.")

if __name__ == '__main__':
    init_db()