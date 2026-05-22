"""
خادم ويب Flask لاستقبال الاسم الثلاثي من الأعضاء
"""
import os
from flask import Flask, request, jsonify, render_template_string
from database import update_member_full_name, get_connection, USE_POSTGRES

app = Flask(__name__)

# HTML template لصفحة إدخال الاسم
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>تسجيل الاسم الثلاثي</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            margin: 0;
            padding: 20px;
        }
        .container {
            background: white;
            padding: 40px;
            border-radius: 15px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            max-width: 400px;
            width: 100%;
            text-align: center;
        }
        h1 {
            color: #333;
            margin-bottom: 10px;
            font-size: 24px;
        }
        .subtitle {
            color: #666;
            margin-bottom: 30px;
            font-size: 14px;
        }
        .input-group {
            margin-bottom: 20px;
            text-align: right;
        }
        label {
            display: block;
            margin-bottom: 8px;
            color: #333;
            font-weight: bold;
        }
        input[type="text"] {
            width: 100%;
            padding: 12px 15px;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            font-size: 16px;
            box-sizing: border-box;
            transition: border-color 0.3s;
        }
        input[type="text"]:focus {
            outline: none;
            border-color: #667eea;
        }
        button {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 15px 30px;
            border-radius: 8px;
            font-size: 16px;
            cursor: pointer;
            width: 100%;
            font-weight: bold;
            transition: transform 0.2s;
        }
        button:hover {
            transform: translateY(-2px);
        }
        button:disabled {
            opacity: 0.6;
            cursor: not-allowed;
        }
        .message {
            margin-top: 20px;
            padding: 15px;
            border-radius: 8px;
            display: none;
        }
        .success {
            background: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        .error {
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        .info {
            background: #d1ecf1;
            color: #0c5460;
            border: 1px solid #bee5eb;
        }
        .spinner {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 3px solid #f3f3f3;
            border-top: 3px solid #667eea;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-left: 10px;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>📝 تسجيل الاسم الثلاثي</h1>
        <p class="subtitle">يرجى إدخال اسمك الثلاثي للانضمام إلى الزيارة</p>
        
        <form id="nameForm">
            <input type="hidden" id="visitId" value="{{ visit_id }}">
            <input type="hidden" id="userId" value="{{ user_id }}">
            
            <div class="input-group">
                <label for="fullName">الاسم الثلاثي:</label>
                <input 
                    type="text" 
                    id="fullName" 
                    name="fullName" 
                    placeholder="مثال: أحمد محمد علي" 
                    required
                    minlength="3"
                >
            </div>
            
            <button type="submit" id="submitBtn">
                <span id="btnText">✅ تسجيل والانضمام</span>
            </button>
        </form>
        
        <div id="message" class="message"></div>
    </div>

    <script>
        document.getElementById('nameForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            
            const submitBtn = document.getElementById('submitBtn');
            const btnText = document.getElementById('btnText');
            const messageDiv = document.getElementById('message');
            const fullName = document.getElementById('fullName').value.trim();
            const visitId = document.getElementById('visitId').value;
            const userId = document.getElementById('userId').value;
            
            if (fullName.length < 3) {
                showMessage('يرجى إدخال اسم ثلاثي صحيح (3 أحرف على الأقل)', 'error');
                return;
            }
            
            // Disable button and show loading
            submitBtn.disabled = true;
            btnText.innerHTML = 'جاري التسجيل<span class="spinner"></span>';
            
            try {
                const response = await fetch('/api/save_name', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        visit_id: visitId,
                        user_id: userId,
                        full_name: fullName
                    })
                });
                
                const result = await response.json();
                
                if (result.success) {
                    showMessage('✅ تم تسجيل اسمك بنجاح! سيتم تحويلك إلى البوت...', 'success');
                    setTimeout(() => {
                        window.location.href = 'https://t.me/InspectionRusafa_bot?start=join_' + visitId;
                    }, 2000);
                } else {
                    showMessage('❌ خطأ: ' + result.error, 'error');
                    submitBtn.disabled = false;
                    btnText.textContent = '✅ تسجيل والانضمام';
                }
            } catch (error) {
                showMessage('❌ خطأ في الاتصال: ' + error.message, 'error');
                submitBtn.disabled = false;
                btnText.textContent = '✅ تسجيل والانضمام';
            }
        });
        
        function showMessage(text, type) {
            const messageDiv = document.getElementById('message');
            messageDiv.textContent = text;
            messageDiv.className = 'message ' + type;
            messageDiv.style.display = 'block';
        }
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    return "🤖 بوت التفتيش - خادم الويب نشط"

@app.route('/join/<visit_id>/<user_id>')
def join_page(visit_id, user_id):
    """عرض صفحة إدخال الاسم الثلاثي"""
    return render_template_string(
        HTML_TEMPLATE,
        visit_id=visit_id,
        user_id=user_id
    )

@app.route('/api/save_name', methods=['POST'])
def save_name():
    """حفظ الاسم الثلاثي في قاعدة البيانات"""
    try:
        data = request.get_json()
        visit_id = data.get('visit_id')
        user_id = data.get('user_id')
        full_name = data.get('full_name', '').strip()
        
        if not all([visit_id, user_id, full_name]):
            return jsonify({'success': False, 'error': 'بيانات غير مكتملة'}), 400
        
        if len(full_name) < 3:
            return jsonify({'success': False, 'error': 'الاسم يجب أن يكون 3 أحرف على الأقل'}), 400
        
        # تحديث الاسم في قاعدة البيانات
        success = update_member_full_name(int(visit_id), int(user_id), full_name)
        
        if success:
            return jsonify({'success': True, 'message': 'تم حفظ الاسم بنجاح'})
        else:
            # حتى لو لم يكن هناك سجل محدث، نعتبر العملية ناجحة
            return jsonify({'success': True, 'message': 'تم الحفظ'})
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
