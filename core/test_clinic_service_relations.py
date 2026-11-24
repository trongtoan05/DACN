import datetime

from django.contrib.gis.geos import Point
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    BacSi,
    BacSiDichVu,
    BenhNhan,
    ChiNhanh,
    DanhMucDichVu,
    DichVuChiNhanh,
    GioLamViecBacSi,
    LichKham,
)


class ClinicServiceRelationsTests(TestCase):
    def setUp(self):
        self.branch = self.make_branch('Chi nhánh mắt trung tâm')
        self.other_branch = self.make_branch('Chi nhánh mắt phía đông', lon=106.8)
        self.doctor = self.make_doctor(self.branch, '0901234567')
        self.patient = BenhNhan.objects.create(
            ho_ten='Nguyễn Bệnh Nhân',
            ngay_sinh=datetime.date(1990, 1, 1),
            gioi_tinh='nu',
            so_dien_thoai='0901234568',
            dia_chi='Địa chỉ kiểm thử',
        )
        self.service = DanhMucDichVu.objects.create(
            ma_dich_vu='MAT_KHAM', ten='Khám mắt tổng quát', nhom='Khám mắt', don_gia=150000,
            cho_phep_dat_lich=True,
        )
        self.branch_service = DichVuChiNhanh.objects.create(
            chi_nhanh=self.branch, dich_vu=self.service,
        )
        self.doctor_service = BacSiDichVu.objects.create(
            bac_si=self.doctor, dich_vu_chi_nhanh=self.branch_service,
        )
        appointment_date = timezone.localdate() + datetime.timedelta(days=14)
        self.appointment_date = appointment_date
        weekday = (appointment_date.weekday() + 1) % 7
        GioLamViecBacSi.objects.create(
            bac_si=self.doctor, thu=weekday,
            gio_bat_dau=datetime.time(8), gio_ket_thuc=datetime.time(17),
        )

    @staticmethod
    def make_branch(name, lon=106.7):
        return ChiNhanh.objects.create(
            ten=name,
            dia_chi='Địa chỉ chi nhánh',
            phuong='Phường kiểm thử',
            vi_tri=Point(lon, 10.77, srid=4326),
            gio_mo=datetime.time(7),
            gio_dong=datetime.time(18),
        )

    @staticmethod
    def make_doctor(branch, phone):
        from core.models import Khoa

        legacy_department = Khoa.objects.create(ten=f'Khoa cũ {phone}', chi_nhanh=branch)
        return BacSi.objects.create(
            ho_ten='Bác sĩ mắt kiểm thử', chuyen_khoa='Mắt', khoa=legacy_department,
            chi_nhanh=branch, so_dien_thoai=phone,
        )

    def appointment(self, *, service=None, doctor=None):
        return LichKham(
            benh_nhan=self.patient,
            bac_si=doctor or self.doctor,
            dich_vu_dat_truoc=service,
            ngay_kham=self.appointment_date,
            gio_kham=datetime.time(9),
        )

    def test_doctor_service_assignment_must_match_doctors_branch(self):
        other_branch_service = DichVuChiNhanh.objects.create(
            chi_nhanh=self.other_branch, dich_vu=self.service,
        )
        for active in (True, False):
            with self.subTest(active=active):
                assignment = BacSiDichVu(
                    bac_si=self.doctor, dich_vu_chi_nhanh=other_branch_service,
                    dang_ap_dung=active,
                )
                with self.assertRaises(ValidationError) as raised:
                    assignment.full_clean()
                self.assertIn('dich_vu_chi_nhanh', raised.exception.message_dict)

    def test_booking_relation_accepts_offered_service_and_eligible_doctor(self):
        appointment = self.appointment(service=self.service)

        appointment.full_clean()

        self.assertEqual(appointment.bac_si.chi_nhanh_id, self.branch_service.chi_nhanh_id)
        self.assertEqual(appointment.dich_vu_dat_truoc_id, self.service.pk)

    def test_booking_rejects_service_not_offered_at_doctors_branch(self):
        other_service = DanhMucDichVu.objects.create(
            ma_dich_vu='MAT_DO_KHUC_XA', ten='Đo khúc xạ', nhom='Chẩn đoán', don_gia=80000,
        )
        DichVuChiNhanh.objects.create(chi_nhanh=self.other_branch, dich_vu=other_service)
        appointment = self.appointment(service=other_service)

        with self.assertRaises(ValidationError) as raised:
            appointment.full_clean()

        self.assertIn('dich_vu_dat_truoc', raised.exception.message_dict)

    def test_booking_rejects_doctor_without_service_assignment(self):
        other_service = DanhMucDichVu.objects.create(
            ma_dich_vu='MAT_DO_KHUC_XA', ten='Đo khúc xạ', nhom='Chẩn đoán', don_gia=80000,
        )
        DichVuChiNhanh.objects.create(chi_nhanh=self.branch, dich_vu=other_service)

        with self.assertRaises(ValidationError) as raised:
            self.appointment(service=other_service).full_clean()

        self.assertIn('dich_vu_dat_truoc', raised.exception.message_dict)

    def test_inactive_catalog_or_branch_service_cannot_be_booked(self):
        self.branch_service.dang_ap_dung = False
        self.branch_service.save(update_fields=['dang_ap_dung', 'ngay_cap_nhat'])

        with self.assertRaises(ValidationError) as raised:
            self.appointment(service=self.service).full_clean()

        self.assertIn('dich_vu_dat_truoc', raised.exception.message_dict)

    def test_inactive_catalog_service_cannot_be_booked(self):
        self.service.dang_ap_dung = False
        self.service.save(update_fields=['dang_ap_dung', 'ngay_cap_nhat'])

        with self.assertRaises(ValidationError) as raised:
            self.appointment(service=self.service).full_clean()

        self.assertIn('dich_vu_dat_truoc', raised.exception.message_dict)

    def test_inactive_doctor_assignment_cannot_be_booked(self):
        self.doctor_service.dang_ap_dung = False
        self.doctor_service.save(update_fields=['dang_ap_dung', 'ngay_cap_nhat'])

        with self.assertRaises(ValidationError) as raised:
            self.appointment(service=self.service).full_clean()

        self.assertIn('dich_vu_dat_truoc', raised.exception.message_dict)

    def test_non_bookable_service_cannot_be_selected_for_new_booking(self):
        self.service.cho_phep_dat_lich = False
        self.service.save()
        with self.assertRaises(ValidationError) as raised:
            self.appointment(service=self.service).full_clean()
        self.assertIn('dich_vu_dat_truoc', raised.exception.message_dict)

    def test_inactive_branch_rejects_new_booking(self):
        self.branch.dang_hoat_dong = False
        self.branch.save()
        with self.assertRaises(ValidationError) as raised:
            self.appointment().full_clean()
        self.assertIn('bac_si', raised.exception.message_dict)

    def test_existing_booking_is_preserved_when_relations_are_disabled(self):
        appointment = self.appointment(service=self.service)
        appointment.save()
        self.branch.dang_hoat_dong = False
        self.branch.save()
        self.branch_service.dang_ap_dung = False
        self.branch_service.save()
        self.doctor_service.dang_ap_dung = False
        self.doctor_service.save()
        appointment.refresh_from_db()
        appointment.full_clean()
        self.assertEqual(appointment.dich_vu_dat_truoc_id, self.service.pk)

    def test_assignment_and_branch_service_are_retained_not_deleted(self):
        with self.assertRaises(ProtectedError):
            self.branch_service.delete()
        with self.assertRaises(ProtectedError):
            self.doctor_service.delete()

    def test_duplicate_branch_service_and_doctor_assignment_are_rejected(self):
        duplicate_service = DichVuChiNhanh(chi_nhanh=self.branch, dich_vu=self.service)
        with self.assertRaises(ValidationError):
            duplicate_service.full_clean()
        duplicate_assignment = BacSiDichVu(
            bac_si=self.doctor, dich_vu_chi_nhanh=self.branch_service,
        )
        with self.assertRaises(ValidationError):
            duplicate_assignment.full_clean()

    def test_legacy_appointment_without_booked_service_remains_valid(self):
        appointment = self.appointment()

        appointment.full_clean()

        self.assertIsNone(appointment.dich_vu_dat_truoc_id)

    def test_global_admin_can_open_new_relation_management_pages(self):
        admin = User.objects.create_superuser('clinic_admin', 'admin@example.invalid', 'Safe-Test-Pass@2026')
        self.client.force_login(admin)

        for model_key in ('dich-vu-chi-nhanh', 'bac-si-dich-vu'):
            with self.subTest(model_key=model_key):
                listing = self.client.get(reverse('custom_admin_model_list', args=[model_key]))
                form = self.client.get(reverse('custom_admin_model_create', args=[model_key]))
                self.assertEqual(listing.status_code, 200)
                self.assertEqual(form.status_code, 200)

    def test_assignment_form_scopes_choices_to_selected_branch(self):
        admin = User.objects.create_superuser('scope_admin', 'scope@example.invalid', 'Safe-Test-Pass@2026')
        self.client.force_login(admin)
        response = self.client.get(
            reverse('custom_admin_model_create', args=['bac-si-dich-vu']),
            {'chi_nhanh': self.branch.pk},
        )
        form = response.context['form']
        self.assertEqual(list(form.fields['bac_si'].queryset), [self.doctor])
        self.assertEqual(list(form.fields['dich_vu_chi_nhanh'].queryset), [self.branch_service])

    def test_relation_edit_reports_future_appointments_without_cancelling(self):
        admin = User.objects.create_superuser('impact_admin', 'impact@example.invalid', 'Safe-Test-Pass@2026')
        self.client.force_login(admin)
        appointment = self.appointment(service=self.service)
        appointment.save()
        response = self.client.get(
            reverse('custom_admin_model_edit', args=['dich-vu-chi-nhanh', self.branch_service.pk]),
        )
        self.assertEqual(response.context['future_relation_appointments'], 1)
        self.branch_service.dang_ap_dung = False
        self.branch_service.save()
        appointment.refresh_from_db()
        self.assertNotEqual(appointment.trang_thai, 'huy')

    def test_relation_crud_keeps_history_and_denies_regular_user(self):
        admin = User.objects.create_superuser('relations_admin', 'relations@example.invalid', 'Safe-Test-Pass@2026')
        self.client.force_login(admin)
        response = self.client.post(
            reverse('custom_admin_model_create', args=['dich-vu-chi-nhanh']),
            {'chi_nhanh': self.branch.pk, 'dich_vu': self.service.pk, 'dang_ap_dung': 'on'},
        )
        self.assertEqual(response.status_code, 200)  # duplicate pair is rejected by the unique constraint
        self.assertTrue(response.context['form'].errors)

        self.client.post(
            reverse('custom_admin_model_edit', args=['dich-vu-chi-nhanh', self.branch_service.pk]),
            {'dang_ap_dung': ''},
        )
        self.branch_service.refresh_from_db()
        self.assertFalse(self.branch_service.dang_ap_dung)
        delete_response = self.client.post(
            reverse('custom_admin_model_delete', args=['dich-vu-chi-nhanh', self.branch_service.pk]),
        )
        self.assertEqual(delete_response.status_code, 403)
        self.assertTrue(DichVuChiNhanh.objects.filter(pk=self.branch_service.pk).exists())

        regular = User.objects.create_user('relations_regular')
        self.client.force_login(regular)
        denied = self.client.post(
            reverse('custom_admin_model_create', args=['bac-si-dich-vu']),
            {'chi_nhanh': self.branch.pk, 'bac_si': self.doctor.pk,
             'dich_vu_chi_nhanh': self.branch_service.pk, 'dang_ap_dung': 'on'},
        )
        self.assertEqual(denied.status_code, 302)

    def test_branch_form_creates_inactive_branch_without_hidden_contact_fields(self):
        admin = User.objects.create_superuser('branch_admin', 'branch@example.invalid', 'Safe-Test-Pass@2026')
        self.client.force_login(admin)
        response = self.client.post(
            reverse('custom_admin_model_create', args=['chi-nhanh']),
            {
                'ten': 'Chi nhánh mới', 'dia_chi': 'Địa chỉ mới', 'so_dien_thoai': '0901234000',
                'email': 'branch@example.invalid', 'phuong': 'Phường thử', 'lat': '10.77', 'lon': '106.70',
                'dang_hoat_dong': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        branch = ChiNhanh.objects.get(ten='Chi nhánh mới')
        self.assertEqual(branch.so_dien_thoai, '')
        self.assertEqual(branch.email, '')
        self.assertFalse(branch.dang_hoat_dong)

