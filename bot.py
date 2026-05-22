import os
import io
import json
import zipfile
import asyncio
import logging
import hashlib
import sqlite3
import requests
import threading
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

from database import init_db, get_connection, upsert_user_session, delete_user_data, cleanup_old_data, delete_visit
from report_generator import generate_docx_report
from web_server import app as web_app

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

# ==========================================
# حالات المحادثة
# ==========================================
INSTITUTION_NAME, VISIT_DATE, SCHEDULE_DATE = range(3)
AXIS_NAME, SECTION_NAME, NOTES, NOTE_CONFIRM, REC_DESTINATION, RECOMMENDATIONS, LOOP_OR_END, SUMMARY_CONFIRM = range(10, 18)
SEARCH_QUERY = 30
ATTACHMENT_CAPTION = 40
DRAFT_RESUME = 50

# ==========================================
# القوائم الثابتة
# ==========================================
ADMIN_MENU_KB = [
    ["➕ إنشاء زيارة جديدة", "📋 إدارة الزيارات"],
    ["📊 الإحصائيات", "🔍 البحث عن زيارة"],
    ["🗂 سجل العمليات"]
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
            cursor.execute(query, params)
            if fetch:
                return cursor.fetchall()
            conn.commit()
    except sqlite3.Error as e:
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
        existing = execute_query(
            "SELECT * FROM Visit_Members WHERE visit_id = ? AND user_id = ?",
            (visit_id, user.id), fetch=True
        )
        if not existing:
            execute_query(
                "INSERT INTO Visit_Members (visit_id, user_id, user_name) VALUES (?, ?, ?)",
                (visit_id, user.id, user.full_name)
            )
            # 🔔 إشعار المدير بانضمام عضو جديد
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        admin_id,
                        f"🔔 <b>عضو جديد انضم لزيارة</b>\n"
                        f"👤 الاسم: {user.full_name}\n"
                        f"🏥 الزيارة: {institution_name}",
                        parse_mode="HTML"
                    )
                except TelegramError as e:
                    logger.warning(f"Failed to notify admin {admin_id}: {e}")
                except Exception as e:
                    logger.error(f"Unexpected error notifying admin {admin_id}: {e}")

        context.user_data['report_visit_id'] = visit_id
        draft = load_draft(user.id, visit_id)
        if draft:
            context.user_data['pending_draft'] = draft
            await update.message.reply_text(
                f"✅ تم دخولك إلى زيارة: <b>{institution_name}</b>\n\n"
                "لديك مسودة محفوظة. هل تريد استكمالها أم البدء جديداً؟",
                reply_markup=ReplyKeyboardMarkup(
                    [["استكمال المسودة"], ["بدء جديد"]],
                    one_time_keyboard=True, resize_keyboard=True
                ),
                parse_mode="HTML"
            )
            return DRAFT_RESUME

        await update.message.reply_text(
            f"✅ تم دخولك إلى زيارة: <b>{institution_name}</b>\n\n"
            f"اختر <b>المحور</b> أو أضف مرفقاً:",
            reply_markup=ReplyKeyboardMarkup(REPORT_START_KB, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="HTML"
        )
        return AXIS_NAME

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
# 2. خطوات إدخال التقرير
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
        await _notify_admins_report(context, update.effective_user.full_name,
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
    await _notify_admins_report(context, update.effective_user.full_name,
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


async def _notify_admins_report(context, user_name, visit_id, axis, section):
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
                f"👤 المُرسل: {user_name}\n"
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
    context.user_data['inst_name'] = update.message.text.strip()
    await update.message.reply_text("📅 أرسل تاريخ الزيارة بصيغة YYYY-MM-DD (مثال: 2025-06-01):")
    return VISIT_DATE


async def get_visit_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        visit_dt = datetime.strptime(text, "%Y-%m-%d")
        context.user_data['visit_date'] = visit_dt.strftime("%Y-%m-%d")
    except ValueError:
        await update.message.reply_text("⚠️ صيغة التاريخ غير صحيحة. أرسل التاريخ بصيغة YYYY-MM-DD:")
        return VISIT_DATE

    await update.message.reply_text(
        "⏰ هل تريد جدولة تذكير لهذه الزيارة؟\n"
        "أرسل تاريخ ووقت التذكير بصيغة YYYY-MM-DD HH:MM (مثال: 2025-06-01 08:00)\n"
        "أو أرسل (لا) للتخطي:",
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

    conn = sqlite3.connect('inspection_db.sqlite')
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO Visits (institution_name, visit_date, manager_id, status, scheduled_date) VALUES (?, ?, ?, 'مفتوحة', ?)",
        (inst_name, visit_date, update.effective_user.id, scheduled_date)
    )
    visit_id = cursor.lastrowid
    conn.commit()
    conn.close()

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

    # الحصول على URL الخادم من متغيرات البيئة أو استخدام localhost للتطوير
    WEB_URL = os.getenv('RAILWAY_PUBLIC_DOMAIN', 'http://localhost:8080')
    link = f"{WEB_URL}/join/{visit_id}/{user.id}"
    await update.message.reply_text(
        f"✅ <b>تم إنشاء الزيارة!</b>\n\n"
        f"🏥 المؤسسة: {inst_name}\n"
        f"📅 التاريخ: {visit_date}\n"
        f"{'⏰ تذكير مجدول: ' + scheduled_date if scheduled_date else ''}\n\n"
        f"🔗 <b>رابط الانضمام:</b>\n<code>{link}</code>\n\n⚠️ <b>ملاحظة:</b> عند فتح الرابط، سيُطلب من العضو إدخال اسمه الثلاثي قبل الانضمام.",
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
                f"<code>{WEB_URL}/join/{data['visit_id']}</code>\n\n⚠️ <b>ملاحظة:</b> عند فتح الرابط، سيُطلب من العضو إدخال اسمه الثلاثي قبل الانضمام.",
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
        [InlineKeyboardButton("🟢 مفتوحة", callback_data="filter_open"),
         InlineKeyboardButton("🔴 مغلقة", callback_data="filter_closed")],
        [InlineKeyboardButton("📅 الأحدث", callback_data="sort_newest"),
         InlineKeyboardButton("📅 الأقدم", callback_data="sort_oldest")],
        [InlineKeyboardButton("📋 عرض الكل", callback_data="filter_all")]
    ]
    keyboard += [
        [InlineKeyboardButton(
            f"{'🟢' if v[3] == 'مفتوحة' else '🔴'} {v[1]} ({v[2]})",
            callback_data=f"select_{v[0]}"
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
            callback_data=f"select_{v[0]}"
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

    # --- عرض تفاصيل زيارة ---
    if data.startswith("select_"):
        visit_id = data.split("_")[1]
        await _show_visit_menu(query, visit_id)

    # --- تصفية الزيارات ---
    elif data.startswith("filter_"):
        filter_type = data.split("_")[1]
        if filter_type == 'open':
            await _show_visits_list(query, status='open', order='DESC')
        elif filter_type == 'closed':
            await _show_visits_list(query, status='closed', order='DESC')
        else:
            await _show_visits_list(query, status=None, order='DESC')

    elif data.startswith("sort_"):
        order = 'ASC' if data == 'sort_oldest' else 'DESC'
        await _show_visits_list(query, status=None, order=order)

    # --- نسخ الرابط ---
    elif data.startswith("link_"):
        visit_id = data.split("_")[1]
        await query.message.reply_text(
            f"🔗 <b>رابط الانضمام:</b>\n"
            f"<code>{WEB_URL}/join/{visit_id}</code>\n\n⚠️ <b>ملاحظة:</b> عند فتح الرابط، سيُطلب من العضو إدخال اسمه الثلاثي قبل الانضمام.",
            parse_mode="HTML"
        )

    # --- إغلاق الزيارة ---
    elif data.startswith("close_"):
        visit_id = data.split("_")[1]
        execute_query("UPDATE Visits SET status = 'مغلقة' WHERE id = ?", (visit_id,))
        await query.edit_message_text("🔒 <b>تم إغلاق الزيارة.</b>", parse_mode="HTML")
        log_action(update.effective_user.id, update.effective_user.full_name, 'close_visit', 'visit', int(visit_id), 'أغلق الزيارة من لوحة الإدارة')

    # --- إعادة فتح ---
    elif data.startswith("reopen_"):
        visit_id = data.split("_")[1]
        execute_query("UPDATE Visits SET status = 'مفتوحة' WHERE id = ?", (visit_id,))
        await query.edit_message_text("🔓 <b>تم إعادة فتح الزيارة!</b>", parse_mode="HTML")
        log_action(update.effective_user.id, update.effective_user.full_name, 'reopen_visit', 'visit', int(visit_id), 'أعاد فتح الزيارة من لوحة الإدارة')

    # --- ملخص نصي سريع ---
    elif data.startswith("preview_"):
        visit_id = data.split("_")[1]
        await _show_text_preview(query, visit_id)

    # --- حذف ملاحظة ---
    elif data.startswith("del_reports_"):
        visit_id = data.split("_")[2]
        await _show_deletable_reports(query, visit_id)

    elif data.startswith("delrep_"):
        report_id = data.split("_")[1]
        await query.edit_message_text(
            "هل أنت متأكد من حذف هذه الملاحظة؟",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("نعم، احذف", callback_data=f"confirm_delrep_{report_id}" )],
                [InlineKeyboardButton("لا، إلغاء", callback_data="cancel_action")]
            ])
        )

    elif data.startswith("confirm_delrep_"):
        report_id = data.split("_")[1]
        execute_query("DELETE FROM Reports WHERE id = ?", (report_id,))
        await query.edit_message_text("🗑️ تم حذف الملاحظة.")
        log_action(update.effective_user.id, update.effective_user.full_name, 'delete_report', 'report', int(report_id), 'تم حذف ملاحظة من لوحة الإدارة')

    elif data == "cancel_action":
        await query.edit_message_text("❌ تم إلغاء العملية.")

    # --- تجميع المرفقات ---
    elif data.startswith("attachments_"):
        visit_id = data.split("_")[1]
        await _send_attachments_zip(query, visit_id, context)

    # --- حذف الزيارة ---
    elif data.startswith("delete_"):
        visit_id = data.split("_")[1]
        await query.edit_message_text(
            "هل أنت متأكد من حذف هذه الزيارة وجميع بياناتها؟",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("نعم، احذف الزيارة", callback_data=f"confirm_delete_{visit_id}" )],
                [InlineKeyboardButton("لا، إلغاء", callback_data="cancel_action")]
            ])
        )

    elif data.startswith("confirm_delete_"):
        visit_id = data.split("_")[1]
        # استخدام دالة الحذف الموحدة من database.py
        success = delete_visit(visit_id)
        if success:
            await query.edit_message_text("🗑️ تم حذف الزيارة وجميع بياناتها.")
            log_action(update.effective_user.id, update.effective_user.full_name, 'delete_visit', 'visit', int(visit_id), 'حذف زيارة ونهجها بالكامل')
        else:
            await query.edit_message_text("❌ فشل حذف الزيارة.")

    # --- إصدار التقرير ---
    elif data.startswith("export_"):
        visit_id = data.split("_")[1]
        await query.edit_message_text("🔄 جاري إنشاء التقرير...")
        file_name, inst_name = generate_docx_report(visit_id, bot_token=TOKEN)
        if not file_name:
            await context.bot.send_message(
                query.message.chat_id, "⚠️ لا توجد بيانات لهذه الزيارة."
            )
            return
        with open(file_name, 'rb') as f:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=f,
                filename=f"تقرير {inst_name}.docx",
                caption="✅ تم إصدار التقرير وإغلاق الزيارة."
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
        [InlineKeyboardButton("📄 إصدار التقرير Word", callback_data=f"export_{visit_id}")],
        [InlineKeyboardButton("👁️ معاينة نصية سريعة", callback_data=f"preview_{visit_id}")],
        [InlineKeyboardButton("📦 تجميع المرفقات (ZIP)", callback_data=f"attachments_{visit_id}")],
        [InlineKeyboardButton("🗑️ حذف ملاحظة", callback_data=f"del_reports_{visit_id}")],
    ]

    if status == "مفتوحة":
        keyboard.append([InlineKeyboardButton("🔗 نسخ الرابط", callback_data=f"link_{visit_id}")])
        keyboard.append([InlineKeyboardButton("🔒 إغلاق الزيارة", callback_data=f"close_{visit_id}")])
    else:
        keyboard.append([InlineKeyboardButton("🔓 إعادة فتح", callback_data=f"reopen_{visit_id}")])

    keyboard.append([InlineKeyboardButton("🗑️ حذف الزيارة نهائياً", callback_data=f"delete_{visit_id}")])

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
            callback_data=f"delrep_{r[0]}"
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
    application.add_handler(CallbackQueryHandler(visit_callback_handler))

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
            VISIT_DATE:       [MessageHandler(filters.TEXT & ~filters.COMMAND, get_visit_date)],
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
        entry_points=[
            CommandHandler('start', start_and_join),
            MessageHandler(filters.Regex("^➕ إرسال رد آخر$"), start_another_report)
        ],
        states={
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

    application.add_handler(visit_creator)
    application.add_handler(search_handler)
    application.add_handler(report_handler)

    print("🤖 البوت يعمل بجميع المميزات الجديدة...")
    application.run_polling()


if __name__ == '__main__':
    main()