# دليل الترقية إلى PostgreSQL والنسخة المحسنة

## 📋 ملخص التحسينات

تم تطبيق التحسينات التالية:

### 1. قاعدة البيانات PostgreSQL مع Connection Pooling
- ✅ استخدام `psycopg_pool` لإدارة اتصالات فعالة
- ✅ دعم SQLite كخيار للتطوير المحلي
- ✅ فهارس وتحسين استعلامات
- ✅ هجرات تلقائية عند البدء

### 2. فصل Business Logic عن Presentation Layer
- ✅ مجلد `services/` يحتوي على:
  - `business_logic.py` - منطق الأعمال الرئيسي
  - `config_service.py` - إدارة الإعدادات المركزية
  - `message_queue.py` - طابور الرسائل Redis

### 3. معالجة Global State
- ✅ فئة `SessionContext` تحل محل `context.user_data`
- ✅ كل مستخدم له سياق معزول وآمن للخيوط
- ✅ قفل غير متزامن لمنع التعارضات

### 4. Dependency Injection
- ✅ فئة `BotApplication` تقبل جميع التبعيات في المنشئ
- ✅ سهولة الاختبار باستخدام تبعيات وهمية
- ✅ مصنع `create_bot_app()` لتهيئة سهلة

### 5. Message Queue و Multi-Instance
- ✅ دعم Redis للطابور والنشر/الاشتراك
- ✅ تنسيق بين النسخ المتعددة عبر أقفال موزعة
- ✅ مثيل واحد يعالج الإشعارات (Leader election)

### 6. تحسين الأداء
- ✅ إشعارات متوازية للمدراء (`asyncio.gather`)
- ✅ كاش ثابت للقوائم (AXES_LIST, DESTINATIONS_LIST)
- ✅ تنظيف دوري للكاش كل ساعة
- ✅ توليد تقارير غير حاجز (في الخلفية)

### 7. Webhook Support
- ✅ دعم وضعي Polling و Webhook
- ✅ تهيئة تلقائية حسب متغير `WEBHOOK_URL`

### 8. Health Check & Metrics
- ✅ أمر `/health` لفحص صحة المكونات
- ✅ أمر `/stats` للإحصائيات
- ✅ نقطة نهاية Prometheus (اختياري)

---

## 🚀 التثبيت على Railway

### الخطوة 1: إضافة متغيرات البيئة

في لوحة تحكم Railway، أضف المتغيرات التالية:

```bash
# Telegram
TOKEN=your_bot_token_here
ADMIN_IDS=123456789,987654321

# Database (PostgreSQL من Railway)
DATABASE_URL=postgresql://user:password@host.railway.internal:5432/database_name

# Redis (اختياري - لـ Multi-Instance)
REDIS_URL=redis://redis.railway.internal:6379

# Cache Settings
CACHE_TTL=300
CACHE_MAX_SIZE=1000

# Optional: Webhook
WEBHOOK_URL=https://your-domain.railway.app
```

### الخطوة 2: تحديث Procfile

```procfile
worker: python bot_v2.py
```

أو لوضع Webhook:

```procfile
web: python bot_v2.py
```

### الخطوة 3: تثبيت المتطلبات

```bash
pip install -r requirements.txt
```

Railway سيفعل ذلك تلقائياً عند الدفع.

---

## 📁 هيكل الملفات الجديد

```
/workspace/
├── bot_v2.py                 # النسخة المحسنة من البوت
├── database.py               # PostgreSQL مع pooling
├── cache_manager.py          # نظام الكاش
├── report_generator.py       # توليد التقارير
├── config.py                 # الإعدادات القديمة (للخلفية)
├── services/
│   ├── __init__.py
│   ├── business_logic.py     # منطق الأعمال
│   ├── config_service.py     # خدمة الإعدادات
│   └── message_queue.py      # طابور Redis
├── tests/
│   └── test_bot.py
├── requirements.txt          # المتطلبات المحدثة
├── Procfile                  # تكوين Railway
└── README_RAILWAY.md         # هذا الملف
```

---

## 🔧 التكوين

### استخدام SQLite (للتطوير المحلي)

```bash
# لا تضع DATABASE_URL أو اجعله فارغاً
DATABASE_URL=
```

### استخدام PostgreSQL (للإنتاج)

```bash
DATABASE_URL=postgresql://user:pass@host:5432/dbname
```

### تمكين Multi-Instance

```bash
REDIS_URL=redis://host:6379
```

---

## 🧪 الاختبار

### اختبار محلي مع SQLite

```bash
python bot_v2.py
```

### اختبار مع PostgreSQL محلي

```bash
export DATABASE_URL=postgresql://localhost:5432/inspection_bot
python bot_v2.py
```

### اختبار الوحدة

```bash
pytest tests/ -v
```

---

## 📊 المراقبة

### فحص الصحة

أرسل `/health` للبوت (للمدراء فقط):

```
🏥 Health Check
الحالة: healthy
النسخة: bot_a1b2c3d4
الوقت: 2024-01-15T10:30:00
```

### الإحصائيات

أرسل `/stats` للبوت (للمدراء فقط):

```
📊 إحصائيات النظام

الكاش:
• الحجم: 45/1000
• Hit Rate: 87.5%

قاعدة البيانات:
• النوع: postgresql
• متصلة: ✅
• زمن الاستجابة: 12.5ms

طابور الرسائل:
• متاح: ✅
```

---

## 🔐 الأمان

- ✅ تشفير معرفات المسؤولين
- ✅ معاملات قاعدة بيانات آمنة
- ✅ حماية من SQL Injection
- ✅ عزل جلسات المستخدمين

---

## ⚠️ ملاحظات مهمة

1. **الهجرة من SQLite إلى PostgreSQL**:
   - البيانات لن تُهاجر تلقائياً
   - استخدم أداة مثل `pgloader` أو صدّر يدوياً

2. **Redis اختياري**:
   - بدون Redis: يعمل البوت بنسخة واحدة
   - مع Redis: يدعم نسخ متعددة وتنسيق

3. **Webhook vs Polling**:
   - Polling: أسهل للإعداد
   - Webhook: أفضل للأداء مع حركة عالية

---

## 🆘 الدعم

إذا واجهت مشاكل:

1. تحقق من السجلات: `railway logs`
2. تأكد من متغيرات البيئة
3. اختبر الاتصال بقاعدة البيانات
4. تحقق من اتصال Redis (إذا مُفعّل)
