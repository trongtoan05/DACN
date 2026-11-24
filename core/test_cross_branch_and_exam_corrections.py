import datetime

from django.contrib.auth.models import Permission, User
from django.contrib.gis.geos import Point
from django.test import TestCase
from django.urls import reverse

from django.utils import timezone

from core.forms import AdminUserForm
from core.models import (ChiNhanh, GiaoDichThanhToan, HoaDon, Khoa, LeTanChiNhanh, LichKham, LogHeThong,
                         PhieuKham, YeuCauChinhSuaPhieuKham)
from core.test_exam_services import fixtures


class CrossBranchHistoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _, _, cls.appointment, cls.doctor_user, _, _, _, _ = fixtures()
        cls.patient = cls.appointment.benh_nhan
        cls.permission = Permission.objects.get(
            content_type__app_label='core', codename='view_cross_branch_medical_history')
        cls.doctor_user.user_permissions.add(cls.permission)
        other_branch = ChiNhanh.objects.create(
            ten='Chi nhánh phía Đông', dia_chi='Địa chỉ test', phuong='Phường test',
            vi_tri=Point(106.8, 10.8, srid=4326), gio_mo=datetime.time(7),
            gio_dong=datetime.time(17))
        department = Khoa.objects.create(ten='Nhãn khoa', chi_nhanh=other_branch)
        from core.models import BacSi
        other_doctor = BacSi.objects.create(ho_ten='Bác sĩ chi nhánh khác', chuyen_khoa='Nhãn khoa',
            khoa=department, chi_nhanh=other_branch, so_dien_thoai='0901000088')
        appointment = LichKham.objects.create(
            benh_nhan=cls.patient, bac_si=other_doctor,
            ngay_kham=datetime.date(2026, 10, 5), gio_kham=datetime.time(10), trang_thai='xong')
        cls.other_exam = PhieuKham.objects.create(
            lich_kham=appointment, benh_nhan=cls.patient, trieu_chung='Khô mắt',
            chan_doan='Khô mắt nhẹ', huong_dieu_tri='Dùng nước mắt nhân tạo')

    def test_permitted_doctor_reads_cross_branch_history_and_access_is_audited(self):
        self.client.force_login(self.doctor_user)
        response = self.client.post(reverse('cross_branch_history'), {
            'so_dien_thoai': self.patient.so_dien_thoai,
            'ly_do': 'Cần xem tiền sử trước khi lập kế hoạch điều trị hiện tại.',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Chi nhánh phía Đông')
        self.assertContains(response, 'Khô mắt nhẹ')
        audit = LogHeThong.objects.get(model_name='LichSuKhamBenhAccess', object_id=self.patient.pk)
        self.assertEqual(audit.du_lieu_moi['doctor_id'], self.doctor_user.bac_si.pk)
        self.assertNotIn('Khô mắt nhẹ', str(audit.du_lieu_moi))

    def test_permission_denied_without_cross_branch_permission(self):
        self.client.force_login(User.objects.get(username='other_exam_doctor'))
        response = self.client.get(reverse('cross_branch_history'))
        self.assertEqual(response.status_code, 403)

    def test_reception_account_cannot_open_cross_branch_history(self):
        user = self.doctor_user
        LeTanChiNhanh.objects.create(user=user, chi_nhanh=self.appointment.bac_si.chi_nhanh)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('cross_branch_history')).status_code, 403)

    def test_short_reason_does_not_query_or_log_patient_history(self):
        self.client.force_login(self.doctor_user)
        response = self.client.post(reverse('cross_branch_history'), {
            'so_dien_thoai': self.patient.so_dien_thoai, 'ly_do': 'Xem lịch sử',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(LogHeThong.objects.filter(model_name='LichSuKhamBenhAccess').exists())

    def test_patient_can_view_own_completed_history_across_branches(self):
        self.client.force_login(self.patient.user)
        response = self.client.get(reverse('medical_history'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Chi nhánh phía Đông')
        self.assertContains(response, 'Khô mắt nhẹ')


class CompletedExamCorrectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin, _, cls.appointment, cls.doctor_user, _, _, _, _ = fixtures()
        cls.patient = cls.appointment.benh_nhan
        cls.exam = PhieuKham.objects.get(lich_kham=cls.appointment)

    def submit_request(self, user=None, **overrides):
        self.client.force_login(user or self.doctor_user)
        data = {
            'ly_do': 'Bổ sung kết quả kiểm tra đã ghi nhận trong buổi khám.',
            'trieu_chung': self.exam.trieu_chung,
            'chan_doan': 'Chẩn đoán đã hiệu chỉnh',
            'huong_dieu_tri': self.exam.huong_dieu_tri,
        }
        data.update(overrides)
        return self.client.post(reverse('exam_correction_request', args=[self.exam.pk]), data)

    def test_only_owner_doctor_can_submit_completed_exam_correction(self):
        response = self.submit_request(user=User.objects.get(username='other_exam_doctor'))
        self.assertEqual(response.status_code, 404)
        self.assertFalse(YeuCauChinhSuaPhieuKham.objects.exists())

    def test_request_is_pending_and_does_not_mutate_exam(self):
        response = self.submit_request()
        self.assertEqual(response.status_code, 302)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.chan_doan, 'Test')
        row = YeuCauChinhSuaPhieuKham.objects.get()
        self.assertEqual(row.trang_thai, 'pending')
        self.assertEqual(row.du_lieu_truoc['clinical']['chan_doan'], 'Test')
        self.assertEqual(row.du_lieu_de_xuat['clinical']['chan_doan'], 'Chẩn đoán đã hiệu chỉnh')

    def test_unauthorized_account_cannot_review(self):
        self.submit_request()
        row = YeuCauChinhSuaPhieuKham.objects.get()
        self.client.force_login(User.objects.get(username='other_exam_doctor'))
        self.assertEqual(self.client.get(reverse('exam_correction_review', args=[row.pk])).status_code, 403)

    def test_approver_applies_content_and_preserves_before_after_audit(self):
        self.submit_request()
        row = YeuCauChinhSuaPhieuKham.objects.get()
        invoice = HoaDon.objects.create(
            lich_kham=self.appointment, phieu_kham=self.exam, so_tien=10000,
            trang_thai='paid', ngay_thanh_toan=timezone.now(), nguoi_tao=self.admin,
        )
        payment = GiaoDichThanhToan.objects.create(
            hoa_don=invoice, so_tien=10000, phuong_thuc='CASH',
            chi_nhanh=self.appointment.bac_si.chi_nhanh, trang_thai='success',
            nguoi_tao=self.admin, hoan_tat_luc=timezone.now(),
        )
        self.client.force_login(self.admin)
        response = self.client.post(reverse('exam_correction_review', args=[row.pk]), {
            'action': 'approve', 'ghi_chu_duyet': 'Đối chiếu hồ sơ gốc, nội dung phù hợp.',
        })
        self.assertEqual(response.status_code, 302)
        self.exam.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual(self.exam.chan_doan, 'Chẩn đoán đã hiệu chỉnh')
        self.assertEqual(row.trang_thai, 'approved')
        self.assertEqual(row.nguoi_de_nghi, self.doctor_user)
        self.assertEqual(row.nguoi_duyet, self.admin)
        self.assertEqual(row.du_lieu_sau['clinical']['chan_doan'], 'Chẩn đoán đã hiệu chỉnh')
        audit = LogHeThong.objects.filter(model_name='PhieuKham', object_id=self.exam.pk).latest('pk')
        self.assertEqual(audit.du_lieu_cu['chan_doan'], 'Test')
        invoice.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(invoice.so_tien, 10000)
        self.assertEqual(invoice.trang_thai, 'paid')
        self.assertEqual(payment.trang_thai, 'success')

    def test_requester_cannot_self_approve_even_with_manager_authority(self):
        from core.models import QuanLyChiNhanh
        QuanLyChiNhanh.objects.create(user=self.doctor_user, chi_nhanh=self.appointment.bac_si.chi_nhanh)
        self.submit_request()
        row = YeuCauChinhSuaPhieuKham.objects.get()
        response = self.client.post(reverse('exam_correction_review', args=[row.pk]), {
            'action': 'approve', 'ghi_chu_duyet': 'Duyệt nội dung',
        })
        self.assertEqual(response.status_code, 403)
        row.refresh_from_db()
        self.assertEqual(row.trang_thai, 'pending')

    def test_rejection_requires_reason_and_does_not_change_exam(self):
        self.submit_request()
        row = YeuCauChinhSuaPhieuKham.objects.get()
        self.client.force_login(self.admin)
        response = self.client.post(reverse('exam_correction_review', args=[row.pk]), {
            'action': 'reject', 'ghi_chu_duyet': 'Không rõ',
        })
        self.assertEqual(response.status_code, 302)
        row.refresh_from_db()
        self.exam.refresh_from_db()
        self.assertEqual(row.trang_thai, 'pending')
        self.assertEqual(self.exam.chan_doan, 'Test')

    def test_patient_cannot_use_correction_submission_route(self):
        self.client.force_login(self.patient.user)
        self.assertEqual(self.client.get(reverse('exam_correction_request', args=[self.exam.pk])).status_code, 403)

    def test_admin_user_form_controls_cross_branch_doctor_permission(self):
        from core.models import BacSi
        account = User.objects.create_user('assigned_doctor')
        doctor = self.appointment.bac_si
        doctor.user = account
        doctor.save(update_fields=['user'])
        data = {
            'username': account.username, 'email': '', 'first_name': '', 'last_name': '',
            'is_active': 'on', 'role': 'bac_si', 'password': '',
            'xem_lich_su_lien_chi_nhanh': 'on',
        }
        form = AdminUserForm(data, instance=account)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        account.refresh_from_db()
        self.assertTrue(account.has_perm('core.view_cross_branch_medical_history'))
