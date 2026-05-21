import sqlite3
import os
import requests
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

DESTINATIONS_LIST = [
    "الإيعاز الى ادارة المستشفى بما يلي:",
    "الإيعاز الى ادارة القطاع بما يلي:",
    "الإيعاز الى ادارة المركز بما يلي:",
    "الإيعاز الى قسم الامور الادارية والقانونية والمالية بما يلي:",
    "الإيعاز الى شعبة التحقيقات/ قسمنا بما يلي:"
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

def write_rtl(doc_obj, text, bold=False, size=12):
    p = doc_obj.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p.paragraph_format.right_to_left = True
    run = p.add_run(text)
    run.bold = bold
    run.font.name = 'Arial'
    run.font.size = Pt(size)
    run._element.rPr.rFonts.set(qn('w:cs'), 'Arial')
    return p

def generate_docx_report(visit_id, bot_token=None):
    """
    يولد تقرير Word للزيارة.
    يُرجع (اسم الملف، اسم المؤسسة)
    """
    visit_info = execute_query(
        "SELECT institution_name, visit_date FROM Visits WHERE id = ?", (visit_id,), fetch=True
    )
    if not visit_info:
        return None, None
    inst_name, v_date = visit_info[0]

    raw_reports = execute_query(
        "SELECT axis_name, section_name, notes, rec_destination, recommendations FROM Reports WHERE visit_id = ?",
        (visit_id,), fetch=True
    )

    grouped_notes = {}
    grouped_recs = {}

    for axis, section, note, dest, rec in raw_reports:
        if axis not in grouped_notes:
            grouped_notes[axis] = {}
        if section not in grouped_notes[axis]:
            grouped_notes[axis][section] = []
        grouped_notes[axis][section].append(note)

        if dest and rec:
            if dest not in grouped_recs:
                grouped_recs[dest] = []
            grouped_recs[dest].append(rec)

    doc = Document()

    # ضبط RTL افتراضي للمستند
    from docx.oxml import OxmlElement
    settings = doc.settings.element
    bidi = OxmlElement('w:bidi')
    settings.append(bidi)

    write_rtl(doc, "جمهورية العراق", bold=True, size=13)
    write_rtl(doc, "وزارة الصحة / دائرة صحة بغداد الرصافة", bold=True, size=13)
    write_rtl(doc, "شعبة تفتيش المؤسسات الصحية الحكومية", bold=True, size=13)
    doc.add_paragraph()
    write_rtl(doc, f"تقرير الزيارة التفتيشية إلى: {inst_name}", bold=True, size=14)
    write_rtl(doc, f"التاريخ: {v_date}", bold=True, size=12)
    doc.add_paragraph()

    # --- الملاحظات ---
    write_rtl(doc, "أولاً: الملاحظات التفتيشية والمعلومات:", bold=True, size=13)

    for axis in ["المعلومات العامة", "المحور الفني", "المحور الإداري", "المحور الهندسي"]:
        if axis in grouped_notes:
            write_rtl(doc, f"■ {axis}", bold=True)
            for section, notes in grouped_notes[axis].items():
                if axis == "المعلومات العامة":
                    sec_clean = section if section.endswith(":") else f"{section}:"
                    write_rtl(doc, f"   {sec_clean} {notes[0]}")
                else:
                    write_rtl(doc, f"   {section}", bold=False)
                    for note in notes:
                        write_rtl(doc, f"        - {note}")

    doc.add_paragraph()

    # --- التوصيات ---
    if grouped_recs:
        write_rtl(doc, "ثانياً: التوصيات والإجراءات المقترحة:", bold=True, size=13)
        for dest in DESTINATIONS_LIST:
            if dest in grouped_recs:
                write_rtl(doc, f"■ {dest}", bold=True)
                for i, rec in enumerate(grouped_recs[dest], 1):
                    write_rtl(doc, f"   {i}- {rec}")

    file_name = f"Report_{visit_id}.docx"
    doc.save(file_name)
    execute_query("UPDATE Visits SET status = 'مغلقة' WHERE id = ?", (visit_id,))

    return file_name, inst_name