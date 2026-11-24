import datetime

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import BenhNhan, DanhGiaLuotKham, LeTanChiNhanh, LogHeThong
from core.test_vnpay import fixtures


class PatientRatingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin, cls.staff, cls.patient_user, cls.appointment = fixtures()
        cls.patient = cls.appointment.benh_nhan
        cls.url = reverse('patient_rating', args=[cls.appointment.pk])

    def test_patient_can_submit_one_rating_for_own_completed_visit(self):
        self.client.force_login(self.patient_user)
        response = self.client.post(self.url, {'diem': '5', 'nhan_xet': 'Bác sĩ tư vấn rõ ràng.'})
        self.assertEqual(response.status_code, 302)
        rating = DanhGiaLuotKham.objects.get(lich_kham=self.appointment)
        self.assertEqual(rating.nguoi_danh_gia, self.patient_user)
        self.assertEqual(rating.diem, 5)
        self.assertTrue(LogHeThong.objects.filter(model_name='DanhGiaLuotKham', object_id=rating.pk,
                                                   hanh_dong='create').exists())

    def test_patient_can_view_existing_rating_but_cannot_submit_second(self):
        rating = DanhGiaLuotKham.objects.create(
            lich_kham=self.appointment, nguoi_danh_gia=self.patient_user,
            diem=4, nhan_xet='Ổn')
        self.client.force_login(self.patient_user)
        self.assertContains(self.client.get(self.url), 'Ổn')
        self.client.post(self.url, {'diem': '1', 'nhan_xet': 'Thay đổi'})
        rating.refresh_from_db()
        self.assertEqual(rating.diem, 4)
        self.assertEqual(rating.nhan_xet, 'Ổn')
        self.assertEqual(DanhGiaLuotKham.objects.filter(lich_kham=self.appointment).count(), 1)

    def test_non_owner_cannot_rate_or_view_visit(self):
        other_user = User.objects.create_user('other_patient')
        BenhNhan.objects.create(user=other_user, ho_ten='Người khác', ngay_sinh=datetime.date(2000, 1, 1),
                                gioi_tinh='nam', so_dien_thoai='0901000998', dia_chi='Địa chỉ')
        self.client.force_login(other_user)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(DanhGiaLuotKham.objects.count(), 0)

    def test_cancelled_or_not_completed_appointment_cannot_be_rated(self):
        self.client.force_login(self.patient_user)
        for status in ('cho', 'huy'):
            with self.subTest(status=status):
                type(self.appointment).objects.filter(pk=self.appointment.pk).update(trang_thai=status)
                self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertFalse(DanhGiaLuotKham.objects.exists())

    def test_invalid_score_and_oversize_comment_are_rejected(self):
        self.client.force_login(self.patient_user)
        response = self.client.post(self.url, {'diem': '6', 'nhan_xet': 'x' * 1001})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(DanhGiaLuotKham.objects.exists())
        self.assertTrue(response.context['form'].errors)

    def test_admin_can_view_and_hide_rating_with_audit(self):
        rating = DanhGiaLuotKham.objects.create(
            lich_kham=self.appointment, nguoi_danh_gia=self.patient_user,
            diem=2, nhan_xet='Cần cải thiện thời gian chờ.')
        self.client.force_login(self.admin)
        response = self.client.get(reverse('rating_admin_list'))
        self.assertContains(response, 'Cần cải thiện thời gian chờ.')
        response = self.client.post(reverse('rating_admin_list'), {
            'rating_id': rating.pk, 'ghi_chu_quan_tri': 'Ẩn do chứa thông tin cá nhân nhạy cảm.',
        })
        self.assertEqual(response.status_code, 302)
        rating.refresh_from_db()
        self.assertFalse(rating.dang_hien_thi)
        self.assertEqual(rating.quan_tri_vien, self.admin)
        self.assertTrue(LogHeThong.objects.filter(model_name='DanhGiaLuotKham', object_id=rating.pk,
                                                   hanh_dong='update').exists())

    def test_reception_account_cannot_access_rating_admin(self):
        LeTanChiNhanh.objects.create(user=self.staff, chi_nhanh=self.appointment.bac_si.chi_nhanh)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse('rating_admin_list')).status_code, 403)

    def test_patient_history_links_to_rating_for_completed_exam(self):
        self.client.force_login(self.patient_user)
        response = self.client.get(reverse('medical_history'))
        self.assertContains(response, reverse('patient_rating', args=[self.appointment.pk]))
