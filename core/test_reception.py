import datetime
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from core.billing.services import bill_exam
from core.models import (DichVuThucHien, GiaoDichThanhToan, HoaDon, LeTanChiNhanh,
                         LichKham, LogHeThong, PhieuKham, ThongBao)
from core.payments.access import can_admin, can_collect
from core import tests as fixture_tests
from core.test_vnpay import CONFIG


class ReceptionTests(TestCase):
    def setUp(self):
        fixture_tests.AppointmentBookingTests.setUp(self)
        self.client.logout()
        self.receptionist = User.objects.create_user('receptionist', password='Reception@2026!')
        self.profile = LeTanChiNhanh.objects.create(user=self.receptionist, chi_nhanh=self.clinic)
        self.client.force_login(self.receptionist)
        self.appointment = LichKham.objects.create(benh_nhan=self.patient, bac_si=self.doctor,
            dich_vu_dat_truoc=self.service, ngay_kham=self.next_monday, gio_kham=datetime.time(9, 0))

    def test_dashboard_is_branch_scoped_and_marks_intake_separately(self):
        response = self.client.get(reverse('reception_dashboard'), {'date': self.next_monday.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.patient.ho_ten)
        self.assertContains(response, 'Tiếp nhận bệnh nhân')
        self.assertContains(response, 'responsive-table')
        self.assertContains(response, 'Đang chờ')
        self.assertContains(response, 'core/img/EyeViora/logoEyeViora.png')
        self.assertContains(response, 'data-desk-menu')
        self.assertContains(response, self.service.ten)
        self.assertContains(response, reverse('payment_list'))
        self.assertNotContains(response, reverse('service_list'))
        # A directly submitted foreign appointment is not shown or reachable.
        from core.models import BenhNhan
        foreign_patient = BenhNhan.objects.create(ho_ten='Foreign patient', ngay_sinh=datetime.date(1990, 1, 1),
            gioi_tinh='nam', so_dien_thoai='0901222444', dia_chi='HCM')
        foreign = LichKham.objects.create(benh_nhan=foreign_patient, bac_si=self.other_doctor,
            ngay_kham=self.next_monday, gio_kham=datetime.time(9, 0))
        response = self.client.post(reverse('reception_transition', args=[foreign.pk, 'arrive']))
        self.assertEqual(response.status_code, 404)
        response = self.client.post(reverse('reception_transition', args=[self.appointment.pk, 'arrive']))
        self.assertEqual(response.status_code, 302)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai_tiep_nhan, 'da_tiep_nhan')
        self.assertEqual(self.appointment.trang_thai, 'cho')

    def test_dashboard_empty_state_is_compact_and_filters_are_kept(self):
        empty_day = self.next_monday + datetime.timedelta(days=30)
        response = self.client.get(reverse('reception_dashboard'), {'date': empty_day.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Không có lịch hẹn phù hợp với bộ lọc hiện tại.')
        self.assertContains(response, 'name="date"')
        self.assertContains(response, 'name="status"')

    def test_receptionist_never_inherits_cashier_or_global_admin_access(self):
        from django.contrib.auth.models import Permission
        self.assertTrue(can_collect(self.receptionist))
        self.receptionist.is_staff = True
        self.receptionist.save(update_fields=['is_staff'])
        cashier_permission = Permission.objects.get(content_type__app_label='core', codename='thu_vnpay')
        self.receptionist.user_permissions.add(cashier_permission)
        self.assertFalse(can_collect(self.receptionist))
        self.assertFalse(can_admin(self.receptionist))
        self.assertEqual(self.client.get(reverse('payment_list')).status_code, 403)
        self.assertEqual(self.client.get(reverse('custom_admin_dashboard')).status_code, 302)

    def test_patient_cannot_open_reception_dashboard(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('reception_dashboard')).status_code, 403)

    def test_receptionist_booking_for_existing_patient_and_audit(self):
        self.client.force_login(self.receptionist)
        patient2_user = User.objects.create_user('0901222333')
        from core.models import BenhNhan
        patient2 = BenhNhan.objects.create(user=patient2_user, ho_ten='Bệnh nhân khác',
            ngay_sinh=datetime.date(1990, 1, 1), gioi_tinh='nu', so_dien_thoai='0901222333', dia_chi='HCM')
        response = self.client.post(reverse('reception_book'), {
            'so_dien_thoai': patient2.so_dien_thoai, 'chi_nhanh': self.clinic.pk,
            'dich_vu_dat_truoc': self.service.pk, 'bac_si': self.doctor.pk,
            'ngay_kham': self.next_monday.isoformat(), 'gio_kham': '10:00', 'ghi_chu': '',
        })
        self.assertEqual(response.status_code, 302)
        appointment = LichKham.objects.get(benh_nhan=patient2)
        self.assertEqual(appointment.trang_thai_tiep_nhan, 'cho_xac_nhan')
        self.assertFalse(HoaDon.objects.filter(lich_kham=appointment).exists())
        self.assertTrue(LogHeThong.objects.filter(model_name='LichKham', object_id=appointment.pk,
            nguoi_thuc_hien=self.receptionist, hanh_dong='create').exists())

    def test_confirm_then_doctor_can_start_received_appointment(self):
        response = self.client.post(reverse('reception_transition', args=[self.appointment.pk, 'confirm']))
        self.assertEqual(response.status_code, 302)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai_tiep_nhan, 'da_xac_nhan')
        doctor_user = User.objects.create_user('doctor-intake')
        self.doctor.user = doctor_user
        self.doctor.save(update_fields=['user'])
        response = self.client.post(reverse('reception_transition', args=[self.appointment.pk, 'arrive']))
        self.assertEqual(response.status_code, 302)
        self.client.force_login(doctor_user)
        response = self.client.post(reverse('start_exam', args=[self.appointment.pk]), {'exception_reason': 'Bệnh nhân đã đến sớm'})
        self.assertEqual(response.status_code, 302)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, 'dang')

    def test_no_show_requires_grace_period_and_is_audited(self):
        from django.utils import timezone
        scheduled = timezone.make_aware(datetime.datetime.combine(self.next_monday, self.appointment.gio_kham))
        with patch('core.reception.timezone.localtime', return_value=scheduled + datetime.timedelta(minutes=10)):
            response = self.client.post(reverse('reception_transition', args=[self.appointment.pk, 'no_show']))
        self.assertEqual(response.status_code, 302)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai_tiep_nhan, 'cho_xac_nhan')
        with patch('core.reception.timezone.localtime', return_value=scheduled + datetime.timedelta(minutes=31)):
            response = self.client.post(reverse('reception_transition', args=[self.appointment.pk, 'no_show']))
        self.assertEqual(response.status_code, 302)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai_tiep_nhan, 'vang_mat')
        self.assertFalse(HoaDon.objects.filter(lich_kham=self.appointment).exists())

    def test_no_show_appointment_cannot_be_started_by_doctor(self):
        from django.core.exceptions import ValidationError
        from core.appointments import transition_exam
        self.appointment.trang_thai_tiep_nhan = 'vang_mat'
        doctor_user = User.objects.create_user('doctor-no-show')
        self.doctor.user = doctor_user
        self.doctor.save(update_fields=['user'])
        with self.assertRaises(ValidationError):
            transition_exam(self.appointment, doctor_user, 'dang')

    def test_cancel_and_reschedule_keep_branch_and_booking_rules(self):
        response = self.client.post(reverse('reception_cancel', args=[self.appointment.pk]),
                                    {'ly_do': 'Bệnh nhân yêu cầu hủy lịch'})
        self.assertEqual(response.status_code, 302)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, 'huy')
        self.assertTrue(LogHeThong.objects.filter(model_name='LichKham', object_id=self.appointment.pk,
            nguoi_thuc_hien=self.receptionist, ghi_chu__contains='Bệnh nhân yêu cầu hủy lịch').exists())

        replacement = LichKham.objects.create(benh_nhan=self.patient, bac_si=self.doctor,
            dich_vu_dat_truoc=self.service, ngay_kham=self.next_monday + datetime.timedelta(days=7), gio_kham=datetime.time(9, 0))
        response = self.client.post(reverse('reception_reschedule', args=[replacement.pk]), {
            'so_dien_thoai': self.patient.so_dien_thoai, 'chi_nhanh': self.clinic.pk,
            'dich_vu_dat_truoc': self.service.pk, 'bac_si': self.doctor.pk,
            'ngay_kham': (self.next_monday + datetime.timedelta(days=7)).isoformat(), 'gio_kham': '10:00', 'ghi_chu': '',
            'ly_do_thay_doi': 'Bệnh nhân đề nghị chuyển sang giờ khác',
        })
        self.assertEqual(response.status_code, 302, getattr(response.context.get('form') if response.context else None, 'errors', None))
        replacement.refresh_from_db()
        self.assertEqual(replacement.gio_kham, datetime.time(10, 0))
        self.assertTrue(LogHeThong.objects.filter(model_name='LichKham', object_id=replacement.pk,
            nguoi_thuc_hien=self.receptionist, ghi_chu__contains='Bệnh nhân đề nghị chuyển sang giờ khác').exists())
        self.assertTrue(ThongBao.objects.filter(nguoi_nhan=self.patient.user,
            tieu_de='Lịch khám đã được thay đổi').exists())

    def test_inactive_receptionist_is_denied(self):
        self.receptionist.is_active = False
        self.receptionist.save(update_fields=['is_active'])
        self.assertEqual(self.client.get(reverse('reception_dashboard')).status_code, 302)

    def prepare_invoice(self, appointment):
        appointment.trang_thai = 'xong'
        appointment.trang_thai_tiep_nhan = 'da_tiep_nhan'
        appointment.save(update_fields=['trang_thai', 'trang_thai_tiep_nhan'])
        exam = PhieuKham.objects.create(lich_kham=appointment, benh_nhan=appointment.benh_nhan,
            trieu_chung='test', chan_doan='test', huong_dieu_tri='test')
        DichVuThucHien.objects.create(phieu_kham=exam, dich_vu=self.service,
            nguoi_thuc_hien=appointment.bac_si, da_xac_nhan_bac_si=True,
            la_dich_vu_dat_truoc=appointment.dich_vu_dat_truoc_id == self.service.pk)
        admin = User.objects.create_superuser(f'admin-{appointment.pk}',
            f'admin-{appointment.pk}@example.invalid', 'Secret@2026!')
        return bill_exam(exam.pk, admin)

    def test_receptionist_can_view_branch_invoice_and_collect_full_cash_once(self):
        invoice = self.prepare_invoice(self.appointment)
        dashboard = self.client.get(reverse('reception_dashboard'), {'date': self.next_monday.isoformat()})
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, 'Hóa đơn cần xử lý tại quầy')
        self.assertContains(dashboard, f'#{invoice.pk}')
        detail = self.client.get(reverse('payment_detail', args=[self.appointment.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'Thu tiền mặt')
        self.assertContains(detail, 'desk-payment-confirmation')
        self.assertContains(detail, 'name="received_full"')
        self.assertContains(detail, 'name="csrfmiddlewaretoken"')
        self.assertEqual(self.client.get(reverse('receipt_view', args=[invoice.pk])).status_code, 404)
        response = self.client.post(reverse('payment_cash', args=[invoice.pk]), {
            'received_full': 'yes', 'version': invoice.phien_ban,
        })
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.trang_thai, 'paid')
        payment = GiaoDichThanhToan.objects.get(hoa_don=invoice)
        self.assertEqual((payment.phuong_thuc, payment.nguoi_tao_id, payment.chi_nhanh_id),
                         ('CASH', self.receptionist.pk, self.clinic.pk))
        self.assertEqual(self.client.post(reverse('payment_cash', args=[invoice.pk]), {
            'received_full': 'yes', 'version': invoice.phien_ban,
        }).status_code, 302)
        self.assertEqual(GiaoDichThanhToan.objects.filter(hoa_don=invoice, trang_thai='success').count(), 1)
        self.assertEqual(self.client.get(reverse('receipt_view', args=[invoice.pk])).status_code, 200)

    @override_settings(**CONFIG)
    def test_receptionist_can_start_vnpay_for_branch_and_cannot_cross_branch(self):
        invoice = self.prepare_invoice(self.appointment)
        response = self.client.post(reverse('payment_start', args=[invoice.pk]))
        self.assertEqual(response.status_code, 302)
        attempt = GiaoDichThanhToan.objects.get(hoa_don=invoice)
        self.assertEqual((attempt.trang_thai, attempt.nguoi_tao_id, attempt.chi_nhanh_id),
                         ('pending', self.receptionist.pk, self.clinic.pk))
        self.client.post(reverse('payment_cash', args=[invoice.pk]), {
            'received_full': 'yes', 'version': invoice.phien_ban,
        })
        invoice.refresh_from_db()
        self.assertEqual(invoice.trang_thai, 'unpaid')
        self.assertEqual(GiaoDichThanhToan.objects.filter(hoa_don=invoice, phuong_thuc='CASH').count(), 0)

        foreign = LichKham.objects.create(benh_nhan=self.patient, bac_si=self.other_doctor,
            ngay_kham=self.next_monday, gio_kham=datetime.time(11, 0), trang_thai='cho')
        foreign_invoice = self.prepare_invoice(foreign)
        self.assertEqual(self.client.get(reverse('payment_detail', args=[foreign.pk])).status_code, 404)
        for route, pk in [('payment_cash', foreign_invoice.pk), ('payment_start', foreign_invoice.pk)]:
            self.assertEqual(self.client.post(reverse(route, args=[pk])).status_code, 404)

    def test_receptionist_cannot_charge_before_patient_is_received_at_counter(self):
        invoice = self.prepare_invoice(self.appointment)
        LichKham.objects.filter(pk=self.appointment.pk).update(trang_thai_tiep_nhan='da_xac_nhan')
        detail = self.client.get(reverse('payment_detail', args=[self.appointment.pk]))
        self.assertContains(detail, 'Chưa thể thu vì lịch chưa được xác nhận bệnh nhân đã có mặt')
        self.assertNotContains(detail, 'Thanh toán VNPAY</button>')
        self.assertEqual(self.client.post(reverse('payment_cash', args=[invoice.pk]), {
            'received_full': 'yes', 'version': invoice.phien_ban,
        }).status_code, 403)
        self.assertEqual(self.client.post(reverse('payment_start', args=[invoice.pk])).status_code, 403)
        self.assertFalse(GiaoDichThanhToan.objects.filter(hoa_don=invoice).exists())

    def test_receptionist_cannot_change_service_or_recalculate_invoice(self):
        invoice = self.prepare_invoice(self.appointment)
        self.assertEqual(self.client.post(reverse('payment_services_save', args=[self.appointment.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('invoice_create', args=[self.appointment.pk])).status_code, 403)
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            bill_exam(invoice.phieu_kham_id, self.receptionist, recalculate=True)
        invoice.refresh_from_db()
        self.assertEqual(invoice.so_tien, 100000)

    def test_dashboard_search_includes_invoice_id_and_branch_scoped_results(self):
        invoice = self.prepare_invoice(self.appointment)
        response = self.client.get(reverse('reception_dashboard'), {'q': str(invoice.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['invoice_count'], 1)
        response = self.client.get(reverse('payment_list'), {'q': self.appointment.benh_nhan.so_dien_thoai})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['page_obj']), 1)

    def test_superuser_retains_global_administration(self):
        superuser = User.objects.create_superuser('root-reception', 'root@example.invalid', 'Secret@2026!')
        self.client.force_login(superuser)
        self.assertEqual(self.client.get(reverse('reception_dashboard')).status_code, 403)
        self.assertEqual(self.client.get(reverse('custom_admin_dashboard')).status_code, 200)

    def test_receptionist_login_uses_existing_django_authentication(self):
        self.client.logout()
        response = self.client.post(reverse('login'), {
            'username': self.receptionist.username, 'password': 'Reception@2026!',
        })
        self.assertRedirects(response, reverse('reception_dashboard'))


class ReceptionAccountTests(TestCase):
    def test_admin_can_create_branch_receptionist(self):
        from core.models import ChiNhanh
        from django.contrib.gis.geos import Point
        admin = User.objects.create_superuser('admin-reception', 'admin@example.invalid', 'Secret@2026!')
        branch = ChiNhanh.objects.create(ten='Branch A', dia_chi='A', phuong='A',
            vi_tri=Point(106.7, 10.77, srid=4326), gio_mo=datetime.time(7), gio_dong=datetime.time(17))
        self.enterContext(override_settings(PUBLIC_CLINIC_BRANCH_IDS=(branch.pk,)))
        self.client.force_login(admin)
        response = self.client.post(reverse('reception_accounts'), {
            'username': 'new-reception', 'password': 'Reception@2026!Strong',
            'first_name': 'Lan', 'last_name': 'Le', 'email': '', 'is_active': 'on', 'chi_nhanh': branch.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LeTanChiNhanh.objects.get(user__username='new-reception').chi_nhanh_id, branch.pk)
        self.assertFalse(User.objects.get(username='new-reception').is_staff)
