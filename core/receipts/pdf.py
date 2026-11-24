"""In-memory A4 documents. No public media files or clinical fields."""
from io import BytesIO
from pathlib import Path
from threading import Lock
from xml.sax.saxutils import escape

from django.db import transaction
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core.models import HoaDon, LichKham

ROOT = Path(__file__).resolve().parent
_font_lock = Lock()


def register_fonts():
    with _font_lock:
        if 'ReceiptNoto' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('ReceiptNoto', str(ROOT / 'fonts/NotoSans-Regular.ttf')))
            pdfmetrics.registerFont(TTFont('ReceiptNotoBold', str(ROOT / 'fonts/NotoSans-Bold.ttf')))
            pdfmetrics.registerFontFamily('ReceiptNoto', normal='ReceiptNoto', bold='ReceiptNotoBold')


def money(value):
    return f'{value:,.0f}'.replace(',', '.') + ' VND'


def date_time(value):
    return timezone.localtime(value).strftime('%d/%m/%Y %H:%M:%S') if value else 'Chưa ghi nhận'


def snapshot(invoice):
    """Invoice must be locked by caller to keep its version and lines consistent."""
    appointment = LichKham.objects.select_related('benh_nhan', 'bac_si__chi_nhanh').get(pk=invoice.lich_kham_id)
    payment = invoice.giao_dichs.filter(trang_thai='success').select_related('chi_nhanh').first()
    pending = invoice.giao_dichs.filter(trang_thai='pending').exists()
    if invoice.trang_thai == 'paid':
        method = payment.get_phuong_thuc_display() if payment else 'Chưa lưu phương thức (hóa đơn cũ)'
        status = 'Đã thanh toán'
    else:
        method = 'VNPAY (đang xử lý)' if pending else 'Chưa ghi nhận thanh toán'
        status = 'Đang xử lý — chưa xác nhận thanh toán' if pending else 'Chưa thanh toán'
    if invoice.can_doi_soat:
        status = 'Cần đối soát — chưa phát hành xác nhận thu tiền'
    clinic = payment.chi_nhanh if payment and payment.chi_nhanh_id else appointment.bac_si.chi_nhanh
    return {
        'id': invoice.pk, 'version': invoice.phien_ban, 'created': date_time(invoice.ngay_tao),
        'clinic': clinic.ten, 'patient': appointment.benh_nhan.ho_ten,
        'exam_date': appointment.ngay_kham.strftime('%d/%m/%Y'),
        'lines': list(invoice.chi_tiet_hien_hanh.values('ma_dich_vu', 'ten_dich_vu', 'so_luong', 'don_gia', 'thanh_tien', 'la_dich_vu_dat_truoc')),
        'gross': invoice.tong_chi_phi if invoice.tong_chi_phi is not None else invoice.so_tien, 'amount': invoice.so_tien,
        'method': method, 'status': status, 'paid_at': date_time(invoice.ngay_thanh_toan) if invoice.trang_thai == 'paid' else 'Chưa ghi nhận',
        'confirmed': invoice.trang_thai == 'paid' and not invoice.can_doi_soat,
    }


@transaction.atomic
def invoice_snapshot(invoice_id):
    return snapshot(HoaDon.objects.select_for_update().get(pk=invoice_id))


def render_pdf(data):
    register_fonts()
    output = BytesIO()
    styles = {
        'body': ParagraphStyle('body', fontName='ReceiptNoto', fontSize=9, leading=14, spaceAfter=4),
        'title': ParagraphStyle('title', fontName='ReceiptNotoBold', fontSize=16, leading=23, alignment=TA_CENTER, spaceAfter=10),
        'bold': ParagraphStyle('bold', fontName='ReceiptNotoBold', fontSize=9, leading=14),
        'right': ParagraphStyle('right', fontName='ReceiptNoto', fontSize=8, leading=12, alignment=TA_RIGHT),
        'cell': ParagraphStyle('cell', fontName='ReceiptNoto', fontSize=8, leading=12),
    }

    def p(text, style='body'):
        # Never allow catalog/patient values to introduce ReportLab markup or URLs.
        return Paragraph(escape(str(text)), styles[style])

    story = []
    logo = ROOT.parent / 'static/core/img/EyeViora/logoEyeViora.png'
    brand = Table([[Image(str(logo), width=14*mm, height=14*mm, kind='proportional'), p('EyeViora', 'bold')]], colWidths=[18*mm, 160*mm])
    brand.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (-1, -1), 0)]))
    story += [brand, Spacer(1, 6*mm), p('PHIẾU THANH TOÁN NỘI BỘ', 'title'),
        p('Không phải hóa đơn điện tử có giá trị pháp lý.'),
        p(f'Mã hóa đơn: #{data["id"]} · Phiên bản: {data["version"]}'), p(f'Ngày lập: {data["created"]}'),
        p(f'Ph\u00f2ng kh\u00e1m: {data["clinic"]}'), p(f'B\u1ec7nh nh\u00e2n: {data["patient"]}'),
        p(f'Ngày khám: {data["exam_date"]}'), p(f'Trạng thái: {data["status"]}', 'bold'), Spacer(1, 4*mm)]
    if not data['confirmed']:
        story += [p('Phiếu này chưa xác nhận đã thu tiền. Vui lòng kiểm tra trạng thái tại quầy.'), Spacer(1, 3*mm)]
    if data['lines']:
        rows = [[p('Dịch vụ', 'bold'), p('Nguồn', 'bold'), p('SL', 'bold'), p('Đơn giá đã chốt', 'bold'), p('Thành tiền', 'bold')]]
        for line in data['lines']:
            source = 'Đặt trước đã thực hiện' if line['la_dich_vu_dat_truoc'] else 'Phát sinh'
            rows.append([p(f'{line["ma_dich_vu"]} · {line["ten_dich_vu"]}', 'cell'), p(source, 'cell'), p(line['so_luong'], 'cell'),
                         p(money(line['don_gia']), 'right'), p(money(line['thanh_tien']), 'right')])
        table = Table(rows, colWidths=[68*mm, 30*mm, 12*mm, 36*mm, 37*mm], repeatRows=1, hAlign='LEFT')
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e8f4fb')),
            ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#dce8ef')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 7),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ]))
        story += [table, Spacer(1, 5*mm)]
    else:
        story += [p('Hóa đơn cũ chưa lưu chi tiết dịch vụ. Tổng tiền theo hóa đơn đã chốt.'), Spacer(1, 4*mm)]
    story += [p(f'Tổng chi phí: {money(data["gross"])}')]
    story += [p(f'Phương thức thanh toán: {data["method"]}'),
        p(f'Số tiền đã thanh toán: {money(data["amount"])}'),
        p(f'Thời điểm thanh toán: {data["paid_at"]}')]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('ReceiptNoto', 8)
        canvas.setFillColor(colors.HexColor('#617486'))
        canvas.drawString(16*mm, 10*mm, f'EyeViora · Phiếu nội bộ #{data["id"]}')
        canvas.drawRightString(A4[0]-16*mm, 10*mm, f'Trang {doc.page}')
        canvas.restoreState()

    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=16*mm, rightMargin=16*mm,
        topMargin=14*mm, bottomMargin=19*mm, title=f'Phieu thanh toan noi bo #{data["id"]}', author='EyeViora')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
