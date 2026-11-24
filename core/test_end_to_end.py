from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core import mail
from django.core.mail.backends.locmem import EmailBackend
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from core.models import ChiTietKhamMat, DonKinh, DonThuoc, EmailPhieuThanhToan, GiaoDichThanhToan, HoaDon, LeTanChiNhanh, LichKham, PhieuKham
from core.test_exam_services import payload
from core.test_receipts import MAIL_SETTINGS, pdf_text
from core.test_workflow import WorkflowFixture


@override_settings(**MAIL_SETTINGS)
class ClinicEndToEndTests(WorkflowFixture, TransactionTestCase):
    def setUp(self):
        self.prepare_workflow()
        public_branch_override = override_settings(
            PUBLIC_CLINIC_BRANCH_IDS=(self.clinic.pk,),
        )
        public_branch_override.enable()
        self.addCleanup(public_branch_override.disable)
        cache.clear()
        self.receptionist = User.objects.create_user('qaReception123', password=self.password)
        LeTanChiNhanh.objects.create(user=self.receptionist, chi_nhanh=self.clinic)
        smtp = patch('core.receipts.delivery.get_connection', side_effect=lambda **kwargs: EmailBackend())
        smtp.start()
        self.addCleanup(smtp.stop)

    def login_as(self, username):
        response = self.client.post(reverse('login'), {'username': username, 'password': self.password})
        self.assertEqual(response.status_code, 302)
        self.assertIn('_auth_user_id', self.client.session)

    def test_booking_reception_eye_exam_cash_invoice_and_receipt(self):
        # Patient selects from real public branch/service/doctor data and books a slot.
        self.client.post(reverse('register'), {
            'username': 'qaPatientE2E', 'phone': '0901000012', 'name': 'Bệnh nhân E2E',
            'email': 'qa-patient@example.invalid', 'password': self.password,
        })
        self.patient = User.objects.get(username='qaPatientE2E').benh_nhan
        self.login_as('qaPatientE2E')
        self.assertEqual(self.client.get(reverse('home')).status_code, 200)
        self.assertEqual(self.client.get(reverse('branch_detail', args=[self.clinic.pk])).status_code, 200)
        booking = self.client.post(reverse('dat_lich', args=[self.clinic.pk]), {
            'chi_nhanh': self.clinic.pk, 'dich_vu_dat_truoc_nhieu': [self.service.pk],
            'bac_si': self.doctor.pk, 'ngay_kham': self.date.isoformat(),
            'gio_kham': '09:00', 'ghi_chu': 'E2E test',
        })
        self.assertEqual(booking.status_code, 302, {
            'form_errors': booking.context.get('form').errors if booking.context and booking.context.get('form') else None,
            'messages': [str(message) for message in booking.context.get('messages', [])] if booking.context else None,
        })
        self.appointment = LichKham.objects.get(benh_nhan=self.patient)
        self.assertEqual(LichKham.objects.filter(benh_nhan=self.patient).count(), 1)
        self.assertFalse(HoaDon.objects.filter(lich_kham=self.appointment).exists())
        self.assertFalse(GiaoDichThanhToan.objects.exists())

        # Reception check-in changes intake state only.
        self.login_as('qaReception123')
        self.assertEqual(self.client.get(reverse('reception_dashboard')).status_code, 200)
        self.client.post(reverse('reception_transition', args=[self.appointment.pk, 'confirm']))
        self.client.post(reverse('reception_transition', args=[self.appointment.pk, 'arrive']))
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai_tiep_nhan, 'da_tiep_nhan')
        self.assertEqual(self.appointment.trang_thai, 'cho')
        self.assertFalse(PhieuKham.objects.filter(lich_kham=self.appointment).exists())
        self.assertFalse(HoaDon.objects.filter(lich_kham=self.appointment).exists())

        # Assigned doctor stores actual eye measurements and orders, then completes once.
        self.login_as('qaDoctor123')
        self.client.post(reverse('start_exam', args=[self.appointment.pk]), {
            'exception_reason': 'Kiểm thử quy trình E2E',
        })
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, 'dang')
        exam_data = payload(None, [])
        exam_data.update({
            'action': 'complete', 'exception_reason': 'Kiểm thử quy trình E2E',
            'booked_performed': str(self.service.pk),
            'eye-thi_luc_phai_khong_kinh': '6/12', 'eye-thi_luc_trai_khong_kinh': '6/9',
            'eye-nhan_ap_phai': '16.5', 'eye-nhan_ap_trai': '17.0',
            'eye-sph_phai': '-1.25', 'eye-sph_trai': '-0.75',
            'glasses-sph_phai': '-1.25', 'glasses-sph_trai': '-0.75',
            'glasses-bac_si_xac_nhan': 'on',
            'medicine-TOTAL_FORMS': '2', 'medicine-INITIAL_FORMS': '0',
            'medicine-0-ten_thuoc': 'Thuốc kiểm thử', 'medicine-0-lieu_dung': '1 lần/ngày',
            'medicine-0-huong_dan': 'Theo chỉ định kiểm thử', 'medicine-0-thoi_gian_dung': '3 ngày',
        })
        completed = self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), exam_data)
        self.assertEqual(completed.status_code, 302, {
            'eye': completed.context['eye_form'].errors if completed.context else None,
            'glasses': completed.context['glasses_form'].errors if completed.context else None,
            'medicine': completed.context['medicine_formset'].errors if completed.context else None,
            'services': completed.context['services_formset'].errors if completed.context else None,
            'messages': [str(item) for item in completed.context['messages']] if completed.context else None,
        })
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, 'xong')
        exam = PhieuKham.objects.get(lich_kham=self.appointment)
        self.assertEqual(PhieuKham.objects.filter(lich_kham=self.appointment).count(), 1)
        self.assertEqual(ChiTietKhamMat.objects.get(phieu_kham=exam).thi_luc_phai_khong_kinh, '6/12')
        self.assertEqual(DonKinh.objects.get(phieu_kham=exam).sph_phai, Decimal('-1.25'))
        self.assertEqual(DonThuoc.objects.get(phieu_kham=exam).ten_thuoc, 'Thuốc kiểm thử')
        invoice = HoaDon.objects.get(lich_kham=self.appointment)
        self.assertEqual(HoaDon.objects.filter(lich_kham=self.appointment).count(), 1)
        self.assertEqual(invoice.so_tien, Decimal('100000'))
        self.assertEqual(invoice.trang_thai, HoaDon.TrangThai.CHUA_THANH_TOAN)
        self.assertFalse(invoice.giao_dichs.exists())

        # Receptionist settles only this branch invoice; receipt is available afterward.
        self.login_as('qaReception123')
        self.assertEqual(self.client.get(reverse('payment_detail', args=[self.appointment.pk])).status_code, 200)
        paid = self.client.post(reverse('payment_cash', args=[invoice.pk]), {
            'received_full': 'yes', 'version': invoice.phien_ban,
        })
        self.assertEqual(paid.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.trang_thai, HoaDon.TrangThai.DA_THANH_TOAN)
        transaction = GiaoDichThanhToan.objects.get(hoa_don=invoice, trang_thai='success')
        self.assertEqual((transaction.phuong_thuc, transaction.nguoi_tao_id), ('CASH', self.receptionist.pk))
        self.assertEqual(self.client.get(reverse('receipt_view', args=[invoice.pk])).status_code, 200)
        self.assertEqual(EmailPhieuThanhToan.objects.get(hoa_don=invoice).trang_thai, 'sent')
        receipt_emails = [message for message in mail.outbox
                          if any(getattr(attachment, 'mimetype', '') == 'application/pdf'
                                 for attachment in message.attachments)]
        self.assertEqual(len(receipt_emails), 1)
        self.assertNotIn('BHYT', pdf_text(self.client.get(reverse('receipt_download', args=[invoice.pk])).content))

