import os
import io
import json
import zipfile
import asyncio
import logging
import hashlib
import requests
import calendar
from datetime import datetime, timedelta
from functools import wraps
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    JobQueue
)
from telegram.error import TelegramError, RetryAfter, NetworkError

from database import init_db, get_connection, upsert_user_session, delete_user_data, cleanup_old_data, USE_POSTGRES
from report_generator import generate_docx_report

# ==========================================
# إعداد Logging مركزي
# ==========================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def load_env():
    """تحميل متغيرات البيئة من ملف .env"""
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if not os.path.exists(env_path):
        logger.warning(".env file not found, using environment variables only")
        return
    with open(env_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue
            key, value = line.split('=', 1)
            os.environ.setdefault(key.strip(), value.strip())
    logger.info("✅ Loaded environment variables from .env")

load_env()

TOKEN = os.getenv('TOKEN', '').strip()
ADMIN_IDS_RAW = os.getenv('ADMIN_IDS', '').strip()

# تشفير بسيط لـ ADMIN_IDS في الذاكرة (ليس حلاً كاملاً ولكن يحسن الأمان)
def hash_admin_ids(ids_str):
    """Hash ADMIN_IDS للتحقق السريع مع تخزين النسخة الأصلية مشفرة"""
    return hashlib.sha256(ids_str.encode()).hexdigest()

if not TOKEN:
    logger.critical("TOKEN is missing!")
    raise RuntimeError('TOKEN is required in .env or environment variables.')
if not ADMIN_IDS_RAW:
    logger.critical("ADMIN_IDS is missing!")
    raise RuntimeError('ADMIN_IDS is required in .env or environment variables.')

ADMIN_IDS = [int(x) for x in ADMIN_IDS_RAW.split(',') if x.strip().isdigit()]
ADMIN_IDS_HASH = hash_admin_ids(ADMIN_IDS_RAW)

logger.info(f"✅ Bot initialized with {len(ADMIN_IDS)} admin(s)")


def is_admin(user_id):
    """التحقق من صلاحيات المدير"""
    return user_id in ADMIN_IDS


def build_callback_data(action, payload=None):
    """إنشاء callback_data موحد يمكن تحليله بأمان"""
    if payload is None or payload == "":
        return action
    return f"{action}|{payload}"


def parse_callback_data(data):
    """تحليل callback_data بما يدعم الشكل الجديد والشكل القديم"""
    if not data:
        return "", None
    if "|" in data:
        return data.split("|", 1)
    if data in ("cancel_action", "institutions_list"):
        return data, None
    if "_" in data:
        return data.rsplit("_", 1)
    return data, None


def sanitize_text(value, max_length=200):
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) > max_length:
        return text[:max_length]
    return text


def is_valid_text(value, max_length=200):
    text = sanitize_text(value, max_length)
    return bool(text)

def create_calendar(year= int, month= int)-> InlineKeyboardMarkup:
    """إنشاء لوحة مفاتيح تقويم لاختيار تاريخ"""
    now = datetime.now()
    year = year or now.year
    month = month or now.month

    keyboard = []
    month_name = calendar.month_name[month]
    keyboard.append([InlineKeyboardButton(f"{month_name} {year}", callback_data="ignore")])

    week_days = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    keyboard.append([InlineKeyboardButton(day, callback_data="ignore") for day in week_days])

    month_calendar = calendar.monthcalendar(year, month)
    for week in month_calendar:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="ignore"))
            else:
                date_str = f"{year}-{month:02d}-{day:02d}"
                row.append(InlineKeyboardButton(str(day), callback_data=build_callback_data("select_date", date_str)))
        keyboard.append(row)

    # أزرار التنقل بين الأشهر
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    keyboard.append([
        InlineKeyboardButton("⬅️ السابق", callback_data=build_callback_data("change_month", f"{prev_year}-{prev_month}")),
        InlineKeyboardButton("التالي ➡️", callback_data=build_callback_data("change_month", f"{next_year}-{next_month}"))
    ])

    return InlineKeyboardMarkup(keyboard)

# ==========================================
# حالات المحادثة
# ==========================================
INSTITUTION_NAME, VISIT_DATE, VISIT_TYPE, SCHEDULE_DATE = range(4)
ENTER_MEMBER_NAME, ENTER_JOB_TITLE, AXIS_NAME, SECTION_NAME, NOTES, NOTE_CONFIRM, REC_DESTINATION, RECOMMENDATIONS, LOOP_OR_END, SUMMARY_CONFIRM = range(10)
SEARCH_QUERY = 30
INSTITUTION_SEARCH = 31
ATTACHMENT_CAPTION = 40
DRAFT_RESUME = 50

# ==========================================
# القوائم الثابتة
# ==========================================
ADMIN_MENU_KB = [
    ["➕ إنشاء زيارة جديدة", "📋 إدارة الزيارات"],
    ["📊 الإحصائيات", "🔍 البحث عن زيارة"],
    ["🗂 سجل العمليات"],
    ["المؤسسات الصحية"]
]
BUTTON_START_REPORT = "▶️ ابدأ تقرير جديد"
BUTTON_RESUME_DRAFT = "⏱ استئناف المسودة"
BUTTON_HELP = "❓ مساعدة"
MEMBER_MENU_KB = [
    [BUTTON_START_REPORT],
    [BUTTON_RESUME_DRAFT],
    [BUTTON_HELP]
]

AXES_LIST = [
    ["المعلومات العامة"],
    ["المحور الفني"],
    ["المحور الإداري"],
    ["المحور الهندسي"]
]
ATTACHMENT_BUTTON = "📎 إرفاق صورة/ملف"
BACK_BUTTON = "رجوع الى القائمة السابقة"
REPORT_START_KB = AXES_LIST + [[ATTACHMENT_BUTTON]]

SECTION_PRESETS = {
    "المحور الفني": [
        ["الأطباء"],
        ["الصيدلية"],
        ["المختبر"],
        ["الأشعة"],
        ["التمريض"],
        ["اكتب اسم القسم يدوياً"],
        [BACK_BUTTON]
    ],
    "المحور الإداري": [
        ["الإدارة والسجلات"],
        ["وحدة البصمة"],
        ["اكتب اسم القسم يدوياً"],
        [BACK_BUTTON]
    ],
    "المحور الهندسي": [
        ["الاجهزة الطبية"],
        ["الصيانة"],
        ["الدفاع المدني"],
        ["اكتب اسم القسم يدوياً"],
        [BACK_BUTTON]
    ]
}
DESTINATIONS_LIST = [
    ["الإيعاز الى ادارة المستشفى بما يلي:"],
    ["الإيعاز الى ادارة القطاع بما يلي:"],
    ["الإيعاز الى ادارة المركز بما يلي:"],
    ["الإيعاز الى قسم الامور الادارية والقانونية والمالية بما يلي:"],
    ["الإيعاز الى شعبة التحقيقات/ قسمنا بما يلي:"],
    ["اكتب جهة الإيعاز يدوياً"],
    ["لا توجد توصية"],
    [BACK_BUTTON]

]
DESTINATIONS_FLAT = [item for sublist in DESTINATIONS_LIST for item in sublist]

GENERAL_INFO_KB = [
    ["اسم المدير"],
    ["رقم الهاتف"],
    ["رديف المدير"],
    ["المعاون الإداري (مسؤول الذاتية والأفراد)"],
    ["الرديف"],
    ["الملاك الكلي"],
    ["الملاك الفعلي"],
    ["عدد الاطباء الكلي"],
    ["عدد الاطباء الفعلي"],
    ["عدد اطباء الاسنان الكلي"],
    ["عدد اطباء الاسنان الفعلي"],
    ["عدد الصيادلة الكلي"],
    ["عدد الصيادلة الفعلي"],
    ["عدد الافراد"],
    ["عدد العوائل"],
    ["اكتب اسم القسم يدوياً"],
    ["🛑 إنهاء الإدخال"],
    [BACK_BUTTON]
]
# ==========================================
# حالات المحادثة
# ==========================================

# ==========================================
# أدوات قاعدة البيانات المحسنة
# ==========================================
def execute_query(query, params=(), fetch=False):
    """تنفيذ استعلام قاعدة البيانات مع إدارة اتصال محسّنة"""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            if USE_POSTGRES:
                # Convert SQLite ? placeholders to PostgreSQL %s
                query = query.replace('?', '%s')
            cursor.execute(query, params)
            if fetch:
                return cursor.fetchall()
            # For SQLite, commit is handled by the context manager
            # For PostgreSQL, commit is also handled by the context manager
    except Exception as e:
        logger.error(f"Database error executing query: {e}")
        raise
    return None


def log_action(user_id, user_name, action, target_type, target_id, details=''):
    """تسجيل إجراء في سجل التدقيق مع logging"""
    try:
        execute_query(
            "INSERT INTO Audit_Log (user_id, user_name, action, target_type, target_id, details) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, user_name, action, target_type, target_id, details)
        )
        logger.debug(f"Audit: {user_name} ({user_id}) performed {action} on {target_type}#{target_id}")
    except Exception as e:
        logger.error(f"Failed to log action: {e}")


def save_draft(user_id, visit_id, user_name, state, payload):
    payload_text = json.dumps(payload, ensure_ascii=False)
    existing = execute_query(
        "SELECT id FROM Drafts WHERE visit_id = ? AND user_id = ?",
        (visit_id, user_id), fetch=True
    )
    if existing:
        execute_query(
            "UPDATE Drafts SET state = ?, payload = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (str(state), payload_text, existing[0][0])
        )
    else:
        execute_query(
            "INSERT INTO Drafts (visit_id, user_id, user_name, state, payload) VALUES (?, ?, ?, ?, ?)",
            (visit_id, user_id, user_name, str(state), payload_text)
        )


def load_draft(user_id, visit_id):
    row = execute_query(
        "SELECT id, state, payload FROM Drafts WHERE visit_id = ? AND user_id = ?",
        (visit_id, user_id), fetch=True
    )
    if not row:
        return None
    _, state, payload_text = row[0]
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in draft payload: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error loading draft: {e}")
        return None
    return {'state': int(state), 'payload': payload}


def delete_draft(user_id, visit_id):
    execute_query("DELETE FROM Drafts WHERE visit_id = ? AND user_id = ?", (visit_id, user_id))


def _format_datetime(dt):
    return dt.strftime('%Y-%m-%d %H:%M')


def _schedule_pending_reminders(application):
    rows = execute_query(
        "SELECT id, institution_name, visit_date, scheduled_date FROM Visits WHERE scheduled_date IS NOT NULL AND reminder_sent = 0 AND status = 'مفتوحة'",
        fetch=True
    )
    for visit_id, inst_name, visit_date, scheduled_date in rows:
        try:
            scheduled_dt = datetime.strptime(scheduled_date, "%Y-%m-%d %H:%M")
            delay = (scheduled_dt - datetime.now()).total_seconds()
            if delay < 0:
                delay = 1
            application.job_queue.run_once(
                send_visit_reminder,
                when=delay,
                data={'visit_id': visit_id, 'inst_name': inst_name, 'visit_date': visit_date},
                name=f"reminder_{visit_id}"
            )
        except Exception as e:
            logger.error(f"Failed to schedule reminder for visit {visit_id}: {e}")
            continue


def admin_required(func):
    """Decorator للتحقق من صلاحية المدير"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not is_admin(user_id):
            if update.callback_query:
                await update.callback_query.answer("⛔ ليس لديك صلاحية لهذا الإجراء.", show_alert=True)
            else:
                await update.message.reply_text("⛔ هذا الأمر مخصص للمدراء فقط.")
            return ConversationHandler.END
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper

# ==========================================
# 1. بداية المحادثة وانضمام الأعضاء
# ==========================================
async def start_and_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    if args and args[0].startswith("join_"):
        visit_id = args[0].replace("join_", "")
        visit = execute_query(
            "SELECT institution_name, status FROM Visits WHERE id = ?", (visit_id,), fetch=True
        )

        if not visit:
            await update.message.reply_text("⚠️ هذه الزيارة غير موجودة.")
            return ConversationHandler.END

        if visit[0][1] == 'مغلقة':
            await update.message.reply_text("🔒 تم إغلاق هذه الزيارة من قبل الإدارة.")
            return ConversationHandler.END

        institution_name = visit[0][0]
        # تجاهل البيانات السابقة وسؤال المستخدم عن الاسم والعنوان في كل مرة
        # حفظ visit_id في متغيرات الجلسة
        context.user_data['report_visit_id'] = visit_id
        await update.message.reply_text(
            f"✅ تم دخولك إلى زيارة: <b>{institution_name}</b>\n\n"
            "📝 <b>الرجاء إدخال اسمك الثلاثي كما يظهر في الهوية الوظيفية:</b>",
            parse_mode="HTML"
        )
        return ENTER_MEMBER_NAME

    else:
        if is_admin(user.id):
            await update.message.reply_text(
                "مرحباً سيدي المدير 👨‍💼\nاستخدم الأزرار للإدارة أو اكتب /help للمساعدة.",
                reply_markup=ReplyKeyboardMarkup(ADMIN_MENU_KB, resize_keyboard=True)
            )
        else:
            await update.message.reply_text(
                "مرحباً بك في بوت التفتيش 🏛\nاستخدم الأزرار لبدء أو اكتب /help لمساعدة سريعة.",
                reply_markup=ReplyKeyboardMarkup(MEMBER_MENU_KB, resize_keyboard=True)
            )
        return ConversationHandler.END


async def start_another_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    visit = execute_query(
        """SELECT V.id, V.institution_name
           FROM Visits V JOIN Visit_Members M ON V.id = M.visit_id
           WHERE M.user_id = ? AND V.status = 'مفتوحة'
           ORDER BY V.id DESC LIMIT 1""",
        (user_id,), fetch=True
    )

    if not visit:
        await update.message.reply_text("⚠️ لست منضماً لأي زيارة مفتوحة حالياً.")
        return ConversationHandler.END

    visit_id, institution_name = visit[0]
    context.user_data['report_visit_id'] = visit_id
    draft = load_draft(user_id, visit_id)
    if draft:
        context.user_data['pending_draft'] = draft
        await update.message.reply_text(
            f"✅ استئناف الإدخال لزيارة: <b>{institution_name}</b>\n\n"
            "لديك مسودة محفوظة. هل تريد استكمالها أم البدء جديداً؟",
            reply_markup=ReplyKeyboardMarkup(
                [["استكمال المسودة"], ["بدء جديد"]],
                one_time_keyboard=True, resize_keyboard=True
            ),
            parse_mode="HTML"
        )
        return DRAFT_RESUME

    await update.message.reply_text(
        f"✅ استئناف الإدخال لزيارة: <b>{institution_name}</b>\n\nاختر <b>المحور</b> أو أضف مرفقاً:",
        reply_markup=ReplyKeyboardMarkup(REPORT_START_KB, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="HTML"
    )
    return AXIS_NAME

# ==========================================
# 2. حفظ اسم العضو والعنوان الوظيفي
# ==========================================
async def save_member_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حفظ الاسم الثلاثي للعضو والانتقال لطلب العنوان الوظيفي"""
    user = update.effective_user
    user_name = update.message.text.strip()
    
    if len(user_name) < 3:
        await update.message.reply_text(
            "⚠️ الرجاء إدخال اسم ثلاثي صحيح (على الأقل 3 أحرف).\n\n"
            "📝 <b>أدخل اسمك الثلاثي كما يظهر في الهوية الوظيفية:</b>",
            parse_mode="HTML"
        )
        return ENTER_MEMBER_NAME
    
    # حفظ الاسم في متغيرات الجلسة
    context.user_data['member_user_name'] = user_name
    
    await update.message.reply_text(
        f"✅ تم حفظ الاسم: <b>{user_name}</b>\n\n"
        "📝 <b>الرجاء إدخال عنوانك الوظيفي (مثال: معلم، مراقب، مشرف...):</b>",
        parse_mode="HTML"
    )
    return ENTER_JOB_TITLE


async def save_member_job_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حفظ العنوان الوظيفي وإدخال العضو إلى جدول Visit_Members ثم الانتقال للمحاور"""
    user = update.effective_user
    job_title = update.message.text.strip()
    
    if len(job_title) < 2:
        await update.message.reply_text(
            "⚠️ الرجاء إدخال عنوان وظيفي صحيح.\n\n"
            "📝 <b>أدخل عنوانك الوظيفي (مثال: معلم، مراقب، مشرف...):</b>",
            parse_mode="HTML"
        )
        return ENTER_JOB_TITLE
    
    visit_id = context.user_data.get('report_visit_id')
    user_name = context.user_data.get('member_user_name', user.full_name)
    
    # حساب full_name بالصيغة المطلوبة: job_title - user_name
    full_name_value = f"{job_title} - {user_name}"
    
    # إضافة العضو إلى قاعدة البيانات مع الاسم والعنوان الوظيفي
    try:
        # السماح بتسجيلات متعددة لنفس المستخدم في نفس الزيارة بأسماء مختلفة
        # نستخدم ON CONFLICT DO UPDATE على اساس id (اذا تم تمريره) او نقوم بادراج جديد دائماً
        # بما اننا ازلنا القيد UNIQUE(visit_id, user_id)، يمكن الآن ادراج سجلات متعددة
        execute_query(
            """INSERT INTO Visit_Members (visit_id, user_id, user_name, full_name, job_title)
               VALUES (?, ?, ?, ?, ?)""",
            (visit_id, user.id, user_name, full_name_value, job_title)
        )
        
        # الحصول على اسم المؤسسة للترحيب
        visit = execute_query(
            "SELECT institution_name FROM Visits WHERE id = ?", (visit_id,), fetch=True
        )
        institution_name = visit[0][0] if visit else "الزيارة"
        
        # تخزين full_name بالصيغة المطلوبة للاستخدام في الإشعارات
        context.user_data['member_display_full_name'] = full_name_value
        
        await update.message.reply_text(
            f"✅ تم تسجيل بياناتك بنجاح!\n"
            f"👤 الاسم: {user_name}\n"
            f"💼 المسمى الوظيفي: {job_title}\n\n"
            f"🏥 <b>المؤسسة: {institution_name}</b>\n\n"
            "اختر <b>المحور</b> أو أضف مرفقاً:",
            reply_markup=ReplyKeyboardMarkup(REPORT_START_KB, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="HTML"
        )
        
        # تنظيف متغيرات الجلسة المؤقتة
        context.user_data.pop('member_user_name', None)
        
        return AXIS_NAME
        
    except Exception as e:
        logger.error(f"Error saving member data: {e}")
        await update.message.reply_text(
            "⚠️ حدث خطأ أثناء حفظ بياناتك. حاول مرة أخرى.\n\n"
            "إذا استمر الخطأ، تواصل مع المشرف.",
            parse_mode="HTML"
        )
        return ConversationHandler.END


# ==========================================
# 3. خطوات إدخال التقرير
# ==========================================
async def get_axis_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['current_state'] = AXIS_NAME
    axis = update.message.text
    if axis == ATTACHMENT_BUTTON:
        await update.message.reply_text(
            "📎 أرسل الآن الصورة أو الملف الذي تريد إرفاقه:",
            reply_markup=ReplyKeyboardRemove()
        )
        return ATTACHMENT_CAPTION

    if [axis] not in AXES_LIST:
        await update.message.reply_text(
            "⚠️ اختر محوراً صحيحاً:",
            reply_markup=ReplyKeyboardMarkup(REPORT_START_KB, resize_keyboard=True)
        )
        return AXIS_NAME

    context.user_data['current_axis'] = axis
    if axis == "المعلومات العامة":
        await update.message.reply_text(
            "📋 اختر الحقل:",
            reply_markup=ReplyKeyboardMarkup(GENERAL_INFO_KB, resize_keyboard=True)
        )
    else:
        section_kb = SECTION_PRESETS.get(axis, [["اكتب اسم القسم يدوياً"]])
        await update.message.reply_text(
            "📂 اختر قسماً متكرراً أو اكتب اسم القسم:",
            reply_markup=ReplyKeyboardMarkup(section_kb, one_time_keyboard=True, resize_keyboard=True)
        )
    return SECTION_NAME


async def get_section_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['current_state'] = SECTION_NAME
    section_text = update.message.text.strip()
    if section_text == BACK_BUTTON:
        await update.message.reply_text(
            "✅ عدت إلى قائمة المحاور. اختر المحور المطلوب:",
            reply_markup=ReplyKeyboardMarkup(REPORT_START_KB, one_time_keyboard=True, resize_keyboard=True)
        )
        return AXIS_NAME

    if section_text == "🛑 إنهاء الإدخال":
        await update.message.reply_text(
            "✅ تم إنهاء الإدخال.",
            reply_markup=ReplyKeyboardMarkup(ADMIN_MENU_KB if is_admin(update.effective_user.id) else MEMBER_MENU_KB, resize_keyboard=True)
        )
        return ConversationHandler.END

    if section_text == "اكتب اسم القسم يدوياً":
        await update.message.reply_text(
            "📂 اكتب اسم القسم:",
            reply_markup=ReplyKeyboardRemove()
        )
        return SECTION_NAME

    context.user_data['current_section'] = section_text
    if context.user_data['current_axis'] == "المعلومات العامة":
        await update.message.reply_text(
            f"🔢 أدخل القيمة لـ <b>{context.user_data['current_section']}</b>:",
            reply_markup=ReplyKeyboardRemove(), parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            "🔍 أرسل <b>الملاحظات التفتيشية</b> لهذا القسم:",
            parse_mode="HTML"
        )
    return NOTES


async def get_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['current_state'] = NOTES
    text = update.message.text.strip()
    if text == BACK_BUTTON:
        await update.message.reply_text(
            "✅ عدت إلى القائمة السابقة. اختر القسم أو المحور:",
            reply_markup=ReplyKeyboardMarkup(REPORT_START_KB, one_time_keyboard=True, resize_keyboard=True)
        )
        return AXIS_NAME

    context.user_data['current_notes'] = text
    if context.user_data.get('current_axis') == "المعلومات العامة":
        execute_query(
            "INSERT INTO Reports (visit_id, user_id, axis_name, section_name, notes, rec_destination, recommendations) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (context.user_data['report_visit_id'], update.effective_user.id,
             context.user_data['current_axis'], context.user_data['current_section'],
             context.user_data['current_notes'], "", "")
        )
        # استخدام full_name بالصيغة المطلوبة (job_title - full_name) إذا كان متاحاً
        sender_display_name = context.user_data.get('member_display_full_name', update.effective_user.full_name)
        await _notify_admins_report(context, sender_display_name,
                                    context.user_data['report_visit_id'],
                                    context.user_data['current_axis'],
                                    context.user_data['current_section'])
        await update.message.reply_text(
            "✅ تم حفظ المعلومة!\nاختر حقلاً آخر من المعلومات العامة أو عد للمحاور:",
            reply_markup=ReplyKeyboardMarkup(GENERAL_INFO_KB, resize_keyboard=True),
            parse_mode="HTML"
        )
        return SECTION_NAME

    context.user_data['current_recommendations'] = []
    await update.message.reply_text(
        "🎯 لمن تود توجيه التوصية؟",
        reply_markup=ReplyKeyboardMarkup(DESTINATIONS_LIST, one_time_keyboard=True, resize_keyboard=True)
    )
    return REC_DESTINATION


async def confirm_note_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text
    if choice == "✏️ تعديل الملاحظة":
        await update.message.reply_text("✍️ حسناً، أعد كتابة الملاحظة أو القيمة:")
        return NOTES

    if choice != "✅ حفظ الملاحظة":
        await update.message.reply_text(
            "⚠️ اختر إما حفظ الملاحظة أو تعديلها.",
            reply_markup=ReplyKeyboardMarkup(
                [["✅ حفظ الملاحظة"], ["✏️ تعديل الملاحظة"]],
                one_time_keyboard=True, resize_keyboard=True
            )
        )
        return NOTE_CONFIRM

    await update.message.reply_text(
        "🎯 لمن تود توجيه التوصية؟",
        reply_markup=ReplyKeyboardMarkup(DESTINATIONS_LIST, one_time_keyboard=True, resize_keyboard=True)
    )
    context.user_data['current_recommendations'] = []
    return REC_DESTINATION


async def get_rec_destination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['current_state'] = REC_DESTINATION
    text = update.message.text.strip()

    # If we previously asked the user to type a custom destination, accept any text now
    if context.user_data.get('awaiting_custom_destination'):
        dest = text
        context.user_data.pop('awaiting_custom_destination', None)
        context.user_data['current_rec_dest'] = dest
        context.user_data['current_recommendations'] = []
        await update.message.reply_text(
            "💡 اكتب نص التوصية الأولى أو أرسل (لا توجد توصية):",
            reply_markup=ReplyKeyboardMarkup([["لا توجد توصية"]], one_time_keyboard=True, resize_keyboard=True),
            parse_mode="HTML"
        )
        return RECOMMENDATIONS

    if text == BACK_BUTTON:
        await update.message.reply_text(
            "✅ عدت إلى القسم السابق. اختر القسم أو المحور:",
            reply_markup=ReplyKeyboardMarkup(SECTION_PRESETS.get(context.user_data.get('current_axis'), [["اكتب اسم القسم يدوياً"]]) if context.user_data.get('current_axis') != "المعلومات العامة" else GENERAL_INFO_KB, one_time_keyboard=True, resize_keyboard=True)
        )
        return SECTION_NAME

    # If user chose to write destination manually, prompt for it
    if text == "اكتب جهة الإيعاز يدوياً":
        context.user_data['awaiting_custom_destination'] = True
        await update.message.reply_text(
            "📌 اكتب جهة الإيعاز يدوياً:",
            reply_markup=ReplyKeyboardRemove()
        )
        return REC_DESTINATION

    # Allow saving draft while selecting destination
    if text == "💾 حفظ كمسودة":
        user = update.effective_user
        visit_id = context.user_data.get('report_visit_id')
        save_draft(user.id, visit_id, user.full_name, REC_DESTINATION, context.user_data)
        await update.message.reply_text(
            "💾 تم حفظ المسودة! يمكنك العودة لاحقاً لاستكمالها.",
            reply_markup=ReplyKeyboardMarkup(MEMBER_MENU_KB if not is_admin(user.id) else ADMIN_MENU_KB, resize_keyboard=True)
        )
        return ConversationHandler.END

    dest = text
    if dest not in DESTINATIONS_FLAT:
        await update.message.reply_text(
            "⚠️ اختر جهة توجيه صحيحة:",
            reply_markup=ReplyKeyboardMarkup(DESTINATIONS_LIST, one_time_keyboard=True, resize_keyboard=True)
        )
        return REC_DESTINATION

    context.user_data['current_rec_dest'] = dest
    context.user_data['current_recommendations'] = []
    await update.message.reply_text(
        "💡 اكتب نص التوصية الأولى أو أرسل (لا توجد توصية):",
        reply_markup=ReplyKeyboardMarkup([["لا توجد توصية"]], one_time_keyboard=True, resize_keyboard=True),
        parse_mode="HTML"
    )
    return RECOMMENDATIONS


async def get_recommendations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['current_state'] = RECOMMENDATIONS
    text = update.message.text.strip()
    current_recs = context.user_data.get('current_recommendations', [])

    if text == BACK_BUTTON:
        await update.message.reply_text(
            "✅ عدت إلى اختيار جهة الإيعاز. اختر جهة الإيعاز:",
            reply_markup=ReplyKeyboardMarkup(DESTINATIONS_LIST, one_time_keyboard=True, resize_keyboard=True)
        )
        return REC_DESTINATION

    if text == "لا توجد توصية":
        recommendation_text = ""
        await _save_report_entry(update, context, recommendation_text)
        return LOOP_OR_END

    if text == "✅ إنهاء التوصيات":
        if not current_recs:
            await update.message.reply_text(
                "⚠️ أرسل توصية واحدة على الأقل أو اختر (لا توجد توصية)."
            )
            return RECOMMENDATIONS
        recommendation_text = "\n".join(f"- {r}" for r in current_recs)
        await _save_report_entry(update, context, recommendation_text)
        return LOOP_OR_END

    current_recs.append(text)
    context.user_data['current_recommendations'] = current_recs
    await update.message.reply_text(
        "✅ تمت إضافة التوصية. أرسل توصية أخرى أو اضغط (✅ إنهاء التوصيات).",
        reply_markup=ReplyKeyboardMarkup(
            [["✅ إنهاء التوصيات"], ["لا توجد توصية"]],
            one_time_keyboard=True, resize_keyboard=True
        )
    )
    return RECOMMENDATIONS


async def _save_report_entry(update: Update, context: ContextTypes.DEFAULT_TYPE, recommendations_text: str):
    execute_query(
        "INSERT INTO Reports (visit_id, user_id, axis_name, section_name, notes, rec_destination, recommendations) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (context.user_data['report_visit_id'], update.effective_user.id,
         context.user_data['current_axis'], context.user_data['current_section'],
         context.user_data['current_notes'], context.user_data['current_rec_dest'],
         recommendations_text)
    )
    # استخدام full_name بالصيغة المطلوبة (job_title - full_name) إذا كان متاحاً
    sender_display_name = context.user_data.get('member_display_full_name', update.effective_user.full_name)
    await _notify_admins_report(context, sender_display_name,
                                context.user_data['report_visit_id'],
                                context.user_data['current_axis'],
                                context.user_data['current_section'])
    await update.message.reply_text(
        "📥 تم الحفظ!\nهل تود إضافة المزيد؟",
        reply_markup=ReplyKeyboardMarkup(
            [["➕ إضافة قسم آخر"], ["📎 إرفاق صورة/ملف"], ["💾 حفظ كمسودة"], ["🛑 إنهاء الإدخال"], [BACK_BUTTON]],
            one_time_keyboard=True, resize_keyboard=True
        )
    )


async def _notify_admins_report(context, full_name, visit_id, axis, section):
    """إشعار المدراء عند إرسال ملاحظة جديدة"""
    visit_info = execute_query("SELECT institution_name FROM Visits WHERE id = ?", (visit_id,), fetch=True)
    if not visit_info:
        return
    inst = visit_info[0][0]
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                admin_id,
                f"📋 <b>ملاحظة جديدة أُضيفت</b>\n"
                f"👤 المُرسل: {full_name}\n"
                f"🏥 الزيارة: {inst}\n"
                f"📌 المحور: {axis} / {section}",
                parse_mode="HTML"
            )
        except TelegramError as e:
            logger.warning(f"Failed to notify admin {admin_id} of new report: {e}")
        except Exception as e:
            logger.error(f"Unexpected error notifying admin {admin_id}: {e}")


async def resume_draft_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text
    draft = context.user_data.get('pending_draft')
    if not draft:
        await update.message.reply_text("⚠️ لا توجد مسودة للاستكمال.")
        return ConversationHandler.END

    if choice == "استكمال المسودة":
        context.user_data.update(draft['payload'])
        await update.message.reply_text(
            "✅ استؤنفت المسودة. تابع من حيث توقفت.",
            reply_markup=ReplyKeyboardRemove()
        )
        return await _resume_draft_state(update, context, draft['state'])

    delete_draft(update.effective_user.id, context.user_data['report_visit_id'])
    await update.message.reply_text(
        "✅ تم بدء إدخال جديد.",
        reply_markup=ReplyKeyboardMarkup(REPORT_START_KB, one_time_keyboard=True, resize_keyboard=True)
    )
    return AXIS_NAME


async def resume_saved_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    draft = execute_query(
        "SELECT visit_id, state, payload FROM Drafts WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
        (user_id,), fetch=True
    )
    if not draft:
        await update.message.reply_text(
            "⚠️ لم يتم العثور على مسودة محفوظة.",
            reply_markup=ReplyKeyboardMarkup(MEMBER_MENU_KB, resize_keyboard=True)
        )
        return ConversationHandler.END

    visit_id, state, payload_text = draft[0]
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in saved draft: {e}")
        payload = {}
    except Exception as e:
        logger.error(f"Unexpected error loading saved draft: {e}")
        payload = {}

    context.user_data['report_visit_id'] = visit_id
    context.user_data.update(payload)
    await update.message.reply_text(
        "✅ استؤنفت المسودة المحفوظة. تابع من حيث توقفت.",
        reply_markup=ReplyKeyboardRemove()
    )
    return await _resume_draft_state(update, context, int(state))


async def _resume_draft_state(update: Update, context: ContextTypes.DEFAULT_TYPE, state: int):
    if state == AXIS_NAME:
        await update.message.reply_text(
            "اختر <b>المحور</b> المطلوب:",
            reply_markup=ReplyKeyboardMarkup(REPORT_START_KB, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="HTML"
        )
        return AXIS_NAME
    if state == SECTION_NAME:
        axis = context.user_data.get('current_axis', "المحور الفني")
        section_kb = SECTION_PRESETS.get(axis, [["اكتب اسم القسم يدوياً"]])
        await update.message.reply_text(
            "📂 اختر قسماً متكرراً أو اكتب اسم القسم:",
            reply_markup=ReplyKeyboardMarkup(section_kb, one_time_keyboard=True, resize_keyboard=True)
        )
        return SECTION_NAME
    if state == NOTES:
        await update.message.reply_text(
            "🔍 أرسل الملاحظة التفتيشية لهذا القسم:",
            reply_markup=ReplyKeyboardRemove()
        )
        return NOTES
    if state == REC_DESTINATION:
        await update.message.reply_text(
            "🎯 لمن تود توجيه التوصية؟",
            reply_markup=ReplyKeyboardMarkup(DESTINATIONS_LIST, one_time_keyboard=True, resize_keyboard=True)
        )
        return REC_DESTINATION
    if state == RECOMMENDATIONS:
        await update.message.reply_text(
            "💡 أرسل نص التوصية أو اختر (لا توجد توصية):",
            reply_markup=ReplyKeyboardMarkup(
                [["لا توجد توصية"]], one_time_keyboard=True, resize_keyboard=True
            )
        )
        return RECOMMENDATIONS
    if state == LOOP_OR_END:
        await update.message.reply_text(
            "✅ استمر أو أنهِ الإدخال:",
            reply_markup=ReplyKeyboardMarkup(
                [["➕ إضافة قسم آخر"], ["📎 إرفاق صورة/ملف"], ["💾 حفظ كمسودة"], ["🛑 إنهاء الإدخال"]],
                one_time_keyboard=True, resize_keyboard=True
            )
        )
        return LOOP_OR_END
    await update.message.reply_text(
        "✅ تابع متى شئت.",
        reply_markup=ReplyKeyboardRemove()
    )
    return AXIS_NAME


async def _notify_admins_entry_summary(update: Update, context: ContextTypes.DEFAULT_TYPE, user_name: str, visit_id: int):
    visit_info = execute_query("SELECT institution_name FROM Visits WHERE id = ?", (visit_id,), fetch=True)
    if not visit_info:
        return
    inst_name = visit_info[0][0]
    count = execute_query(
        "SELECT COUNT(*) FROM Reports WHERE visit_id = ? AND user_id = ?",
        (visit_id, update.effective_user.id), fetch=True
    )
    total = count[0][0] if count else 0
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                admin_id,
                f"📌 <b>انتهى إدخال عضو</b>\n"
                f"👤 {user_name}\n"
                f"🏥 {inst_name}\n"
                f"📝 عدد الملاحظات التي أرسلها: {total}",
                parse_mode="HTML"
            )
        except Exception:
            pass


async def _show_end_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    visit_id = context.user_data.get('report_visit_id')
    user_id = update.effective_user.id
    reports_count = execute_query(
        "SELECT COUNT(*) FROM Reports WHERE visit_id = ? AND user_id = ?",
        (visit_id, user_id), fetch=True
    )[0][0]
    recommendations_count = execute_query(
        "SELECT COUNT(*) FROM Reports WHERE visit_id = ? AND user_id = ? AND recommendations != ''",
        (visit_id, user_id), fetch=True
    )[0][0]

    await update.message.reply_text(
        f"📋 ملخص قبل الإنهاء:\n"
        f"عدد الملاحظات: {reports_count}\n"
        f"عدد العناصر مع توصيات: {recommendations_count}\n\n"
        "هل تريد التأكيد على إنهاء الجلسة أم إضافة المزيد؟",
        reply_markup=ReplyKeyboardMarkup(
            [["✅ تأكيد الإنهاء"], ["➕ إضافة قسم آخر"], ["📎 إرفاق صورة/ملف"], ["💾 حفظ كمسودة"], [BACK_BUTTON]],
            one_time_keyboard=True, resize_keyboard=True
        )
    )
    return SUMMARY_CONFIRM


async def process_loop_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['current_state'] = LOOP_OR_END
    choice = update.message.text

    if choice == "➕ إضافة قسم آخر":
        await update.message.reply_text(
            "اختر <b>المحور</b> للقسم الجديد:",
            reply_markup=ReplyKeyboardMarkup(REPORT_START_KB, resize_keyboard=True),
            parse_mode="HTML"
        )
        return AXIS_NAME

    elif choice == "📎 إرفاق صورة/ملف":
        await update.message.reply_text(
            "📎 أرسل الصورة أو الملف الآن، ويمكنك إضافة وصف مختصر معه.\n"
            "بعد الإرسال ستُسألك إن كنت تريد إرفاق المزيد.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ATTACHMENT_CAPTION

    elif choice == "🛑 إنهاء الإدخال":
        return await _show_end_summary(update, context)

    elif choice == BACK_BUTTON:
        await update.message.reply_text(
            "✅ عدت إلى قائمة المحاور. اختر المحور أو أضف مرفقاً:",
            reply_markup=ReplyKeyboardMarkup(REPORT_START_KB, one_time_keyboard=True, resize_keyboard=True)
        )
        return AXIS_NAME

    elif choice == "✅ تأكيد الإنهاء":
        reports_count = execute_query(
            "SELECT COUNT(*) FROM Reports WHERE visit_id = ? AND user_id = ?",
            (context.user_data['report_visit_id'], update.effective_user.id), fetch=True
        )[0][0]
        attachments_count = execute_query(
            "SELECT COUNT(*) FROM Attachments WHERE visit_id = ? AND user_id = ?",
            (context.user_data['report_visit_id'], update.effective_user.id), fetch=True
        )[0][0]

        if is_admin(update.effective_user.id):
            await update.message.reply_text(
                f"✅ تم إنهاء جلستك.\nعدد الملاحظات: {reports_count}\nعدد المرفقات: {attachments_count}",
                reply_markup=ReplyKeyboardMarkup(ADMIN_MENU_KB, resize_keyboard=True)
            )
        else:
            await update.message.reply_text(
                f"✅ تم إرسال تقريرك بنجاح. شكراً لجهودك.\nعدد الملاحظات: {reports_count}\nعدد المرفقات: {attachments_count}",
                reply_markup=ReplyKeyboardMarkup(MEMBER_MENU_KB, resize_keyboard=True)
            )
        await _notify_admins_entry_summary(update, context, update.effective_user.full_name, context.user_data['report_visit_id'])
        delete_draft(update.effective_user.id, context.user_data['report_visit_id'])
        return ConversationHandler.END

    elif choice == "💾 حفظ كمسودة":
        user = update.effective_user
        save_draft(user.id, context.user_data['report_visit_id'], user.full_name, LOOP_OR_END, context.user_data)
        await update.message.reply_text(
            "💾 تم حفظ المسودة! يمكنك العودة لاحقاً لاستكمالها.",
            reply_markup=ReplyKeyboardMarkup(MEMBER_MENU_KB if not is_admin(user.id) else ADMIN_MENU_KB, resize_keyboard=True)
        )
        return ConversationHandler.END

    else:
        reports_count = execute_query(
            "SELECT COUNT(*) FROM Reports WHERE visit_id = ? AND user_id = ?",
            (context.user_data['report_visit_id'], update.effective_user.id), fetch=True
        )[0][0]
        attachments_count = execute_query(
            "SELECT COUNT(*) FROM Attachments WHERE visit_id = ? AND user_id = ?",
            (context.user_data['report_visit_id'], update.effective_user.id), fetch=True
        )[0][0]

        if is_admin(update.effective_user.id):
            await update.message.reply_text(
                f"✅ تم إنهاء جلستك.\nعدد الملاحظات: {reports_count}\nعدد المرفقات: {attachments_count}",
                reply_markup=ReplyKeyboardMarkup(ADMIN_MENU_KB, resize_keyboard=True)
            )
        else:
            await update.message.reply_text(
                f"✅ تم إرسال تقريرك بنجاح. شكراً لجهودك.\nعدد الملاحظات: {reports_count}\nعدد المرفقات: {attachments_count}",
                reply_markup=ReplyKeyboardMarkup(MEMBER_MENU_KB, resize_keyboard=True)
            )
        await _notify_admins_entry_summary(update, context, update.effective_user.full_name, context.user_data['report_visit_id'])
        delete_draft(update.effective_user.id, context.user_data['report_visit_id'])
        return ConversationHandler.END


# ==========================================
# 3. استقبال المرفقات (صور وملفات)
# ==========================================
async def receive_attachment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    visit_id = context.user_data.get('report_visit_id')
    if not visit_id:
        await update.message.reply_text("⚠️ لا توجد زيارة نشطة.")
        return ConversationHandler.END

    msg = update.message
    caption = msg.caption or ""
    file_id = None
    file_type = None
    file_name = None

    if msg.photo:
        file_id = msg.photo[-1].file_id
        file_type = "photo"
        file_name = f"photo_{file_id[:8]}.jpg"
    elif msg.document:
        file_id = msg.document.file_id
        file_type = "document"
        file_name = msg.document.file_name or f"file_{file_id[:8]}"
    elif msg.video:
        file_id = msg.video.file_id
        file_type = "video"
        file_name = f"video_{file_id[:8]}.mp4"
    else:
        await update.message.reply_text("⚠️ نوع الملف غير مدعوم. أرسل صورة أو ملف.")
        return LOOP_OR_END

    execute_query(
        "INSERT INTO Attachments (visit_id, user_id, user_name, file_id, file_type, file_name, caption) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (visit_id, user.id, user.full_name, file_id, file_type, file_name, caption)
    )

    # إشعار المدير بالمرفق الجديد
    visit_info = execute_query("SELECT institution_name FROM Visits WHERE id = ?", (visit_id,), fetch=True)
    inst_name = visit_info[0][0] if visit_info else "؟"
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                admin_id,
                f"📎 <b>مرفق جديد</b>\n👤 {user.full_name}\n🏥 {inst_name}\n📄 {file_name}",
                parse_mode="HTML"
            )
        except Exception:
            pass

    await update.message.reply_text(
        "✅ تم حفظ المرفق!\nهل تود إرفاق المزيد أو المتابعة؟",
        reply_markup=ReplyKeyboardMarkup(
            [["📎 إرفاق صورة/ملف أخرى"], ["➕ إضافة قسم آخر"], ["🛑 إنهاء الإدخال"], [BACK_BUTTON]],
            one_time_keyboard=True, resize_keyboard=True
        )
    )
    return LOOP_OR_END


# ==========================================
# 4. أوامر المدير - إنشاء زيارة
# ==========================================
@admin_required
async def create_visit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✏️ اكتب اسم المؤسسة الصحية:",
        reply_markup=ReplyKeyboardRemove()
    )
    return INSTITUTION_NAME


async def get_institution_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        msg = update.callback_query.message
    else:
        msg = update.message

    if update.message and update.message.text:
        context.user_data['inst_name'] = update.message.text.strip()
    
    now = datetime.now()
    reply_markup = create_calendar(now.year, now.month)

    await msg.reply_text("📅 اختر تاريخ الزيارة من التقويم", reply_markup=reply_markup)
    return VISIT_DATE


async def get_visit_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # نستقبل البيانات من الزر الذي تم الضغط عليه
    query = update.callback_query
    await query.answer() # لإيقاف دائرة التحميل في زر تيليجرام
    
    data = query.data
    logger.info(f"👉 تم استلام ضغطة زر من التقويم، البيانات: {data}")
    # 1. إذا ضغط على يوم فارغ أو اسم الشهر (للعرض فقط)
    try:
        if data == "IGNORE":
            return VISIT_DATE
            
        # 2. إذا ضغط على أزرار التقليب بين الأشهر (السابق / التالي)
        if data.startswith("PREV:") or data.startswith("NEXT:"):
            _, year, month = data.split(":")
            year, month = int(year), int(month)
            
            if data.startswith("PREV:"):
                month -= 1
                if month == 0:
                    month = 12
                    year -= 1
            else:
                month += 1
                if month == 13:
                    month = 1
                    year += 1
                    
            # تحديث التقويم للشهر الجديد
            reply_markup = create_calendar(year, month)
            await query.edit_message_reply_markup(reply_markup=reply_markup)
            logger.info(f"👉 تم تحديث التقويم إلى {year}-{month:02d}")
            return VISIT_DATE
            
        # 3. إذا ضغط على تاريخ محدد
        if data.startswith("DATE:"):
            # استخراج التاريخ من الزر (مثال: من "DATE:2025-06-01" نأخذ "2025-06-01")
            selected_date = data.split(":")[1]
            
            # حفظ التاريخ في بيانات المستخدم (نفس ما كنت تفعله في الكود القديم)
            context.user_data['visit_date'] = selected_date
            
            # تعديل رسالة التقويم لإخبار المستخدم بأنه تم اختيار التاريخ بنجاح
            await query.edit_message_text(text=f"✅ تم اختيار تاريخ الزيارة: {selected_date}")
            logger.info(f"👉 تم اختيار تاريخ الزيارة: {selected_date}")

            # --- هنا ضع رسالتك للسؤال التالي ---
            await query.message.reply_text("الرجاء إدخال نوع الزيارة:")
            
        # عرض خيارات نوع الزيارة
        kb = [["تفتيشية"], ["متابعة"], ["متابعة تنفيذ توصيات"]]
        await update.message.reply_text(
            "📋 اختر <b>نوع الزيارة</b>:",
            reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="HTML"
        )
        logging.info(f"✅ تم اختيار التاريخ ({selected_date}) والانتقال للخطوة التالية بنجاح.")
        return VISIT_TYPE
    except Exception as e:
        logging.error(f"❌ حدث خطأ صامت أثناء معالجة التقويم: {e}")
        return VISIT_DATE

async def get_visit_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    visit_type = update.message.text.strip()
    if visit_type not in ["تفتيشية", "متابعة", "متابعة تنفيذ توصيات"]:
        await update.message.reply_text("⚠️ الرجاء اختيار نوع زيارة صحيح من القائمة:")
        return VISIT_TYPE
    
    context.user_data['visit_type'] = visit_type
    
    await update.message.reply_text(
        "⏰ هل تريد جدولة تذكير لهذه الزيارة؟\n"
        "أرسل تاريخ ووقت التذكير بصيغة YYYY-MM-DD HH:MM (مثال: 2025-06-01 08:00)\n"
        "أو أرسل (لا) للتخطي:",
        reply_markup=ReplyKeyboardRemove()
    )
    return SCHEDULE_DATE


async def get_schedule_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    inst_name = context.user_data['inst_name']
    visit_date = context.user_data['visit_date']

    scheduled_date = None
    if text.lower() != "لا":
        try:
            scheduled_dt = datetime.strptime(text, "%Y-%m-%d %H:%M")
            scheduled_date = scheduled_dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            await update.message.reply_text(
                "⚠️ صيغة التذكير غير صحيحة. أرسل التاريخ والوقت بصيغة YYYY-MM-DD HH:MM أو أرسل (لا)."
            )
            return SCHEDULE_DATE

    with get_connection() as conn:
        cursor = conn.cursor()
        visit_type = context.user_data.get('visit_type', 'تفتيشية')
        if USE_POSTGRES:
            query = "INSERT INTO Visits (institution_name, visit_date, visit_type, manager_id, status, scheduled_date) VALUES (%s, %s, %s, %s, 'مفتوحة', %s) RETURNING id"
            cursor.execute(query, (inst_name, visit_date, visit_type, update.effective_user.id, scheduled_date))
            visit_id = cursor.fetchone()[0]
        else:
            query = "INSERT INTO Visits (institution_name, visit_date, visit_type, manager_id, status, scheduled_date) VALUES (?, ?, ?, ?, 'مفتوحة', ?)"
            cursor.execute(query, (inst_name, visit_date, visit_type, update.effective_user.id, scheduled_date))
            visit_id = cursor.lastrowid

    if scheduled_date:
        try:
            scheduled_dt = datetime.strptime(scheduled_date, "%Y-%m-%d %H:%M")
            delay = (scheduled_dt - datetime.now()).total_seconds()
            if delay < 0:
                delay = 1
            context.job_queue.run_once(
                send_visit_reminder,
                when=delay,
                data={'visit_id': visit_id, 'inst_name': inst_name, 'visit_date': visit_date},
                name=f"reminder_{visit_id}"
            )
        except Exception:
            pass

    link = f"https://t.me/InspectionRusafa_bot?start=join_{visit_id}"
    await update.message.reply_text(
        f"✅ <b>تم إنشاء الزيارة!</b>\n\n"
        f"🏥 المؤسسة: {inst_name}\n"
        f"📅 التاريخ: {visit_date}\n"
        f"📋 النوع: {visit_type}\n"
        f"{'⏰ تذكير مجدول: ' + scheduled_date if scheduled_date else ''}\n\n"
        f"🔗 <b>رابط الانضمام:</b>\n<code>{link}</code>",
        reply_markup=ReplyKeyboardMarkup(ADMIN_MENU_KB, resize_keyboard=True),
        parse_mode="HTML"
    )
    return ConversationHandler.END


async def send_visit_reminder(context: ContextTypes.DEFAULT_TYPE):
    """إرسال تذكير بالزيارة المجدولة"""
    data = context.job.data
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                admin_id,
                f"⏰ <b>تذكير بزيارة مجدولة!</b>\n"
                f"🏥 المؤسسة: {data['inst_name']}\n"
                f"📅 التاريخ: {data['visit_date']}\n"
                f"🔗 رابط الزيارة:\n"
                f"<code>https://t.me/InspectionRusafa_bot?start=join_{data['visit_id']}</code>",
                parse_mode="HTML"
            )
        except Exception:
            pass
    execute_query("UPDATE Visits SET reminder_sent = 1 WHERE id = ?", (data['visit_id'],))


# ==========================================
# 5. إدارة الزيارات
# ==========================================
@admin_required
async def manage_visits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _show_visits_list(update, status=None, order='DESC')


@admin_required
async def manage_institutions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    institutions = execute_query(
        "SELECT DISTINCT institution_name FROM Visits ORDER BY institution_name ASC",
        fetch=True
    )
    if not institutions:
        await update.message.reply_text(
            "⚠️ لا توجد مؤسسات مسجلة.",
            reply_markup=ReplyKeyboardMarkup(ADMIN_MENU_KB, resize_keyboard=True)
        )
        return ConversationHandler.END

    context.user_data['institution_map'] = {
        str(idx): row[0]
        for idx, row in enumerate(institutions, start=1)
    }
    keyboard = [
        [InlineKeyboardButton(row[0], callback_data=build_callback_data("institution", idx))]
        for idx, row in enumerate(institutions, start=1)
    ]
    await update.message.reply_text(
        "🏥 اختر مؤسسة من القائمة أو اكتب جزءاً من اسم المؤسسة للبحث:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return INSTITUTION_SEARCH


async def institution_search_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.message.text.strip()
    if not query_text:
        return await manage_institutions(update, context)

    query = f"%{query_text}%"
    institutions = execute_query(
        "SELECT DISTINCT institution_name FROM Visits WHERE institution_name LIKE ? ORDER BY institution_name ASC",
        (query,), fetch=True
    )
    if not institutions:
        await update.message.reply_text(
            "⚠️ لا توجد مؤسسات مطابقة.",
            reply_markup=ReplyKeyboardMarkup(ADMIN_MENU_KB, resize_keyboard=True)
        )
        return ConversationHandler.END

    context.user_data['institution_map'] = {
        str(idx): row[0]
        for idx, row in enumerate(institutions, start=1)
    }
    keyboard = [
        [InlineKeyboardButton(row[0], callback_data=build_callback_data("institution", idx))]
        for idx, row in enumerate(institutions, start=1)
    ]
    await update.message.reply_text(
        f"🔍 نتائج البحث عن: {query_text}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return INSTITUTION_SEARCH


async def _show_institution_visits(query, institution_name, context: ContextTypes.DEFAULT_TYPE):
    visits = execute_query(
        "SELECT id, visit_date, status FROM Visits WHERE institution_name = ? ORDER BY visit_date DESC, id DESC",
        (institution_name,), fetch=True
    )
    if not visits:
        await query.message.reply_text(
            "⚠️ لا توجد زيارات لهذه المؤسسة.",
            reply_markup=ReplyKeyboardMarkup(ADMIN_MENU_KB, resize_keyboard=True)
        )
        return

    keyboard = [
        [InlineKeyboardButton(f"{'🟢' if v[2]=='مفتوحة' else '🔴'} {v[1]}", callback_data=build_callback_data("select", v[0]))]
        for v in visits
    ]
    keyboard.insert(0, [InlineKeyboardButton("◀️ العودة للمؤسسات", callback_data="institutions_list")])

    await query.edit_message_text(
        f"🏥 <b>{institution_name}</b>\n📋 اختر زيارة لعرض الخيارات:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def _show_institutions_list(query, context: ContextTypes.DEFAULT_TYPE):
    institutions = execute_query(
        "SELECT DISTINCT institution_name FROM Visits ORDER BY institution_name ASC",
        fetch=True
    )
    if not institutions:
        await query.edit_message_text(
            "⚠️ لا توجد مؤسسات مسجلة.",
        )
        return

    context.user_data['institution_map'] = {
        str(idx): row[0]
        for idx, row in enumerate(institutions, start=1)
    }
    keyboard = [
        [InlineKeyboardButton(row[0], callback_data=build_callback_data("institution", idx))]
        for idx, row in enumerate(institutions, start=1)
    ]
    await query.edit_message_text(
        "🏥 اختر مؤسسة من القائمة أو اكتب جزءاً من اسم المؤسسة للبحث:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def _show_visits_list(update, status=None, order='DESC'):
    query_conditions = []
    params = []
    if status == 'open':
        query_conditions.append("status = 'مفتوحة'")
    elif status == 'closed':
        query_conditions.append("status = 'مغلقة'")

    condition = f"WHERE {' AND '.join(query_conditions)}" if query_conditions else ""
    visits = execute_query(
        f"SELECT id, institution_name, visit_date, status FROM Visits {condition} ORDER BY visit_date {order}, id {order}",
        fetch=True
    )

    if not visits:
        message = "⚠️ لا توجد زيارات مطابقة." if status else "⚠️ لا توجد زيارات مسجلة."
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(message)
        else:
            await update.message.reply_text(message)
        return

    keyboard = [
        [InlineKeyboardButton("🟢 مفتوحة", callback_data=build_callback_data("filter", "open")),
         InlineKeyboardButton("🔴 مغلقة", callback_data=build_callback_data("filter", "closed"))],
        [InlineKeyboardButton("📅 الأحدث", callback_data=build_callback_data("sort", "newest")),
         InlineKeyboardButton("📅 الأقدم", callback_data=build_callback_data("sort", "oldest"))],
        [InlineKeyboardButton("📋 عرض الكل", callback_data=build_callback_data("filter", "all"))]
    ]
    keyboard += [
        [InlineKeyboardButton(
            f"{'🟢' if v[3] == 'مفتوحة' else '🔴'} {v[1]} ({v[2]})",
            callback_data=build_callback_data("select", v[0])
        )]
        for v in visits
    ]
    text = "📋 <b>قائمة الزيارات:</b>"
    if status == 'open':
        text += "\n<i>عرض الزيارات المفتوحة فقط</i>"
    elif status == 'closed':
        text += "\n<i>عرض الزيارات المغلقة فقط</i>"

    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )


# ==========================================
# 6. الإحصائيات
# ==========================================
@admin_required
async def show_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_visits = execute_query("SELECT COUNT(*) FROM Visits", fetch=True)[0][0]
    open_visits = execute_query("SELECT COUNT(*) FROM Visits WHERE status='مفتوحة'", fetch=True)[0][0]
    closed_visits = execute_query("SELECT COUNT(*) FROM Visits WHERE status='مغلقة'", fetch=True)[0][0]
    total_members = execute_query("SELECT COUNT(DISTINCT user_id) FROM Visit_Members", fetch=True)[0][0]
    total_reports = execute_query("SELECT COUNT(*) FROM Reports", fetch=True)[0][0]
    total_attachments = execute_query("SELECT COUNT(*) FROM Attachments", fetch=True)[0][0]

    axis_summary = execute_query(
        "SELECT axis_name, COUNT(*) FROM Reports GROUP BY axis_name ORDER BY COUNT(*) DESC",
        fetch=True
    )
    frequent_sections = execute_query(
        "SELECT section_name, COUNT(*) FROM Reports GROUP BY section_name ORDER BY COUNT(*) DESC LIMIT 5",
        fetch=True
    )
    top_institutions = execute_query(
        "SELECT institution_name, COUNT(*) FROM Visits GROUP BY institution_name ORDER BY COUNT(*) DESC LIMIT 5",
        fetch=True
    )

    axis_text = "\n".join([f"- {row[0]}: {row[1]}" for row in axis_summary])
    sections_text = "\n".join([f"- {row[0]} ({row[1]})" for row in frequent_sections])
    institutions_text = "\n".join([f"- {row[0]} ({row[1]} زيارة)" for row in top_institutions])

    await update.message.reply_text(
        f"📊 <b>إحصائيات النظام</b>\n\n"
        f"🏥 إجمالي الزيارات: {total_visits}\n"
        f"  🟢 مفتوحة: {open_visits}\n"
        f"  🔴 مغلقة: {closed_visits}\n\n"
        f"👥 إجمالي الأعضاء المسجلين: {total_members}\n"
        f"📝 إجمالي الملاحظات: {total_reports}\n"
        f"📎 إجمالي المرفقات: {total_attachments}\n\n"
        f"📌 الملاحظات حسب المحور:\n{axis_text}\n\n"
        f"📂 أكثر الأقسام تكراراً:\n{sections_text}\n\n"
        f"🏥 أكثر المؤسسات زيارة:\n{institutions_text}",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(ADMIN_MENU_KB, resize_keyboard=True)
    )


# ==========================================
# 7. البحث
# ==========================================
@admin_required
async def search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔍 اكتب اسم المؤسسة أو جزءاً منه للبحث:",
        reply_markup=ReplyKeyboardRemove()
    )
    return SEARCH_QUERY


async def search_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = f"%{update.message.text}%"
    results = execute_query(
        "SELECT id, institution_name, visit_date, status FROM Visits WHERE institution_name LIKE ? ORDER BY id DESC",
        (query,), fetch=True
    )
    if not results:
        await update.message.reply_text(
            "⚠️ لا توجد نتائج.",
            reply_markup=ReplyKeyboardMarkup(ADMIN_MENU_KB, resize_keyboard=True)
        )
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton(
            f"{'🟢' if v[3] == 'مفتوحة' else '🔴'} {v[1]} ({v[2]})",
            callback_data=build_callback_data("select", v[0])
        )]
        for v in results
    ]
    await update.message.reply_text(
        f"🔍 نتائج البحث ({len(results)}):",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ConversationHandler.END


@admin_required
async def show_audit_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = execute_query(
        "SELECT user_name, action, target_type, target_id, details, created_at FROM Audit_Log ORDER BY created_at DESC LIMIT 20",
        fetch=True
    )
    if not rows:
        await update.message.reply_text("⚠️ لا توجد سجلات حتى الآن.")
        return

    text = "🧾 <b>سجل العمليات الأخير</b>\n\n"
    for user_name, action, target_type, target_id, details, created_at in rows:
        text += f"[{created_at}] {user_name} - {action} - {target_type} {target_id} - {details}\n"

    await update.message.reply_text(text, parse_mode="HTML")


# ==========================================
# 8. معالج الـ Callback (إدارة الزيارات)
# ==========================================
async def visit_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.callback_query.answer("⛔ ليس لديك صلاحية.", show_alert=True)
        return

    query = update.callback_query
    await query.answer()
    data = query.data
    action, payload = parse_callback_data(data)

    # --- عرض تفاصيل زيارة ---
    if action == "select":
        if not payload or not payload.isdigit():
            await query.answer("⚠️ بيانات الزيارة غير صحيحة.", show_alert=True)
            return
        await _show_visit_menu(query, payload)

    # --- تصفية الزيارات ---
    elif action == "filter":
        if payload == 'open':
            await _show_visits_list(query, status='open', order='DESC')
        elif payload == 'closed':
            await _show_visits_list(query, status='closed', order='DESC')
        else:
            await _show_visits_list(query, status=None, order='DESC')

    elif action == "institution":
        institution_name = context.user_data.get('institution_map', {}).get(payload)
        if not institution_name:
            await query.answer("⚠️ لا يمكن العثور على المؤسسة. أعد البحث.", show_alert=True)
            return
        await _show_institution_visits(query, institution_name, context)

    elif action == "institutions_list":
        await _show_institutions_list(query, context)

    elif action == "sort":
        order = 'ASC' if payload == 'oldest' else 'DESC'
        await _show_visits_list(query, status=None, order=order)

    # --- نسخ الرابط ---
    elif action == "link":
        if not payload or not payload.isdigit():
            await query.answer("⚠️ بيانات الرابط غير صحيحة.", show_alert=True)
            return
        await query.message.reply_text(
            f"🔗 <b>رابط الانضمام:</b>\n"
            f"<code>https://t.me/InspectionRusafa_bot?start=join_{payload}</code>",
            parse_mode="HTML"
        )

    # --- إغلاق الزيارة ---
    elif action == "close":
        if not payload or not payload.isdigit():
            await query.answer("⚠️ بيانات الزيارة غير صحيحة.", show_alert=True)
            return
        execute_query("UPDATE Visits SET status = 'مغلقة' WHERE id = ?", (payload,))
        await query.edit_message_text("🔒 <b>تم إغلاق الزيارة.</b>", parse_mode="HTML")
        log_action(update.effective_user.id, update.effective_user.full_name, 'close_visit', 'visit', int(payload), 'أغلق الزيارة من لوحة الإدارة')

    # --- إعادة فتح ---
    elif action == "reopen":
        if not payload or not payload.isdigit():
            await query.answer("⚠️ بيانات الزيارة غير صحيحة.", show_alert=True)
            return
        execute_query("UPDATE Visits SET status = 'مفتوحة' WHERE id = ?", (payload,))
        await query.edit_message_text("🔓 <b>تم إعادة فتح الزيارة!</b>", parse_mode="HTML")
        log_action(update.effective_user.id, update.effective_user.full_name, 'reopen_visit', 'visit', int(payload), 'أعاد فتح الزيارة من لوحة الإدارة')

    # --- ملخص نصي سريع ---
    elif action == "preview":
        if not payload or not payload.isdigit():
            await query.answer("⚠️ بيانات العرض غير صحيحة.", show_alert=True)
            return
        await _show_text_preview(query, payload)

    # --- حذف ملاحظة ---
    elif action == "del_reports":
        if not payload or not payload.isdigit():
            await query.answer("⚠️ بيانات الملاحظة غير صحيحة.", show_alert=True)
            return
        await _show_deletable_reports(query, payload)

    elif action == "delrep":
        if not payload or not payload.isdigit():
            await query.answer("⚠️ بيانات الملاحظة غير صحيحة.", show_alert=True)
            return
        await query.edit_message_text(
            "هل أنت متأكد من حذف هذه الملاحظة؟",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("نعم، احذف", callback_data=build_callback_data("confirm_delrep", payload))],
                [InlineKeyboardButton("لا، إلغاء", callback_data="cancel_action")]
            ])
        )

    elif action == "confirm_delrep":
        if not payload or not payload.isdigit():
            await query.answer("⚠️ بيانات الحذف غير صحيحة.", show_alert=True)
            return
        execute_query("DELETE FROM Reports WHERE id = ?", (payload,))
        await query.edit_message_text("🗑️ تم حذف الملاحظة.")
        log_action(update.effective_user.id, update.effective_user.full_name, 'delete_report', 'report', int(payload), 'تم حذف ملاحظة من لوحة الإدارة')

    elif action == "cancel_action":
        await query.edit_message_text("❌ تم إلغاء العملية.")

    # --- تجميع المرفقات ---
    elif action == "attachments":
        if not payload or not payload.isdigit():
            await query.answer("⚠️ بيانات المرفقات غير صحيحة.", show_alert=True)
            return
        await _send_attachments_zip(query, payload, context)

    # --- حذف الزيارة ---
    elif action == "delete":
        if not payload or not payload.isdigit():
            await query.answer("⚠️ بيانات الزيارة غير صحيحة.", show_alert=True)
            return
        await query.edit_message_text(
            "هل أنت متأكد من حذف هذه الزيارة وجميع بياناتها؟",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("نعم، احذف الزيارة", callback_data=build_callback_data("confirm_delete", payload))],
                [InlineKeyboardButton("لا، إلغاء", callback_data="cancel_action")]
            ])
        )

    elif action == "confirm_delete":
        if not payload or not payload.isdigit():
            await query.answer("⚠️ بيانات الحذف غير صحيحة.", show_alert=True)
            return
        execute_query("DELETE FROM Reports WHERE visit_id = ?", (payload,))
        execute_query("DELETE FROM Visit_Members WHERE visit_id = ?", (payload,))
        execute_query("DELETE FROM Attachments WHERE visit_id = ?", (payload,))
        execute_query("DELETE FROM Visits WHERE id = ?", (payload,))
        await query.edit_message_text("🗑️ تم حذف الزيارة وجميع بياناتها.")
        log_action(update.effective_user.id, update.effective_user.full_name, 'delete_visit', 'visit', int(payload), 'حذف زيارة ونهجها بالكامل')

    # --- إصدار التقرير ---
    elif action == "export":
        if not payload or not payload.isdigit():
            await query.answer("⚠️ بيانات الزيارة غير صحيحة.", show_alert=True)
            return
        await query.edit_message_text("🔄 جاري إنشاء التقرير...")
        file_name, inst_name = generate_docx_report(payload, bot_token=TOKEN)
        if not file_name:
            await context.bot.send_message(
                query.message.chat_id, "⚠️ لا توجد بيانات لهذه الزيارة."
            )
            return
        with open(file_name, 'rb') as f:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=f,
                filename=os.path.basename(file_name),
                caption="✅ تم إصدار التقرير."
            )
        if os.path.exists(file_name):
            os.remove(file_name)


async def _show_visit_menu(query, visit_id):
    """عرض قائمة إدارة الزيارة"""
    visit_info = execute_query(
        "SELECT institution_name, status, visit_date FROM Visits WHERE id = ?",
        (visit_id,), fetch=True
    )
    if not visit_info:
        await query.edit_message_text("⚠️ الزيارة غير موجودة.")
        return

    inst_name, status, v_date = visit_info[0]
    members_count = execute_query(
        "SELECT COUNT(*) FROM Visit_Members WHERE visit_id = ?", (visit_id,), fetch=True
    )[0][0]
    reports_count = execute_query(
        "SELECT COUNT(*) FROM Reports WHERE visit_id = ?", (visit_id,), fetch=True
    )[0][0]
    attach_count = execute_query(
        "SELECT COUNT(*) FROM Attachments WHERE visit_id = ?", (visit_id,), fetch=True
    )[0][0]

    status_text = "مفتوحة 🟢" if status == "مفتوحة" else "مغلقة 🔴"

    keyboard = [
        [InlineKeyboardButton("📄 إصدار التقرير Word", callback_data=build_callback_data("export", visit_id))],
        [InlineKeyboardButton("👁️ معاينة نصية سريعة", callback_data=build_callback_data("preview", visit_id))],
        [InlineKeyboardButton("📦 تجميع المرفقات (ZIP)", callback_data=build_callback_data("attachments", visit_id))],
        [InlineKeyboardButton("🗑️ حذف ملاحظة", callback_data=build_callback_data("del_reports", visit_id))],
    ]

    if status == "مفتوحة":
        keyboard.append([InlineKeyboardButton("🔗 نسخ الرابط", callback_data=build_callback_data("link", visit_id))])
        keyboard.append([InlineKeyboardButton("🔒 إغلاق الزيارة", callback_data=build_callback_data("close", visit_id))])
    else:
        keyboard.append([InlineKeyboardButton("🔓 إعادة فتح", callback_data=build_callback_data("reopen", visit_id))])

    keyboard.append([InlineKeyboardButton("🗑️ حذف الزيارة نهائياً", callback_data=build_callback_data("delete", visit_id))])

    await query.edit_message_text(
        f"🏥 <b>{inst_name}</b>\n"
        f"📅 التاريخ: {v_date}\n"
        f"الحالة: {status_text}\n"
        f"👥 الأعضاء: {members_count} | 📝 الملاحظات: {reports_count} | 📎 المرفقات: {attach_count}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def _show_text_preview(query, visit_id):
    """عرض ملخص نصي سريع للزيارة"""
    reports = execute_query(
        "SELECT axis_name, section_name, notes FROM Reports WHERE visit_id = ? LIMIT 15",
        (visit_id,), fetch=True
    )
    if not reports:
        await query.edit_message_text("⚠️ لا توجد ملاحظات بعد.")
        return

    text = "📋 <b>معاينة سريعة:</b>\n\n"
    for axis, section, note in reports:
        note_preview = note[:60] + "..." if len(note) > 60 else note
        text += f"<b>{axis}</b> / {section}\n↪️ {note_preview}\n\n"

    total = execute_query("SELECT COUNT(*) FROM Reports WHERE visit_id = ?", (visit_id,), fetch=True)[0][0]
    if total > 15:
        text += f"<i>... و{total - 15} ملاحظة أخرى (في التقرير الكامل)</i>"

    await query.edit_message_text(text, parse_mode="HTML")


async def _show_deletable_reports(query, visit_id):
    """عرض قائمة الملاحظات لحذف واحدة منها"""
    reports = execute_query(
        "SELECT id, axis_name, section_name FROM Reports WHERE visit_id = ? ORDER BY id DESC LIMIT 20",
        (visit_id,), fetch=True
    )
    if not reports:
        await query.edit_message_text("⚠️ لا توجد ملاحظات.")
        return

    keyboard = [
        [InlineKeyboardButton(
            f"🗑 {r[1]} / {r[2][:20]}",
            callback_data=build_callback_data("delrep", r[0])
        )]
        for r in reports
    ]
    await query.edit_message_text(
        "اختر الملاحظة التي تريد حذفها:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def _send_attachments_zip(query, visit_id, context):
    """تجميع مرفقات الزيارة وضغطها وإرسالها"""
    attachments = execute_query(
        "SELECT file_id, file_type, file_name, caption FROM Attachments WHERE visit_id = ?",
        (visit_id,), fetch=True
    )
    if not attachments:
        await query.edit_message_text("⚠️ لا توجد مرفقات لهذه الزيارة.")
        return

    await query.edit_message_text(f"📦 جاري تجميع {len(attachments)} مرفق وضغطها...")

    visit_info = execute_query("SELECT institution_name FROM Visits WHERE id = ?", (visit_id,), fetch=True)
    inst_name = visit_info[0][0] if visit_info else f"زيارة_{visit_id}"

    zip_buffer = io.BytesIO()
    errors = 0
    success_count = 0

    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, (file_id, file_type, file_name, caption) in enumerate(attachments, 1):
            try:
                # تحميل الملف من تيليغرام مع معالجة أخطاء الشبكة
                file_obj = await context.bot.get_file(file_id)
                file_url = file_obj.file_path
                
                # Retry logic مع timeout محسّن
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        response = requests.get(file_url, timeout=30)
                        response.raise_for_status()
                        file_data = response.content
                        break
                    except requests.exceptions.Timeout:
                        logger.warning(f"Timeout downloading file {file_id}, attempt {attempt + 1}/{max_retries}")
                        if attempt == max_retries - 1:
                            raise
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff
                    except requests.exceptions.RequestException as e:
                        logger.warning(f"Error downloading file {file_id}: {e}, attempt {attempt + 1}/{max_retries}")
                        if attempt == max_retries - 1:
                            raise
                        await asyncio.sleep(2 ** attempt)

                safe_name = f"{i:03d}_{file_name or file_id[:8]}"
                zf.writestr(safe_name, file_data)
                success_count += 1

                # إضافة ملف وصف إن وُجد
                if caption:
                    zf.writestr(f"{i:03d}_description.txt", caption.encode('utf-8'))
            except Exception as e:
                logger.error(f"Failed to download attachment {file_id}: {e}")
                errors += 1

    zip_buffer.seek(0)
    zip_name = f"مرفقات_{inst_name}.zip"

    await context.bot.send_document(
        chat_id=query.message.chat_id,
        document=zip_buffer,
        filename=zip_name,
        caption=f"📦 مرفقات زيارة: {inst_name}\n"
                f"✅ {success_count} ملف\n"
                f"{'⚠️ ' + str(errors) + ' ملفات تعذر تحميلها' if errors else ''}"
    )


# ==========================================
# 9. الإلغاء
# ==========================================
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if context.user_data.get('report_visit_id') and any(k in context.user_data for k in ('current_axis', 'current_section', 'current_notes', 'current_rec_dest')):
        state = context.user_data.get('current_state', AXIS_NAME)
        save_draft(user.id, context.user_data['report_visit_id'], user.full_name, state, context.user_data)
        cancel_text = "💾 تم حفظ المسودة عند الإلغاء. يمكنك العودة لاحقاً."
    else:
        cancel_text = "❌ تم الإلغاء."

    if is_admin(user.id):
        kb = ADMIN_MENU_KB
    else:
        kb = MEMBER_MENU_KB
    await update.message.reply_text(
        cancel_text,
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled exception in Telegram bot handler")
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "⚠️ حدث خطأ غير متوقع. حاول مرة أخرى لاحقاً."
            )
    except Exception:
        logger.exception("Failed to notify user about the error")


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💡 كيفية استخدام البوت:\n"
        "1. اختر المحور أو أضف مرفقاً مباشرة.\n"
        "2. إذا اخترت المعلومات العامة، اختر الحقل وأرسل القيمة.\n"
        "3. إذا اخترت محوراً آخر، اكتب الملاحظة ثم اختر جهة الإيعاز.\n"
        "4. اختر لا توجد توصية إذا لا تحتاج توجيه.\n"
        "5. يمكنك حفظ المسودة أو إنهاء الإدخال في أي وقت.\n"
        "للمساعدة أضف \"رجوع الى القائمة السابقة\" في أي شاشة للعودة بسرعة.",
        reply_markup=ReplyKeyboardMarkup(MEMBER_MENU_KB if not is_admin(update.effective_user.id) else ADMIN_MENU_KB, resize_keyboard=True)
    )


# ==========================================
# main
# ==========================================
def main():
    init_db()
    application = Application.builder().token(TOKEN).build()
    _schedule_pending_reminders(application)

    # --- معالج الـ Callback ---
    application.add_handler(visit_creator)
    application.add_handler(CallbackQueryHandler(visit_callback_handler))
    application.add_error_handler(error_handler)

    # --- إدارة الزيارات والإحصائيات ---
    application.add_handler(CommandHandler("visits", manage_visits))
    application.add_handler(MessageHandler(filters.Regex("^📋 إدارة الزيارات$"), manage_visits))
    application.add_handler(MessageHandler(filters.Regex("^📊 الإحصائيات$"), show_statistics))
    application.add_handler(CommandHandler("audit", show_audit_log))
    application.add_handler(MessageHandler(filters.Regex("^🗂 سجل العمليات$"), show_audit_log))

    # --- إنشاء زيارة (محادثة) ---
    visit_creator = ConversationHandler(
        entry_points=[
            CommandHandler('create_visit', create_visit_start),
            MessageHandler(filters.Regex("^➕ إنشاء زيارة جديدة$"), create_visit_start)
        ],
        states={
            INSTITUTION_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_institution_name)],
            VISIT_DATE:       [CallbackQueryHandler(get_visit_date, pattern="^(IGNORE|PREV:|NEXT:|DATE:)")],
            VISIT_TYPE:       [MessageHandler(filters.TEXT & ~filters.COMMAND, get_visit_type)],
            SCHEDULE_DATE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, get_schedule_date)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    # --- البحث (محادثة) ---
    search_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^🔍 البحث عن زيارة$"), search_start),
            CommandHandler("search", search_start)
        ],
        states={
            SEARCH_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, search_execute)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    application.add_handler(CommandHandler('help', show_help))

    # --- إدخال التقرير والمرفقات (محادثة) ---
    report_handler = ConversationHandler(
        allow_reentry=True,
        entry_points=[
            CommandHandler('start', start_and_join),
            MessageHandler(filters.Regex("^➕ إرسال رد آخر$"), start_another_report)
        ],
        states={
            ENTER_MEMBER_NAME:   [MessageHandler(filters.TEXT & ~filters.COMMAND, save_member_name)],
            ENTER_JOB_TITLE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, save_member_job_title)],
            DRAFT_RESUME:      [MessageHandler(filters.TEXT & ~filters.COMMAND, resume_draft_choice)],
            AXIS_NAME:         [MessageHandler(filters.TEXT & ~filters.COMMAND, get_axis_name)],
            SECTION_NAME:      [MessageHandler(filters.TEXT & ~filters.COMMAND, get_section_name)],
            NOTES:             [MessageHandler(filters.TEXT & ~filters.COMMAND, get_notes)],
            NOTE_CONFIRM:      [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_note_entry)],
            REC_DESTINATION:   [MessageHandler(filters.TEXT & ~filters.COMMAND, get_rec_destination)],
            RECOMMENDATIONS:   [MessageHandler(filters.TEXT & ~filters.COMMAND, get_recommendations)],
            LOOP_OR_END:       [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_loop_choice),
                MessageHandler(filters.PHOTO | filters.Document.ALL | filters.VIDEO, receive_attachment),
            ],
            ATTACHMENT_CAPTION: [
                MessageHandler(filters.PHOTO | filters.Document.ALL | filters.VIDEO, receive_attachment),
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_loop_choice),
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    institution_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^المؤسسات الصحية$"), manage_institutions),
            CommandHandler("institutions", manage_institutions)
        ],
        states={
            INSTITUTION_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, institution_search_execute)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    application.add_handler(search_handler)
    application.add_handler(institution_handler)
    application.add_handler(report_handler)

    print("🤖 البوت يعمل بجميع المميزات الجديدة...")
    application.run_polling()


if __name__ == '__main__':
    main()