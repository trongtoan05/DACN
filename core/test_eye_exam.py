import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.gis.geos import Point
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (BacSi, BenhNhan, ChiNhanh, ChiTietKhamMat, DonKinh, DonThuoc,
                         BacSiDichVu, DichVuChiNhanh, DanhMucDichVu, GiaoDichThanhToan,
                         HoaDon, Khoa, LichKham, PhieuKham)
from core.exam_services.services import revision


@override_settings(ALLOWED_HOSTS=['testserver'])
class EyeExamWorkflowTests(TestCase):
    def setUp(self):
        self.branch = ChiNhanh.objects.create(
            ten='Chi nhanh kiem thu', dia_chi='1 Duong Test', phuong='Quan 1',
            vi_tri=Point(106.7, 10.77, srid=4326),
            gio_mo=datetime.time(7), gio_dong=datetime.time(17),
        )
        self.department = Khoa.objects.create(ten='Khoa mat', chi_nhanh=self.branch)
        self.doctor_user = User.objects.create_user('eyeDoctor123', password='Test-password-123!')
        self.other_user = User.objects.create_user('otherDoctor123', password='Test-password-123!')
        self.patient_user = User.objects.create_user('eyePatient123', password='Test-password-123!')
        self.doctor = BacSi.objects.create(user=self.doctor_user, ho_ten='Bac si mat', khoa=self.department,
            chi_nhanh=self.branch, chuyen_khoa='Mat', so_dien_thoai='0901000001')
        self.service = DanhMucDichVu.objects.create(
            ma_dich_vu='EYE_EXAM_TEST', ten='Kham mat', nhom='Kham mat', don_gia=100000,
        )
        branch_service = DichVuChiNhanh.objects.create(chi_nhanh=self.branch, dich_vu=self.service)
        BacSiDichVu.objects.create(bac_si=self.doctor, dich_vu_chi_nhanh=branch_service)
        BacSi.objects.create(user=self.other_user, ho_ten='Bac si khac', khoa=self.department,
            chi_nhanh=self.branch, chuyen_khoa='Mat', so_dien_thoai='0901000002')
        self.patient = BenhNhan.objects.create(user=self.patient_user, ho_ten='Benh nhan thu',
            ngay_sinh=datetime.date(1990, 1, 1), gioi_tinh='nam', so_dien_thoai='0901000003', dia_chi='TPHCM')
        self.appointment = LichKham.objects.create(benh_nhan=self.patient, bac_si=self.doctor,
            ngay_kham=timezone.localdate(), gio_kham=datetime.time(9), trang_thai='cho',
            trang_thai_tiep_nhan='da_tiep_nhan')
        now = timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0)
        frozen = patch('django.utils.timezone.now', return_value=now)
        frozen.start()
        self.addCleanup(frozen.stop)

    def login_doctor(self):
        self.client.force_login(self.doctor_user)

    def test_doctor_schedule_uses_eyeviora_shell_and_real_booked_services(self):
        self.appointment.dich_vu_dat_truoc_nhieu.set([self.service])
        self.login_doctor()
        response = self.client.get(reverse('bac_si_home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'core/css/doctor.css')
        self.assertContains(response, 'core/img/EyeViora/logoEyeViora.png')
        self.assertContains(response, self.patient.ho_ten)
        self.assertContains(response, self.service.ten)
        self.assertContains(response, self.branch.ten)
        self.assertContains(response, 'data-doctor-menu')

    def test_doctor_exam_screen_keeps_completion_only_action(self):
        self.start()
        response = self.client.get(reverse('doctor_exam', args=[self.appointment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ho&#224;n th&#224;nh kh&#225;m')
        self.assertNotContains(response, 'save_draft')
        self.assertContains(response, self.patient.ho_ten)

    def test_completed_exam_is_eye_viora_read_only_for_assigned_doctor(self):
        self.start()
        self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), self.complete_payload())
        exam = PhieuKham.objects.get(lich_kham=self.appointment)
        response = self.client.get(reverse('phieu_kham_detail', args=[exam.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'doctor-workspace')
        self.assertContains(response, 'doctor-sidebar')
        self.assertContains(response, 'core/css/doctor.css')
        self.assertNotContains(response, 'action="complete"')

    def test_doctor_patient_page_hides_obsolete_insurance_field(self):
        self.login_doctor()
        response = self.client.get(reverse('bac_si_patient_detail', args=[self.patient.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'doctor-sidebar')
        self.assertNotContains(response, 'BHYT')

    def start(self):
        self.login_doctor()
        return self.client.post(reverse('start_exam', args=[self.appointment.pk]))

    def post_legacy_draft(self, payload=None):
        data = {'action': 'save_draft', **(payload or {})}
        return self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), data)

    def complete_payload(self, extra=None):
        return {'action': 'complete', 'trieu_chung': 'Mo mat', 'chan_doan': 'Can theo doi',
                'huong_dieu_tri': 'Tai kham theo hen', 'services-TOTAL_FORMS': '1',
                'services-INITIAL_FORMS': '0', 'services_revision': revision([]),
                'services-0-dich_vu': str(self.service.pk), 'services-0-so_luong': '1',
                'services-0-ghi_chu': '', **(extra or {})}

    def test_exam_form_explains_service_confirmation_and_warns_before_completion(self):
        self.start()

        response = self.client.get(reverse('doctor_exam', args=[self.appointment.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'doctor-service-note')
        self.assertContains(response, 'window.confirm')
        self.assertContains(response, 'name="action" value="complete"')
        self.assertNotContains(response, 'save_draft')

    def test_legacy_draft_action_is_rejected_without_saving_partial_results(self):
        self.start()
        response = self.post_legacy_draft({'eye-thi_luc_phai_khong_kinh': '6/12', 'eye-nhan_ap_trai': '17.5'})
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, 'dang')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Thao tác phiếu khám không hợp lệ.', response.content.decode('utf-8-sig'))
        self.assertContains(response, '6/12')
        self.assertFalse(PhieuKham.objects.filter(lich_kham=self.appointment).exists())
        self.assertFalse(ChiTietKhamMat.objects.exists())

    def test_incomplete_completion_keeps_exam_unstarted_and_creates_no_records(self):
        self.start()
        response = self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), {
            'action': 'complete', 'eye-thi_luc_phai_khong_kinh': '',
        })
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, 'dang')
        self.assertIn('Để hoàn tất, vui lòng nhập triệu chứng', response.content.decode('utf-8-sig'))
        self.assertFalse(PhieuKham.objects.filter(lich_kham=self.appointment).exists())
        self.assertFalse(ChiTietKhamMat.objects.exists())

    def test_axis_and_pressure_validation_reject_out_of_range_measurements(self):
        self.start()
        for values in ({'eye-axis_phai': '181'}, {'eye-nhan_ap_phai': '101'}, {'eye-sph_trai': '31'}):
            response = self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), self.complete_payload(values))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(PhieuKham.objects.filter(lich_kham=self.appointment).exists())

    def test_visual_acuity_rejects_unstructured_value(self):
        self.start()
        response = self.client.post(reverse('doctor_exam', args=[self.appointment.pk]),
                                    self.complete_payload({'eye-thi_luc_phai_khong_kinh': '<script>'}))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PhieuKham.objects.filter(lich_kham=self.appointment).exists())

    def test_axis_requires_cylinder(self):
        self.start()
        response = self.client.post(reverse('doctor_exam', args=[self.appointment.pk]),
                                    self.complete_payload({'eye-axis_trai': '90'}))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PhieuKham.objects.filter(lich_kham=self.appointment).exists())

    def test_invalid_glasses_order_requires_doctor_confirmation(self):
        self.start()
        response = self.client.post(reverse('doctor_exam', args=[self.appointment.pk]),
                                    self.complete_payload({'glasses-sph_phai': '-1.25'}))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(DonKinh.objects.exists())

    def test_glasses_order_is_explicitly_confirmed_and_stored_separately(self):
        self.start()
        payload = self.complete_payload({'glasses-sph_phai': '-1.25', 'glasses-cyl_trai': '-0.50',
            'glasses-axis_trai': '90', 'glasses-bac_si_xac_nhan': 'on'})
        response = self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), payload)
        self.assertEqual(response.status_code, 302)
        prescription = DonKinh.objects.get(phieu_kham__lich_kham=self.appointment)
        self.assertEqual(prescription.sph_phai, Decimal('-1.25'))
        self.assertEqual(prescription.cyl_trai, Decimal('-0.50'))
        self.assertTrue(prescription.bac_si_xac_nhan)

    def test_medicine_requires_name_dose_and_instructions(self):
        self.start()
        payload = self.complete_payload({'medicine-TOTAL_FORMS': '2', 'medicine-INITIAL_FORMS': '0',
                                         'medicine-0-ten_thuoc': 'Thuoc A'})
        response = self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(DonThuoc.objects.exists())

    def test_medicine_order_persists_in_separate_rows(self):
        self.start()
        payload = self.complete_payload({'medicine-TOTAL_FORMS': '2', 'medicine-INITIAL_FORMS': '0',
            'medicine-0-ten_thuoc': 'Thuoc A', 'medicine-0-lieu_dung': '1 vien/ngay',
            'medicine-0-huong_dan': 'Theo chi dinh', 'medicine-0-thoi_gian_dung': '5 ngay'})
        response = self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), payload)
        self.assertEqual(response.status_code, 302)
        item = DonThuoc.objects.get(phieu_kham__lich_kham=self.appointment)
        self.assertEqual(item.ten_thuoc, 'Thuoc A')
        self.assertEqual(item.thoi_gian_dung, '5 ngay')

    def test_completion_requires_clinical_summary_and_does_not_duplicate_exam(self):
        self.start()
        self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), {'action': 'complete'})
        self.assertEqual(self.appointment.__class__.objects.get(pk=self.appointment.pk).trang_thai, 'dang')
        self.assertFalse(PhieuKham.objects.filter(lich_kham=self.appointment).exists())
        self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), self.complete_payload())
        self.assertEqual(PhieuKham.objects.filter(lich_kham=self.appointment).count(), 1)
        self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), self.complete_payload())
        self.assertEqual(PhieuKham.objects.filter(lich_kham=self.appointment).count(), 1)

    def test_other_doctor_cannot_edit_and_patient_can_read_own_completed_exam(self):
        self.start()
        self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), self.complete_payload())
        exam = PhieuKham.objects.get(lich_kham=self.appointment)
        self.client.force_login(self.other_user)
        self.assertEqual(self.client.get(reverse('bac_si_phieu_kham_edit', args=[exam.pk])).status_code, 404)
        self.client.force_login(self.patient_user)
        self.assertEqual(self.client.get(reverse('phieu_kham_detail', args=[exam.pk])).status_code, 200)
        history = self.client.get(reverse('medical_history'))
        self.assertContains(history, 'Can theo doi')

    def test_patient_can_read_own_eye_results_only_after_completion(self):
        self.start()
        payload = self.complete_payload({'eye-thi_luc_phai_co_kinh': '20/20'})
        self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), payload)
        exam = PhieuKham.objects.get(lich_kham=self.appointment)
        self.client.force_login(self.patient_user)
        response = self.client.get(reverse('phieu_kham_detail', args=[exam.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '20/20')
        self.assertEqual(self.client.post(reverse('phieu_kham_detail', args=[exam.pk])).status_code, 405)

    def test_patient_cannot_read_another_patients_exam(self):
        self.start()
        self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), self.complete_payload())
        exam = PhieuKham.objects.get(lich_kham=self.appointment)
        other_user = User.objects.create_user('anotherPatient123', password='Test-password-123!')
        BenhNhan.objects.create(user=other_user, ho_ten='Benh nhan khac', ngay_sinh=datetime.date(1990, 1, 1),
            gioi_tinh='nu', so_dien_thoai='0901000004', dia_chi='TPHCM')
        self.client.force_login(other_user)
        self.assertEqual(self.client.get(reverse('phieu_kham_detail', args=[exam.pk])).status_code, 404)

    def test_completed_exam_is_read_only_and_does_not_change_billing(self):
        self.start()
        self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), self.complete_payload())
        exam = PhieuKham.objects.get(lich_kham=self.appointment)
        before = (exam.chan_doan, HoaDon.objects.count(), GiaoDichThanhToan.objects.count())
        response = self.client.post(reverse('bac_si_phieu_kham_edit', args=[exam.pk]),
                                    {'action': 'save_draft', 'chan_doan': 'Changed'})
        self.assertEqual(response.status_code, 302)
        exam.refresh_from_db()
        self.assertEqual((exam.chan_doan, HoaDon.objects.count(), GiaoDichThanhToan.objects.count()), before)

    def test_receptionist_cannot_edit_clinical_results(self):
        self.start()
        self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), self.complete_payload())
        exam = PhieuKham.objects.get(lich_kham=self.appointment)
        frontdesk = User.objects.create_user('frontdesk123', password='Test-password-123!')
        self.client.force_login(frontdesk)
        detail = self.client.get(reverse('phieu_kham_detail', args=[exam.pk]))
        self.assertNotContains(detail, 'Can theo doi')
        self.assertEqual(self.client.post(reverse('bac_si_phieu_kham_edit', args=[exam.pk]),
                                          {'chan_doan': 'Changed'}).status_code, 302)
        exam.refresh_from_db()
        self.assertEqual(exam.chan_doan, 'Can theo doi')
