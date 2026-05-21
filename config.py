"""
إعدادات بوت التفتيش المدرسي

يحتوي هذا الملف على جميع الإعدادات والثوابت المستخدمة في البوت.
"""

import os
from dotenv import load_dotenv
import hashlib

# تحميل المتغيرات البيئية
load_dotenv()

# =============================================================================
# إعدادات Telegram
# =============================================================================

# توكن البوت (من BotFather)
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')

# معرفات المسؤولين (مفصولة بفواصل)
ADMIN_IDS_RAW = os.getenv('ADMIN_IDS', '')
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(',') if x.strip()]

# تجزئة معرفات المسؤولين للأمان
ADMIN_IDS_HASHED = [hashlib.sha256(str(admin_id).encode()).hexdigest() for admin_id in ADMIN_IDS]

# =============================================================================
# إعدادات قاعدة البيانات
# =============================================================================

# مسار قاعدة البيانات
DATABASE_PATH = os.getenv('DATABASE_URL', 'inspection_bot.db')

# نوع قاعدة البيانات (sqlite أو postgresql)
DATABASE_TYPE = 'postgresql' if DATABASE_PATH.startswith('postgresql://') else 'sqlite'

# إعدادات PostgreSQL (إذا تم استخدامه)
POSTGRES_CONFIG = {
    'host': os.getenv('POSTGRES_HOST', 'localhost'),
    'port': int(os.getenv('POSTGRES_PORT', 5432)),
    'database': os.getenv('POSTGRES_DB', 'inspection_bot'),
    'user': os.getenv('POSTGRES_USER', 'postgres'),
    'password': os.getenv('POSTGRES_PASSWORD', ''),
}

# =============================================================================
# إعدادات التسجيل (Logging)
# =============================================================================

# مستوى التسجيل
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')

# مسار ملف السجلات
LOG_FILE = os.getenv('LOG_FILE', 'bot.log')

# تنسيق السجلات
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# =============================================================================
# إعدادات التخزين المؤقت (Cache)
# =============================================================================

# وقت انتهاء صلاحية الكاش بالثواني
CACHE_TTL = int(os.getenv('CACHE_TTL', 300))

# الحد الأقصى لعناصر الكاش
CACHE_MAX_SIZE = int(os.getenv('CACHE_MAX_SIZE', 1000))

# =============================================================================
# إعدادات التنظيف التلقائي
# =============================================================================

# تنظيف المسودات القديمة بعد (بالأيام)
DRAFT_CLEANUP_DAYS = int(os.getenv('DRAFT_CLEANUP_DAYS', 7))

# تنظيف السجلات القديمة بعد (بالأيام)
LOG_CLEANUP_DAYS = int(os.getenv('LOG_CLEANUP_DAYS', 90))

# =============================================================================
# إعدادات الملفات والمرفقات
# =============================================================================

# الحد الأقصى لعدد المرفقات لكل ملاحظة
MAX_ATTACHMENTS_PER_OBSERVATION = int(os.getenv('MAX_ATTACHMENTS', 500))

# الحد الأقصى لحجم الملف بالبايت (50 ميجابايت)
MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE', 50 * 1024 * 1024))

# أنواع الملفات المسموحة
ALLOWED_FILE_TYPES = ['photo', 'document', 'video', 'audio', 'voice']

# =============================================================================
# إعدادات التقارير
# =============================================================================

# مجلد التقارير المؤقتة
TEMP_REPORTS_DIR = os.getenv('TEMP_REPORTS_DIR', 'temp_reports')

# اسم البوت (للتضمين في التقارير)
BOT_NAME = os.getenv('BOT_NAME', 'InspectionRusafa_bot')

# عنوان التقرير الافتراضي
DEFAULT_REPORT_TITLE = os.getenv('DEFAULT_REPORT_TITLE', 'تقرير تفتيش مدرسي')

# =============================================================================
# إعدادات الأمان
# =============================================================================

# مفتاح تشفير إضافي (اختياري)
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY', '')

# مدة صلاحية الجلسة بالثواني
SESSION_TIMEOUT = int(os.getenv('SESSION_TIMEOUT', 3600))

# الحد الأقصى لمحاولات تسجيل الدخول الفاشلة
MAX_LOGIN_ATTEMPTS = int(os.getenv('MAX_LOGIN_ATTEMPTS', 5))

# =============================================================================
# إعدادات الأداء
# =============================================================================

# مهلة طلبات HTTP بالثواني
HTTP_TIMEOUT = int(os.getenv('HTTP_TIMEOUT', 30))

# عدد محاولات إعادة المحاولة
MAX_RETRIES = int(os.getenv('MAX_RETRIES', 3))

# وقت الانتظار بين المحاولات بالثواني
RETRY_DELAY = int(os.getenv('RETRY_DELAY', 2))

# =============================================================================
# إعدادات الخصوصية
# =============================================================================

# تمكين حذف البيانات الذاتية
ENABLE_SELF_DATA_DELETION = os.getenv('ENABLE_SELF_DATA_DELETION', 'true').lower() == 'true'

# نص سياسة الخصوصية
PRIVACY_POLICY_TEXT = """
سياسة الخصوصية لبوت التفتيش المدرسي:

1. نجمع فقط البيانات الضرورية لعمل البوت (اسم المستخدم، معرف Telegram)
2. يتم تخزين البيانات بشكل آمن ولا تشارك مع أطراف ثالثة
3. يمكنك طلب حذف جميع بياناتك في أي وقت عبر الأمر /deletedata
4. يتم تنظيف البيانات القديمة تلقائياً حسب سياسات الاحتفاظ

باستخدامك للبوت، فإنك توافق على هذه السياسة.
"""

# =============================================================================
# الثوابت العامة
# =============================================================================

# أسماء الأزرار
BUTTON_NEW_VISIT = "➕ زيارة جديدة"
BUTTON_MY_VISITS = "📋 زياراتي"
BUTTON_ALL_VISITS = "🌐 جميع الزيارات"
BUTTON_STATISTICS = "📊 الإحصائيات"
BUTTON_SETTINGS = "⚙️ الإعدادات"
BUTTON_HELP = "❓ المساعدة"
BUTTON_DELETE_DATA = "🗑️ حذف بياناتي"

# المحاور الافتراضية
DEFAULT_AXES = [
    "المحور الأول: القيادة المدرسية",
    "المحور الثاني: التدريس والتعلم",
    "المحور الثالث: البيئة المدرسية",
    "المحور الرابع: المشاركة المجتمعية",
    "المحور الخامس: السلامة والأمن",
]

# حالات الزيارة
VISIT_STATUS_OPEN = "open"
VISIT_STATUS_CLOSED = "closed"

# حالات الملاحظة
OBSERVATION_STATUS_EXCELLENT = "ممتاز"
OBSERVATION_STATUS_GOOD = "جيد"
OBSERVATION_STATUS_AVERAGE = "متوسط"
OBSERVATION_STATUS_NEEDS_IMPROVEMENT = "يحتاج تحسين"

# =============================================================================
# التحقق من الإعدادات المطلوبة
# =============================================================================

REQUIRED_ENV_VARS = ['TELEGRAM_BOT_TOKEN']

missing_vars = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
if missing_vars:
    raise ValueError(f"المتغيرات البيئية التالية مفقودة: {', '.join(missing_vars)}")

# =============================================================================
# دوال مساعدة
# =============================================================================

def is_admin(user_id: int) -> bool:
    """
    التحقق مما إذا كان المستخدم مسؤولاً
    
    Args:
        user_id: معرف المستخدم
        
    Returns:
        True إذا كان مسؤولاً
    """
    return user_id in ADMIN_IDS


def hash_user_id(user_id: int) -> str:
    """
    تجزئة معرف المستخدم للأمان
    
    Args:
        user_id: معرف المستخدم
        
    Returns:
        التجزئة SHA256
    """
    return hashlib.sha256(str(user_id).encode()).hexdigest()


def get_database_url() -> str:
    """
    الحصول على رابط قاعدة البيانات
    
    Returns:
        رابط قاعدة البيانات
    """
    if DATABASE_TYPE == 'postgresql':
        return f"postgresql://{POSTGRES_CONFIG['user']}:{POSTGRES_CONFIG['password']}@{POSTGRES_CONFIG['host']}:{POSTGRES_CONFIG['port']}/{POSTGRES_CONFIG['database']}"
    return DATABASE_PATH
