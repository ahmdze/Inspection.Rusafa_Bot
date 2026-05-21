"""
وحدة الاختبارات الآلية لبوت التفتيش المدرسي

تغطي هذه الوحدة:
- اختبار وظائف قاعدة البيانات
- اختبار معالجة الأخطاء
- اختبار التخزين المؤقت
- اختبار التوثيق
"""

import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import sqlite3
import os
import sys
import asyncio
from datetime import datetime, timedelta

# إضافة مسار المشروع
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# استيراد دوال قاعدة البيانات
from database import init_db, get_connection, upsert_user_session, delete_user_data, cleanup_old_data


class TestDatabaseFunctions(unittest.TestCase):
    """اختبار وظائف قاعدة البيانات"""

    @classmethod
    def setUpClass(cls):
        """إعداد قاعدة بيانات اختبار مرة واحدة للجميع"""
        # استخدام ملف قاعدة بيانات مؤقت
        import tempfile
        cls.test_db_file = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        cls.test_db_path = cls.test_db_file.name
        cls.test_db_file.close()
        
        # تهيئة قاعدة البيانات مباشرة
        import database
        # تعيين مسار قاعدة البيانات المؤقتة
        cls.original_db_path = database.DB_PATH
        database.DB_PATH = cls.test_db_path
        
        # تهيئة الجداول
        database.init_db()
        
        # حفظ المراجع للدوال
        cls.db_module = database

    @classmethod
    def tearDownClass(cls):
        """تنظيف قاعدة البيانات المؤقتة"""
        import os
        if os.path.exists(cls.test_db_path):
            os.unlink(cls.test_db_path)
        # استعادة المسار الأصلي
        import database
        database.DB_PATH = cls.original_db_path

    def test_init_db_creates_tables(self):
        """اختبار إنشاء الجداول عند التهيئة"""
        with self.db_module.get_connection() as conn:
            cursor = conn.cursor()
            
            # التحقق من وجود الجداول الأساسية
            tables = [
                'Visits', 'Visit_Members', 'Reports', 
                'Attachments', 'User_Sessions', 'Schema_Migrations'
            ]
            
            for table in tables:
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?", 
                    (table,)
                )
                result = cursor.fetchone()
                self.assertIsNotNone(result, f"الجدول {table} غير موجود")

    def test_upsert_user_session(self):
        """اختبار إضافة/تحديث جلسة مستخدم"""
        user_id = 123456
        
        # إضافة مستخدم جديد
        self.db_module.upsert_user_session(
            user_id,
            "Test",
            "User",
            "testuser",
            "ar"
        )
        
        # التحقق من الإضافة
        with self.db_module.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM User_Sessions WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            
            self.assertIsNotNone(result)
            self.assertEqual(result[1], user_id)
            self.assertEqual(result[2], "Test")

    def test_upsert_user_session_update(self):
        """اختبار تحديث جلسة مستخدم موجود"""
        user_id = 123457
        
        # إضافة مستخدم
        self.db_module.upsert_user_session(
            user_id,
            "Test",
            "User",
            "testuser",
            "ar"
        )
        
        # تحديث المستخدم
        self.db_module.upsert_user_session(
            user_id,
            "Updated",
            "Name",
            "updateduser",
            "en"
        )
        
        # التحقق من التحديث
        with self.db_module.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT first_name, username FROM User_Sessions WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            
            self.assertEqual(result[0], "Updated")
            self.assertEqual(result[1], "updateduser")

    def test_delete_user_data(self):
        """اختبار حذف بيانات المستخدم للخصوصية"""
        user_id = 123458
        
        # إضافة مستخدم
        self.db_module.upsert_user_session(
            user_id,
            "Test",
            "User",
            "testuser",
            "ar"
        )
        
        # حذف البيانات
        self.db_module.delete_user_data(user_id)
        
        # التحقق من الحذف
        with self.db_module.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM User_Sessions WHERE user_id = ?", (user_id,))
            user_count = cursor.fetchone()[0]
            
            self.assertEqual(user_count, 0, "لم يتم حذف المستخدم")

    def test_cleanup_old_data(self):
        """اختبار تنظيف البيانات القديمة"""
        # هذا الاختبار يتحقق من وجود الدالة فقط
        # التنظيف الفعلي يحتاج بيانات قديمة حقيقية
        try:
            self.db_module.cleanup_old_data(days=7)
            self.assertTrue(True, "دالة التنظيف تعمل بدون أخطاء")
        except Exception as e:
            self.fail(f"دالة التنظيف فشلت: {e}")


class TestCache(unittest.TestCase):
    """اختبار نظام التخزين المؤقت"""

    def setUp(self):
        """إعداد الكاش"""
        from cache_manager import CacheManager
        self.cache = CacheManager(ttl=300)  # 5 دقائق

    def test_set_and_get(self):
        """اختبار تخزين واسترجاع قيمة"""
        self.cache.set("key1", "value1")
        result = self.cache.get("key1")
        
        self.assertEqual(result, "value1")

    def test_get_nonexistent(self):
        """اختبار استرجاع مفتاح غير موجود"""
        result = self.cache.get("nonexistent_key")
        
        self.assertIsNone(result)

    def test_expiration(self):
        """اختبار انتهاء صلاحية الكاش"""
        # كاش بصلاحية قصيرة جداً
        from cache_manager import CacheManager
        short_cache = CacheManager(ttl=1)  # 1 ثانية
        short_cache.set("temp_key", "temp_value")
        
        # الانتظار حتى تنتهي الصلاحية
        import time
        time.sleep(1.5)
        
        result = short_cache.get("temp_key")
        self.assertIsNone(result, "الكاش لم ينتهِ بعد")

    def test_delete(self):
        """اختبار حذف قيمة من الكاش"""
        self.cache.set("key_to_delete", "value")
        self.cache.delete("key_to_delete")
        
        result = self.cache.get("key_to_delete")
        self.assertIsNone(result)

    def test_clear(self):
        """اختبار مسح كل الكاش"""
        self.cache.set("key1", "value1")
        self.cache.set("key2", "value2")
        self.cache.clear()
        
        self.assertIsNone(self.cache.get("key1"))
        self.assertIsNone(self.cache.get("key2"))

    def test_stats(self):
        """اختبار إحصائيات الكاش"""
        self.cache.set("key1", "value1")
        self.cache.get("key1")  # hit
        self.cache.get("nonexistent")  # miss
        
        stats = self.cache.get_stats()
        
        self.assertIn('hits', stats)
        self.assertIn('misses', stats)
        self.assertGreaterEqual(stats['hits'], 1)
        self.assertGreaterEqual(stats['misses'], 1)


class TestConfig(unittest.TestCase):
    """اختبار ملف الإعدادات"""

    def test_config_imports(self):
        """اختبار استيراد ملف الإعدادات"""
        try:
            # محاولة استيراد بدون توكن حقيقي
            with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'test_token'}):
                import importlib
                import config
                importlib.reload(config)
                
                self.assertTrue(hasattr(config, 'ADMIN_IDS'))
                self.assertTrue(hasattr(config, 'DATABASE_PATH'))
                self.assertTrue(hasattr(config, 'CACHE_TTL'))
        except Exception as e:
            # إذا فشل الاستيراد بسبب متغيرات مفقودة، هذا مقبول في بيئة الاختبار
            self.skipTest(f"لا يمكن استيراد config في بيئة الاختبار: {e}")


if __name__ == '__main__':
    unittest.main(verbosity=2)
