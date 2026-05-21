import sqlite3
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

# قائمة الجهات (نحتاجها هنا لترتيب ظهورها في التقرير)
DESTINATIONS_LIST = [
    "الإيعاز الى ادارة المستشفى بما يلي:",
    "الإيعاز الى ادارة القطاع بما يلي:",
    "الإيعاز الى ادارة المركز بما يلي:",
    "الإيعاز الى قسم الامور الادارية والقانونية والمالية بما يلي:",
    "الإيعاز الى شعبة التحقيقات/ قسمنا بما يلي:"
]

def execute_query(query, params=(), fetch=False):
    """دالة الاتصال بقاعدة البيانات الخاصة بالتقرير"""
    conn = sqlite3.connect('inspection_db.sqlite')
    cursor = conn.cursor()
    cursor.execute(query, params)
    if fetch:
        result = cursor.fetchall()
        conn.close()
        return result
    conn.commit()
    conn.close()

def write_rtl(doc_obj, text, bold=False):
    """دالة مساعدة لكتابة النص العربي بوضوح وتجنب مشكلة المربعات"""
    p = doc_obj.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p.paragraph_format.right_to_left = True
    run = p.add_run(text)
    run.bold = bold
    run.font.name = 'Arial'
    run._element.rPr.rFonts.set(qn('w:cs'), 'Arial')
    return p

def generate_docx_report(visit_id):
    """
    الدالة الرئيسية لاستخراج التقرير
    تستقبل رقم الزيارة، وتقوم بتوليد ملف وورد، وتُرجع (اسم الملف، اسم المؤسسة)
    """
    # 1. جلب معلومات الزيارة
    visit_info = execute_query("SELECT institution_name, visit_date FROM Visits WHERE id = ?", (visit_id,), fetch=True)
    if not visit_info: 
        return None, None
    inst_name, v_date = visit_info[0]
    
    # 2. جلب التقارير
    raw_reports = execute_query("SELECT axis_name, section_name, notes, rec_destination, recommendations FROM Reports WHERE visit_id = ?", (visit_id,), fetch=True)
    
    grouped_notes = {}
    grouped_recs = {}
    
    # 3. فرز البيانات
    for axis, section, note, dest, rec in raw_reports:
        # فرز الملاحظات والمعلومات العامة
        if axis not in grouped_notes: grouped_notes[axis] = {}
        if section not in grouped_notes[axis]: grouped_notes[axis][section] = []
        grouped_notes[axis][section].append(note)
        
        # فرز التوصيات (فقط إذا كانت موجودة)
        if dest and rec:
            if dest not in grouped_recs: grouped_recs[dest] = []
            grouped_recs[dest].append(rec)

    # 4. بناء مستند Word
    doc = Document()
    write_rtl(doc, "جمهورية العراق\nوزارة الصحة / دائرة صحة بغداد الرصافة\nشعبة تفتيش المؤسسات الصحية الحكومية\n", bold=True)
    write_rtl(doc, f"📄 تقرير الزيارة التفتيشية إلى: {inst_name}\n📅 التاريخ: {v_date}\n", bold=True)
    
    # --- قسم الملاحظات والمعلومات ---
    write_rtl(doc, "أولاً: الملاحظات التفتيشية والمعلومات:", bold=True)
    
    for axis in ["المعلومات العامة", "المحور الفني", "المحور الإداري", "المحور الهندسي"]:
        if axis in grouped_notes:
            write_rtl(doc, f"■ {axis}", bold=True)
            for section, notes in grouped_notes[axis].items():
                if axis == "المعلومات العامة":
                    sec_clean = section if section.endswith(":") else f"{section}:"
                    write_rtl(doc, f" {sec_clean} {notes[0]}", bold=False)
                else:
                    write_rtl(doc, f" {section}", bold=False)
                    for note in notes:
                        write_rtl(doc, f"      - {note}", bold=False)
                        
    doc.add_paragraph("\n")
    
    # --- قسم التوصيات ---
    if grouped_recs:
        write_rtl(doc, "ثانياً: التوصيات والإجراءات المقترحة:", bold=True)
        for dest in DESTINATIONS_LIST:
            if dest in grouped_recs:
                write_rtl(doc, f"■ {dest}", bold=True)
                rec_counter = 1
                for rec in grouped_recs[dest]:
                    write_rtl(doc, f"   {rec_counter}- {rec}", bold=False)
                    rec_counter += 1
                
    # 5. الحفظ وإغلاق الزيارة
    file_name = f"Report_{visit_id}.docx"
    doc.save(file_name)
    execute_query("UPDATE Visits SET status = 'مغلقة' WHERE id = ?", (visit_id,))
    
    return file_name, inst_name