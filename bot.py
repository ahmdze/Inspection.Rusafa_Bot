import os
import sqlite3
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
    ConversationHandler
)

from report_generator import generate_docx_report

TOKEN = "8767469034:AAGwQ5iDI5rRH6RWxJJAxZDXvwPzU54gZiw" # 🔴 ضع التوكن الخاص بك هنا
ADMIN_IDS = [5372786095, ] # 🔴 ضع أرقام المدراء هنا

def ensure_db_schema():
    conn = sqlite3.connect('inspection_db.sqlite')
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE Reports ADD COLUMN rec_destination TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

# --- حالات المحادثة والقوائم ---
INSTITUTION_NAME, VISIT_DATE = range(2)
AXIS_NAME, SECTION_NAME, NOTES, REC_DESTINATION, RECOMMENDATIONS, LOOP_OR_END = range(10, 16)

# --- الكيبوردات الثابتة ---
ADMIN_MENU_KB = [["➕ إنشاء زيارة جديدة", "📋 إدارة الزيارات"]]
MEMBER_MENU_KB = [["➕ إرسال رد آخر"]]

AXES_LIST = [["المعلومات العامة"], ["المحور الفني"], ["المحور الإداري"], ["المحور الهندسي"]]
GENERAL_INFO_KB = [
    ["المدير:", "الرديف (للمدير):"],
    ["المعاون الاداري (مسؤول الوحدة الادارية):", "الرديف (للمعاون):"],
    ["عدد الملاك الكلي:", "عدد الملاك الفعلي:"],
    ["عدد الاطباء الكلي:", "عدد الاطباء الفعلي:"],
    ["عدد اطباء الاسنان الكلي:", "عدد اطباء الاسنان الفعلي:"],
    ["عدد الصيادلة الكلي:", "عدد الصيادلة الفعلي:"],
    ["عدد النفوس:", "عدد العوائل:"]
]
DESTINATIONS_LIST = [
    ["الإيعاز الى ادارة المستشفى بما يلي:"],
    ["الإيعاز الى ادارة القطاع بما يلي:"],
    ["الإيعاز الى ادارة المركز بما يلي:"],
    ["الإيعاز الى قسم الامور الادارية والقانونية والمالية بما يلي:"],
    ["الإيعاز الى شعبة التحقيقات/ قسمنا بما يلي:"]
]

def execute_query(query, params=(), fetch=False):
    conn = sqlite3.connect('inspection_db.sqlite')
    cursor = conn.cursor()
    cursor.execute(query, params)
    if fetch:
        result = cursor.fetchall()
        conn.close()
        return result
    conn.commit()
    conn.close()


# ==========================================
# 1. أوامر الأعضاء والترحيب
# ==========================================
async def start_and_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args 

    if args and args[0].startswith("join_"):
        visit_id = args[0].replace("join_", "")
        visit = execute_query("SELECT institution_name, status FROM Visits WHERE id = ?", (visit_id,), fetch=True)
        
        if not visit:
            await update.message.reply_text("⚠️ عذراً، هذه الزيارة غير موجودة.")
            return ConversationHandler.END
            
        if visit[0][1] == 'مغلقة':
            await update.message.reply_text("🔒 عذراً، تم إغلاق هذه الزيارة من قبل الإدارة.")
            return ConversationHandler.END

        institution_name = visit[0][0]
        existing_member = execute_query("SELECT * FROM Visit_Members WHERE visit_id = ? AND user_id = ?", (visit_id, user.id), fetch=True)
        if not existing_member:
            execute_query("INSERT INTO Visit_Members (visit_id, user_id, user_name) VALUES (?, ?, ?)", (visit_id, user.id, user.full_name))
        
        context.user_data['report_visit_id'] = visit_id
        await update.message.reply_text(
            f"✅ تم دخولك إلى زيارة: <b>{institution_name}</b>\n\nالخطوة 1: يرجى اختيار <b>(المحور)</b> المطلوب إدخاله:",
            reply_markup=ReplyKeyboardMarkup(AXES_LIST, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="HTML"
        )
        return AXIS_NAME
    else:
        if user.id in ADMIN_IDS:
            await update.message.reply_text(
                "مرحباً سيدي المدير 👨‍💼\nاستخدم الأزرار في الأسفل لإدارة الزيارات.",
                reply_markup=ReplyKeyboardMarkup(ADMIN_MENU_KB, resize_keyboard=True)
            )
        else:
            await update.message.reply_text(
                "مرحباً بك في بوت التفتيش 🏛\nيمكنك استخدام الزر بالأسفل لاستئناف الإدخال للزيارة الحالية.",
                reply_markup=ReplyKeyboardMarkup(MEMBER_MENU_KB, resize_keyboard=True)
            )
        return ConversationHandler.END

async def start_another_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    visit = execute_query("""
        SELECT V.id, V.institution_name 
        FROM Visits V 
        JOIN Visit_Members M ON V.id = M.visit_id 
        WHERE M.user_id = ? AND V.status = 'مفتوحة'
        ORDER BY V.id DESC LIMIT 1
    """, (user_id,), fetch=True)
    
    if not visit:
        await update.message.reply_text("⚠️ أنت لست منضماً لأي زيارة تفتيشية مفتوحة حالياً. يرجى استخدام رابط الدعوة الجديد.")
        return ConversationHandler.END
        
    visit_id, institution_name = visit[0]
    context.user_data['report_visit_id'] = visit_id
    
    await update.message.reply_text(
        f"✅ استئناف الإدخال لزيارة: <b>{institution_name}</b>\n\nالخطوة 1: يرجى اختيار <b>(المحور)</b> المطلوب إدخاله:",
        reply_markup=ReplyKeyboardMarkup(AXES_LIST, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="HTML"
    )
    return AXIS_NAME


async def get_axis_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    axis = update.message.text
    if [axis] not in AXES_LIST:
        await update.message.reply_text("⚠️ يرجى اختيار محور صحيح:", reply_markup=ReplyKeyboardMarkup(AXES_LIST, resize_keyboard=True))
        return AXIS_NAME
        
    context.user_data['current_axis'] = axis
    if axis == "المعلومات العامة":
        await update.message.reply_text("📋 اختر الحقل الذي تود إدخال معلومته:", reply_markup=ReplyKeyboardMarkup(GENERAL_INFO_KB, resize_keyboard=True))
    else:
        await update.message.reply_text(f"📂 ممتاز. اكتب الآن اسم <b>(القسم)</b>:", reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
    return SECTION_NAME

async def get_section_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['current_section'] = update.message.text
    if context.user_data['current_axis'] == "المعلومات العامة":
        await update.message.reply_text(f"🔢 أدخل القيمة أو الاسم الخاص بـ <b>({context.user_data['current_section']})</b>:", reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
    else:
        await update.message.reply_text("🔍 الخطوة 3: أرسل الآن جميع <b>(الملاحظات التفتيشية)</b> لهذا القسم:", parse_mode="HTML")
    return NOTES

async def get_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['current_notes'] = update.message.text
    if context.user_data['current_axis'] == "المعلومات العامة":
        execute_query("INSERT INTO Reports (visit_id, user_id, axis_name, section_name, notes, rec_destination, recommendations) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (context.user_data['report_visit_id'], update.effective_user.id, context.user_data['current_axis'], context.user_data['current_section'], context.user_data['current_notes'], "", ""))
        await update.message.reply_text("✅ تم حفظ المعلومة!\nهل تود إضافة حقل/قسم آخر لهذه الزيارة؟", reply_markup=ReplyKeyboardMarkup([["➕ إضافة قسم آخر"], ["🛑 إنهاء الإدخال"]], one_time_keyboard=True, resize_keyboard=True))
        return LOOP_OR_END
    
    await update.message.reply_text("🎯 الخطوة 4: لمن تود توجيه التوصية؟:", reply_markup=ReplyKeyboardMarkup(DESTINATIONS_LIST, one_time_keyboard=True, resize_keyboard=True))
    return REC_DESTINATION

async def get_rec_destination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dest = update.message.text
    if [dest] not in DESTINATIONS_LIST: return REC_DESTINATION
    context.user_data['current_rec_dest'] = dest
    await update.message.reply_text("💡 الخطوة 5: اكتب الآن <b>نص (التوصية والحل المقترح)</b>:", reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
    return RECOMMENDATIONS

async def get_recommendations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    execute_query("INSERT INTO Reports (visit_id, user_id, axis_name, section_name, notes, rec_destination, recommendations) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (context.user_data['report_visit_id'], update.effective_user.id, context.user_data['current_axis'], context.user_data['current_section'], context.user_data['current_notes'], context.user_data['current_rec_dest'], update.message.text))
    await update.message.reply_text("📥 تم الحفظ!\n\nهل تود إدخال ملاحظات خاصة بقسم/محور آخر؟", reply_markup=ReplyKeyboardMarkup([["➕ إضافة قسم آخر"], ["🛑 إنهاء الإدخال"]], one_time_keyboard=True, resize_keyboard=True))
    return LOOP_OR_END

async def process_loop_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "➕ إضافة قسم آخر":
        await update.message.reply_text("اختر <b>المحور</b> للقسم الجديد:", reply_markup=ReplyKeyboardMarkup(AXES_LIST, resize_keyboard=True), parse_mode="HTML")
        return AXIS_NAME
    else:
        if update.effective_user.id in ADMIN_IDS:
            await update.message.reply_text("✅ تم إنهاء جلستك. شكراً لجهودكم.", reply_markup=ReplyKeyboardMarkup(ADMIN_MENU_KB, resize_keyboard=True))
        else:
            await update.message.reply_text("✅ تم إرسال تقريرك للقسم بنجاح.", reply_markup=ReplyKeyboardMarkup(MEMBER_MENU_KB, resize_keyboard=True))
        return ConversationHandler.END


# ==========================================
# 2. أوامر المدير (محمية بقائمة ADMIN_IDS)
# ==========================================
async def create_visit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END 
        
    await update.message.reply_text("✏️ يرجى كتابة اسم المؤسسة الصحية:", reply_markup=ReplyKeyboardRemove())
    return INSTITUTION_NAME

async def get_institution_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['inst_name'] = update.message.text
    await update.message.reply_text("📅 أرسل تاريخ الزيارة:")
    return VISIT_DATE

async def get_visit_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    inst_name = context.user_data['inst_name']
    conn = sqlite3.connect('inspection_db.sqlite')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO Visits (institution_name, visit_date, manager_id, status) VALUES (?, ?, ?, 'مفتوحة')", 
                   (inst_name, update.message.text, update.effective_user.id))
    visit_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    link = f"https://t.me/InspectionRusafa_bot?start=join_{visit_id}"
    await update.message.reply_text(
        f"✅ <b>تم إنشاء الزيارة!</b>\n\n🔗 <b>الرابط:</b>\n<code>{link}</code>", 
        reply_markup=ReplyKeyboardMarkup(ADMIN_MENU_KB, resize_keyboard=True),
        parse_mode="HTML"
    )
    return ConversationHandler.END

async def manage_visits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return 
        
    visits = execute_query("SELECT id, institution_name, visit_date, status FROM Visits ORDER BY id DESC", fetch=True)
    if not visits:
        await update.message.reply_text("لا توجد زيارات مسجلة.")
        return
        
    keyboard = [[InlineKeyboardButton(f"{'🟢' if v[3] == 'مفتوحة' else '🔴'} {v[1]} - ({v[2]})", callback_data=f"select_{v[0]}")] for v in visits]
    await update.message.reply_text("📋 <b>قائمة الزيارات:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def visit_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.callback_query.answer("ليس لديك صلاحية.", show_alert=True)
        return
        
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith("select_"):
        visit_id = data.split("_")[1]
        visit_info = execute_query("SELECT institution_name, status FROM Visits WHERE id = ?", (visit_id,), fetch=True)[0]
        status = visit_info[1]
        
        # الأزرار الأساسية (توليد التقرير والحذف)
        keyboard = [
            [InlineKeyboardButton("📄 إصدار التقرير بملف Word", callback_data=f"export_{visit_id}")],
            [InlineKeyboardButton("🗑️ حذف الزيارة", callback_data=f"delete_{visit_id}")]
        ]
        
        # تغيير الأزرار بناءً على حالة الزيارة
        if status == 'مفتوحة':
            # إذا كانت مفتوحة نظهر زر نسخ الرابط
            keyboard.insert(0, [InlineKeyboardButton("🔗 نسخ الرابط", callback_data=f"link_{visit_id}")])
            status_text = 'مفتوحة 🟢'
        else:
            # إذا كانت مغلقة نظهر زر إعادة الفتح
            keyboard.insert(0, [InlineKeyboardButton("🔓 إعادة فتح الزيارة", callback_data=f"reopen_{visit_id}")])
            status_text = 'مغلقة 🔴'

        await query.edit_message_text(f"إدارة زيارة: <b>{visit_info[0]}</b>\nالحالة: {status_text}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        
    elif data.startswith("link_"):
        visit_id = data.split("_")[1]
        await query.message.reply_text(f"🔗 <b>الرابط:</b>\n<code>https://t.me/InspectionRusafa_bot?start=join_{visit_id}</code>", parse_mode="HTML")
        
    elif data.startswith("reopen_"):
        visit_id = data.split("_")[1]
        execute_query("UPDATE Visits SET status = 'مفتوحة' WHERE id = ?", (visit_id,))
        await query.edit_message_text("🔓 <b>تم إعادة فتح الزيارة بنجاح!</b>\n\nيمكن للأعضاء الآن استئناف إضافة الملاحظات عبر الرابط.", parse_mode="HTML")
        
    elif data.startswith("delete_"):
        visit_id = data.split("_")[1]
        execute_query("DELETE FROM Reports WHERE visit_id = ?", (visit_id,))
        execute_query("DELETE FROM Visit_Members WHERE visit_id = ?", (visit_id,))
        execute_query("DELETE FROM Visits WHERE id = ?", (visit_id,))
        await query.edit_message_text("🗑️ تم الحذف بنجاح.")
        
    elif data.startswith("export_"):
        visit_id = data.split("_")[1]
        await query.edit_message_text("🔄 جاري بناء الملف واستخراجه من المولد...")
        
        file_name, inst_name = generate_docx_report(visit_id)
        if not file_name:
            await context.bot.send_message(query.message.chat_id, "⚠️ لا توجد بيانات مسجلة لهذه الزيارة.")
            return
            
        with open(file_name, 'rb') as document_file:
            await context.bot.send_document(
                chat_id=query.message.chat_id, 
                document=document_file, 
                filename=f"تقرير {inst_name}.docx",
                caption="✅ تم إصدار التقرير بنجاح وإغلاق الزيارة."
            )
        if os.path.exists(file_name): os.remove(file_name)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id in ADMIN_IDS:
        await update.message.reply_text("❌ تم الإلغاء.", reply_markup=ReplyKeyboardMarkup(ADMIN_MENU_KB, resize_keyboard=True))
    else:
        await update.message.reply_text("❌ تم الإلغاء.", reply_markup=ReplyKeyboardMarkup(MEMBER_MENU_KB, resize_keyboard=True))
    return ConversationHandler.END


def main():
    ensure_db_schema()
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("visits", manage_visits))
    application.add_handler(MessageHandler(filters.Regex("^📋 إدارة الزيارات$"), manage_visits))
    application.add_handler(CallbackQueryHandler(visit_callback_handler))
    
    visit_creator = ConversationHandler(
        entry_points=[
            CommandHandler('create_visit', create_visit_start),
            MessageHandler(filters.Regex("^➕ إنشاء زيارة جديدة$"), create_visit_start)
        ],
        states={
            INSTITUTION_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_institution_name)],
            VISIT_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_visit_date)],
        }, fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    report_handler = ConversationHandler(
        entry_points=[
            CommandHandler('start', start_and_join),
            MessageHandler(filters.Regex("^➕ إرسال رد آخر$"), start_another_report)
        ],
        states={
            AXIS_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_axis_name)],
            SECTION_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_section_name)],
            NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_notes)],
            REC_DESTINATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_rec_destination)],
            RECOMMENDATIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_recommendations)],
            LOOP_OR_END: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_loop_choice)],
        }, fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    application.add_handler(visit_creator)
    application.add_handler(report_handler)
    print("🤖 البوت يعمل... (ميزة إعادة فتح الزيارة مفعلة)")
    application.run_polling()

if __name__ == '__main__':
    main()