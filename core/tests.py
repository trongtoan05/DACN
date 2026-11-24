import datetime

from django.core import mail
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.contrib.auth.models import User
from django.contrib.gis.geos import Point
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.forms import AdminChiNhanhForm, AdminUserForm
from core.models import (
    BacSi,
    BacSiDichVu,
    BenhNhan,
    ChiNhanh,
    DanhMucDichVu,
    DichVuChiNhanh,
    GioLamViecBacSi,
    GioLamViecChiNhanh,
    HoaDon,
    Khoa,
    LichKham,
    PhieuKham,
    QuanLyChiNhanh,
)


class DoctorFlowTests(TestCase):
    def setUp(self):
        self.clinic = ChiNhanh.objects.create(
            ten="Benh vien test",
            dia_chi="1 Duong Test",
            phuong="Quan 1",
            vi_tri=Point(106.7, 10.77, srid=4326),
            gio_mo=datetime.time(7, 0),
            gio_dong=datetime.time(17, 0),
        )
        self.department = Khoa.objects.create(
            ten="Noi tong quat",
            chi_nhanh=self.clinic,
        )

        self.doctor_user = User.objects.create_user(
            username="doctor1",
            password="pass12345",
        )
        self.other_doctor_user = User.objects.create_user(
            username="doctor2",
            password="pass12345",
        )
        self.patient_user = User.objects.create_user(
            username="0909000999",
            password="pass12345",
        )

        self.doctor = BacSi.objects.create(
            user=self.doctor_user,
            ho_ten="Bac si A",
            chuyen_khoa="Noi tong quat",
            khoa=self.department,
            chi_nhanh=self.clinic,
            so_dien_thoai="0909000001",
        )
        self.other_doctor = BacSi.objects.create(
            user=self.other_doctor_user,
            ho_ten="Bac si B",
            chuyen_khoa="Noi tong quat",
            khoa=self.department,
            chi_nhanh=self.clinic,
            so_dien_thoai="0909000002",
        )
        self.patient = BenhNhan.objects.create(
            user=self.patient_user,
            ho_ten="Benh nhan A",
            ngay_sinh=datetime.date(1995, 1, 1),
            gioi_tinh="nam",
            so_dien_thoai=self.patient_user.username,
            dia_chi="TP.HCM",
        )

        self.appointment = LichKham.objects.create(
            benh_nhan=self.patient,
            bac_si=self.doctor,
            ngay_kham=timezone.localdate(),
            gio_kham=datetime.time(9, 0),
            trang_thai="cho",
            trang_thai_tiep_nhan="da_tiep_nhan",
            ghi_chu="",
        )
        from unittest.mock import patch
        clock = patch('django.utils.timezone.now', return_value=timezone.localtime().replace(hour=10, minute=0, second=0))
        clock.start()
        self.addCleanup(clock.stop)

    def test_start_exam_requires_post(self):
        self.client.login(username="doctor1", password="pass12345")

        response = self.client.get(reverse("start_exam", args=[self.appointment.id]))

        self.assertEqual(response.status_code, 405)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, "cho")

    def test_start_exam_post_updates_status(self):
        self.client.login(username="doctor1", password="pass12345")

        response = self.client.post(reverse("start_exam", args=[self.appointment.id]))

        self.assertRedirects(response, reverse('doctor_exam', args=[self.appointment.pk]))
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, "dang")

    def test_start_exam_permission_enforced(self):
        self.client.login(username="doctor2", password="pass12345")

        response = self.client.post(reverse("start_exam", args=[self.appointment.id]))

        self.assertEqual(response.status_code, 404)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, "cho")

    def test_start_exam_patient_is_redirected_home(self):
        self.client.login(username=self.patient_user.username, password="pass12345")

        response = self.client.post(reverse("start_exam", args=[self.appointment.id]))

        self.assertRedirects(response, reverse("home"))

    def test_doctor_exam_creates_phieu_and_marks_done(self):
        from core.models import DanhMucDichVu
        from core.exam_services.services import revision
        self.client.login(username="doctor1", password="pass12345")
        self.client.post(reverse('start_exam', args=[self.appointment.pk]))
        service = DanhMucDichVu.objects.create(ma_dich_vu='FLOW_FEE', ten='Khám', nhom='Khám bệnh', don_gia=100000)
        branch_service = DichVuChiNhanh.objects.create(chi_nhanh=self.clinic, dich_vu=service)
        BacSiDichVu.objects.create(bac_si=self.doctor, dich_vu_chi_nhanh=branch_service)
        payload = {
            "trieu_chung": "Dau bung",
            "chan_doan": "Viem da day",
            "huong_dieu_tri": "Uong thuoc",
            'services-TOTAL_FORMS': '1', 'services-INITIAL_FORMS': '0',
            'services_revision': revision([]), 'services-0-dich_vu': str(service.pk), 'services-0-so_luong': '1',
        }

        response = self.client.post(reverse("doctor_exam", args=[self.appointment.id]), payload)

        self.assertRedirects(response, reverse("bac_si_home"))
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, "xong")

        phieu = PhieuKham.objects.get(lich_kham=self.appointment)
        self.assertEqual(phieu.benh_nhan, self.patient)
        self.assertEqual(phieu.chan_doan, "Viem da day")
        invoice = HoaDon.objects.get(lich_kham=self.appointment)
        self.assertEqual((invoice.so_tien, invoice.trang_thai), (100000, HoaDon.TrangThai.CHUA_THANH_TOAN))

    def test_doctor_exam_rejects_empty_payload(self):
        self.client.login(username="doctor1", password="pass12345")

        response = self.client.post(
            reverse("doctor_exam", args=[self.appointment.id]),
            {"trieu_chung": "", "chan_doan": "", "huong_dieu_tri": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(PhieuKham.objects.filter(lich_kham=self.appointment).exists())
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.trang_thai, "cho")

    def test_doctor_exam_permission_enforced(self):
        self.client.login(username="doctor2", password="pass12345")

        response = self.client.get(reverse("doctor_exam", args=[self.appointment.id]))

        self.assertEqual(response.status_code, 404)


class AppointmentBookingTests(TestCase):
    def setUp(self):
        self.clinic = ChiNhanh.objects.create(
            ten="Benh vien A",
            dia_chi="1 Duong A",
            phuong="Quan 1",
            vi_tri=Point(106.7, 10.77, srid=4326),
            gio_mo=datetime.time(7, 0),
            gio_dong=datetime.time(17, 0),
        )
        self.other_clinic = ChiNhanh.objects.create(
            ten="Benh vien B",
            dia_chi="2 Duong B",
            phuong="Quan 3",
            vi_tri=Point(106.68, 10.79, srid=4326),
            gio_mo=datetime.time(7, 0),
            gio_dong=datetime.time(17, 0),
        )
        self.enterContext(override_settings(PUBLIC_CLINIC_BRANCH_IDS=(self.clinic.pk, self.other_clinic.pk)))
        self.department = Khoa.objects.create(ten="Noi", chi_nhanh=self.clinic)
        self.other_department = Khoa.objects.create(ten="Ngoai", chi_nhanh=self.other_clinic)

        self.doctor = BacSi.objects.create(
            ho_ten="Bac si A",
            chuyen_khoa="Noi",
            khoa=self.department,
            chi_nhanh=self.clinic,
            so_dien_thoai="0911000001",
        )
        self.other_doctor = BacSi.objects.create(
            ho_ten="Bac si B",
            chuyen_khoa="Ngoai",
            khoa=self.other_department,
            chi_nhanh=self.other_clinic,
            so_dien_thoai="0911000002",
        )

        # Thu 2 full-day working window so booking validation can pass.
        GioLamViecBacSi.objects.create(
            bac_si=self.doctor,
            thu=1,
            gio_bat_dau=datetime.time(7, 0),
            gio_ket_thuc=datetime.time(17, 0),
            nghi=False,
        )
        GioLamViecChiNhanh.objects.create(
            chi_nhanh=self.clinic,
            thu=1,
            gio_mo=datetime.time(7, 0),
            gio_dong=datetime.time(17, 0),
            nghi=False,
        )

        self.user = User.objects.create_user(username="0911999999", password="pass12345")
        self.patient = BenhNhan.objects.create(
            user=self.user,
            ho_ten="Benh nhan test",
            ngay_sinh=datetime.date(1999, 1, 1),
            gioi_tinh="nam",
            so_dien_thoai=self.user.username,
            dia_chi="TP.HCM",
        )
        self.client.login(username=self.user.username, password="pass12345")

        # Find upcoming Monday for deterministic schedule validation.
        today = timezone.localdate()
        days_ahead = (0 - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        self.next_monday = today + datetime.timedelta(days=days_ahead)

        self.service = DanhMucDichVu.objects.create(
            ma_dich_vu="KHAM_MAT", ten="Khám mắt tổng quát", nhom="Khám mắt",
            don_gia=100000, cho_phep_dat_lich=True,
        )
        self.branch_service = DichVuChiNhanh.objects.create(chi_nhanh=self.clinic, dich_vu=self.service)
        BacSiDichVu.objects.create(bac_si=self.doctor, dich_vu_chi_nhanh=self.branch_service)

    def booking_data(self, *, doctor=None, service=None, time="09:00"):
        return {
            "chi_nhanh": self.clinic.pk,
            "dich_vu_dat_truoc_nhieu": [service or self.service.pk],
            "bac_si": doctor or self.doctor.pk,
            "ngay_kham": self.next_monday.isoformat(),
            "gio_kham": time,
            "ghi_chu": "",
        }

    def test_booking_does_not_require_department_and_stores_service(self):
        response = self.client.post(
            reverse("dat_lich", args=[self.clinic.id]),
            self.booking_data(),
        )

        self.assertRedirects(response, reverse("upcoming_appointments"))
        appointment = LichKham.objects.get(benh_nhan=self.patient)
        self.assertEqual(appointment.dich_vu_dat_truoc_id, self.service.pk)
        self.assertFalse(HoaDon.objects.filter(lich_kham=appointment).exists())

    def test_booking_rejects_doctor_outside_selected_clinic(self):
        response = self.client.post(
            reverse("dat_lich", args=[self.clinic.id]),
            self.booking_data(doctor=self.other_doctor.pk),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("bac_si", response.context["form"].errors)
        self.assertFalse(LichKham.objects.filter(benh_nhan=self.patient).exists())

    def test_booking_success_redirects_to_upcoming_appointments(self):
        response = self.client.post(
            reverse("dat_lich", args=[self.clinic.id]),
            {**self.booking_data(), "ghi_chu": "Tai kham"},
        )

        self.assertRedirects(response, reverse("upcoming_appointments"))
        self.assertTrue(
            LichKham.objects.filter(
                benh_nhan=self.patient,
                bac_si=self.doctor,
                ngay_kham=self.next_monday,
                gio_kham=datetime.time(9, 0),
            ).exists()
        )
        appointment = LichKham.objects.get(benh_nhan=self.patient, ngay_kham=self.next_monday)
        self.assertEqual(appointment.dich_vu_dat_truoc_id, self.service.pk)

    def test_booking_can_reuse_slot_when_previous_was_cancelled(self):
        LichKham.objects.create(
            benh_nhan=self.patient,
            bac_si=self.doctor,
            ngay_kham=self.next_monday,
            gio_kham=datetime.time(10, 0),
            trang_thai="huy",
            ghi_chu="Da huy",
        )

        response = self.client.post(
            reverse("dat_lich", args=[self.clinic.id]),
            {**self.booking_data(time="10:00"), "ghi_chu": "Dat lai"},
        )

        self.assertRedirects(response, reverse("upcoming_appointments"))
        self.assertEqual(
            LichKham.objects.filter(
                benh_nhan=self.patient,
                bac_si=self.doctor,
                ngay_kham=self.next_monday,
                gio_kham=datetime.time(10, 0),
            ).count(),
            2,
        )

    def test_booking_rejects_non_bookable_and_wrong_branch_services(self):
        self.service.cho_phep_dat_lich = False
        self.service.save()
        response = self.client.post(reverse("dat_lich", args=[self.clinic.pk]), self.booking_data())
        self.assertEqual(response.status_code, 200)
        self.assertIn("dich_vu_dat_truoc_nhieu", response.context["form"].errors)
        self.service.cho_phep_dat_lich = True
        self.service.save()

        other_service = DanhMucDichVu.objects.create(
            ma_dich_vu="KHAM_KHUC_XA", ten="Khám khúc xạ", nhom="Khám mắt",
            don_gia=120000, cho_phep_dat_lich=True,
        )
        response = self.client.post(
            reverse("dat_lich", args=[self.clinic.pk]),
            self.booking_data(service=other_service.pk),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("dich_vu_dat_truoc_nhieu", response.context["form"].errors)
        self.assertFalse(LichKham.objects.filter(benh_nhan=self.patient).exists())

    def test_booking_rejects_unassigned_doctor_and_unavailable_slot(self):
        BacSiDichVu.objects.filter(bac_si=self.doctor).update(dang_ap_dung=False)
        response = self.client.post(reverse("dat_lich", args=[self.clinic.pk]), self.booking_data())
        self.assertEqual(response.status_code, 200)
        self.assertIn("bac_si", response.context["form"].errors)
        BacSiDichVu.objects.filter(bac_si=self.doctor).update(dang_ap_dung=True)
        response = self.client.post(
            reverse("dat_lich", args=[self.clinic.pk]), self.booking_data(time="09:15"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("gio_kham", response.context["form"].errors)

    def test_picker_api_and_branch_service_doctor_filtering(self):
        response = self.client.get(reverse("dat_lich_picker"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["chi_nhanhs"]), [self.clinic, self.other_clinic])
        response = self.client.get(reverse("appointment_options_api"), {"chi_nhanh": self.clinic.pk})
        self.assertEqual([row["id"] for row in response.json()["services"]], [self.service.pk])
        response = self.client.get(reverse("appointment_options_api"), {
            "chi_nhanh": self.clinic.pk, "dich_vu_dat_truoc": self.service.pk,
        })
        self.assertEqual([row["id"] for row in response.json()["doctors"]], [self.doctor.pk])
        response = self.client.get(reverse("appointment_options_api"), {
            "chi_nhanh": self.clinic.pk, "dich_vu_dat_truoc": self.service.pk,
            "bac_si": self.doctor.pk, "ngay_kham": self.next_monday.isoformat(),
        })
        self.assertIn("09:00", response.json()["slots"])
        other_patient = BenhNhan.objects.create(
            ho_ten="Bệnh nhân khác", ngay_sinh=datetime.date(1990, 1, 1), gioi_tinh="nu",
            so_dien_thoai="0911999998", dia_chi="TP.HCM",
        )
        LichKham.objects.create(
            benh_nhan=other_patient, bac_si=self.doctor, ngay_kham=self.next_monday,
            gio_kham=datetime.time(9),
        )
        response = self.client.get(reverse("appointment_options_api"), {
            "chi_nhanh": self.clinic.pk, "dich_vu_dat_truoc": self.service.pk,
            "bac_si": self.doctor.pk, "ngay_kham": self.next_monday.isoformat(),
        })
        self.assertNotIn("09:00", response.json()["slots"])

    def test_post_cannot_book_for_another_patient(self):
        other_patient = BenhNhan.objects.create(
            ho_ten="Bệnh nhân khác", ngay_sinh=datetime.date(1990, 1, 1), gioi_tinh="nu",
            so_dien_thoai="0911999998", dia_chi="TP.HCM",
        )
        response = self.client.post(
            reverse("dat_lich", args=[self.clinic.pk]),
            {**self.booking_data(), "benh_nhan": other_patient.pk},
        )
        self.assertRedirects(response, reverse("upcoming_appointments"))
        appointment = LichKham.objects.get(benh_nhan=self.patient)
        self.assertNotEqual(appointment.benh_nhan_id, other_patient.pk)

    def test_existing_legacy_null_service_is_displayed_without_backfill(self):
        legacy = LichKham.objects.create(
            benh_nhan=self.patient, bac_si=self.doctor, ngay_kham=self.next_monday,
            gio_kham=datetime.time(10), ghi_chu="Lịch cũ",
        )
        response = self.client.get(reverse("upcoming_appointments"))
        self.assertContains(response, "Lịch cũ chưa ghi nhận dịch vụ đặt trước")
        legacy.refresh_from_db()
        self.assertIsNone(legacy.dich_vu_dat_truoc_id)

    def test_inactive_doctor_account_is_not_offered_or_accepted(self):
        doctor_user = User.objects.create_user("inactive-doctor")
        self.doctor.user = doctor_user
        self.doctor.save()
        doctor_user.is_active = False
        doctor_user.save(update_fields=["is_active"])
        response = self.client.get(reverse("appointment_options_api"), {
            "chi_nhanh": self.clinic.pk, "dich_vu_dat_truoc": self.service.pk,
        })
        self.assertEqual(response.json()["doctors"], [])
        response = self.client.post(
            reverse("dat_lich", args=[self.clinic.pk]), self.booking_data(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("bac_si", response.context["form"].errors)

    def test_inactive_branch_rejects_booking_and_is_not_listed(self):
        self.clinic.dang_hoat_dong = False
        self.clinic.save()
        response = self.client.post(reverse("dat_lich", args=[self.clinic.pk]), self.booking_data())
        self.assertEqual(response.status_code, 200)
        self.assertIn("chi_nhanh", response.context["form"].errors)
        self.assertNotIn(self.clinic, response.context["chi_nhanhs"])


class AdminDashboardTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="admin_demo",
            password="pass12345",
            email="admin@example.com",
        )
        self.normal_user = User.objects.create_user(
            username="user_demo",
            password="pass12345",
        )
        self.staff_user = User.objects.create_user(
            username="staff_demo",
            password="pass12345",
            is_staff=True,
        )
        self.clinic = ChiNhanh.objects.create(
            ten="BV Admin Test",
            dia_chi="1 Duong Test",
            phuong="Quan 1",
            vi_tri=Point(106.7, 10.77, srid=4326),
            gio_mo=datetime.time(7, 0),
            gio_dong=datetime.time(17, 0),
        )
        self.department = Khoa.objects.create(
            ten="Khoa Admin Test",
            chi_nhanh=self.clinic,
        )
        self.doctor = BacSi.objects.create(
            ho_ten="Bac Si Admin Test",
            chuyen_khoa="Noi tong quat",
            khoa=self.department,
            chi_nhanh=self.clinic,
            so_dien_thoai="0909555666",
        )

    def test_admin_login_redirects_to_custom_dashboard(self):
        response = self.client.post(
            reverse("login"),
            {"username": "admin_demo", "password": "pass12345"},
        )
        self.assertRedirects(response, reverse("custom_admin_dashboard"))

    def test_custom_dashboard_requires_admin(self):
        self.client.login(username="user_demo", password="pass12345")
        response = self.client.get(reverse("custom_admin_dashboard"))
        self.assertRedirects(response, reverse("home"))

    def test_plain_staff_cannot_access_global_administration(self):
        self.client.login(username="staff_demo", password="pass12345")
        for route, args in [
            ("custom_admin_dashboard", []),
            ("custom_admin_clinics", []),
            ("custom_admin_model_list", ["chi-nhanh"]),
            ("cashier_monitor", []),
        ]:
            with self.subTest(route=route):
                response = self.client.get(reverse(route, args=args))
                self.assertIn(response.status_code, (302, 403))

    def test_admin_can_open_custom_dashboard(self):
        self.client.login(username="admin_demo", password="pass12345")
        response = self.client.get(reverse("custom_admin_dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_admin_can_open_custom_management_pages(self):
        self.client.login(username="admin_demo", password="pass12345")
        self.assertEqual(self.client.get(reverse("custom_admin_clinics")).status_code, 200)
        self.assertEqual(self.client.get(reverse("custom_admin_departments")).status_code, 200)
        self.assertEqual(self.client.get(reverse("custom_admin_doctors")).status_code, 200)
        self.assertEqual(self.client.get(reverse("custom_admin_model_list", args=["tai-khoan"])).status_code, 200)

    def test_superuser_cannot_edit_a_superuser_in_custom_account_directory(self):
        self.client.login(username="admin_demo", password="pass12345")
        response = self.client.get(
            reverse("custom_admin_model_edit", args=["tai-khoan", self.admin_user.id])
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_department_api_returns_departments_by_clinic(self):
        self.client.login(username="admin_demo", password="pass12345")
        response = self.client.get(
            reverse("custom_admin_departments_api"),
            {"chi_nhanh_id": self.clinic.id},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("results", data)
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["id"], self.department.id)

    def test_non_admin_cannot_use_department_api(self):
        self.client.login(username="user_demo", password="pass12345")
        response = self.client.get(
            reverse("custom_admin_departments_api"),
            {"chi_nhanh_id": self.clinic.id},
        )
        self.assertEqual(response.status_code, 403)

    def test_doctor_schedule_form_shows_clinic_in_doctor_option(self):
        self.client.login(username="admin_demo", password="pass12345")
        response = self.client.get(reverse("custom_admin_model_create", args=["gio-lam-viec-bac-si"]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bac Si Admin Test - BV Admin Test")

    def test_admin_can_create_user_from_custom_admin(self):
        self.client.login(username="admin_demo", password="pass12345")
        response = self.client.post(
            reverse("custom_admin_model_create", args=["tai-khoan"]),
            {
                "username": "staff_from_custom",
                "email": "staff@example.com",
                "first_name": "Staff",
                "last_name": "Custom",
                "is_active": "on",
                "role": "admin",
                "password": "Staff@123456",
            },
        )
        self.assertEqual(response.status_code, 302)
        created_user = User.objects.get(username="staff_from_custom")
        self.assertTrue(created_user.check_password("Staff@123456"))
        self.assertTrue(created_user.is_staff)
        self.assertFalse(created_user.is_superuser)
        self.assertTrue(created_user.has_perm("core.quan_tri_he_thong"))

    def test_superuser_cannot_edit_own_account_through_custom_directory(self):
        self.client.login(username="admin_demo", password="pass12345")
        response = self.client.post(
            reverse("custom_admin_model_edit", args=["tai-khoan", self.admin_user.id]),
            {
                "username": "admin_demo",
                "email": "admin@example.com",
                "first_name": "",
                "last_name": "",
                "is_active": "on",
                "role": "admin",
                "password": "NewPass@12345",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.admin_user.refresh_from_db()
        self.assertTrue(self.admin_user.check_password("pass12345"))
        self.assertTrue(self.admin_user.is_superuser)

    def test_staff_cannot_manage_accounts_or_promote_themselves(self):
        self.client.login(username="staff_demo", password="pass12345")
        routes = [
            ("custom_admin_model_list", ["tai-khoan"]),
            ("custom_admin_model_create", ["tai-khoan"]),
            ("custom_admin_model_edit", ["tai-khoan", self.admin_user.pk]),
            ("custom_admin_model_delete", ["tai-khoan", self.admin_user.pk]),
        ]
        for route, args in routes:
            request = self.client.post if route == "custom_admin_model_delete" else self.client.get
            response = request(reverse(route, args=args))
            self.assertIn(response.status_code, (302, 403))
            if response.status_code == 302:
                self.assertEqual(response.url, reverse('home'))
        self.staff_user.refresh_from_db()
        self.assertFalse(self.staff_user.is_superuser)

    def test_superuser_cannot_delete_self_or_another_superuser(self):
        other = User.objects.create_superuser("other_root", "other@example.com", "pass12345")
        self.client.login(username="admin_demo", password="pass12345")
        for target in [self.admin_user, other]:
            response = self.client.post(reverse("custom_admin_model_delete", args=["tai-khoan", target.pk]))
            self.assertEqual(response.status_code, 403)
            self.assertTrue(User.objects.filter(pk=target.pk).exists())

    def test_admin_user_form_never_demotes_a_superuser(self):
        form = AdminUserForm({
            "username": self.admin_user.username,
            "email": self.admin_user.email,
            "first_name": "",
            "last_name": "",
            "is_active": "on",
            "role": "admin",
        }, instance=self.admin_user)
        self.assertTrue(form.is_valid(), form.errors)
        account = form.save()
        self.assertTrue(account.is_superuser)
        self.assertTrue(account.is_staff)

    def test_medical_records_block_clinic_delete_and_show_impact(self):
        self.client.login(username="admin_demo", password="pass12345")
        url = reverse("custom_admin_model_delete", args=["chi-nhanh", self.clinic.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Không thể xóa")
        self.assertContains(response, "Bác sĩ: 1")
        self.assertNotContains(response, "Xác nhận xóa")
        self.client.post(url)
        self.assertTrue(ChiNhanh.objects.filter(pk=self.clinic.pk).exists())
        with self.assertRaises(ProtectedError):
            self.clinic.delete()

    def test_doctor_user_cannot_be_deleted_with_medical_profile(self):
        doctor_account = User.objects.create_user("doctor_account", password="pass12345")
        self.doctor.user = doctor_account
        self.doctor.save(update_fields=["user"])
        with self.assertRaises(ProtectedError):
            doctor_account.delete()

    def test_doctor_with_appointments_cannot_move_to_another_clinic(self):
        patient = BenhNhan.objects.create(
            ho_ten="Patient move guard", ngay_sinh=datetime.date(2000, 1, 1), gioi_tinh="nam",
            so_dien_thoai="0909555777", dia_chi="Test",
        )
        LichKham.objects.create(
            benh_nhan=patient, bac_si=self.doctor, ngay_kham=timezone.localdate() + datetime.timedelta(days=1),
            gio_kham=datetime.time(9, 0),
        )
        other_clinic = ChiNhanh.objects.create(
            ten="BV Chuyen Bac Si", dia_chi="2 Duong Test", phuong="Quan 2",
            vi_tri=Point(106.8, 10.78, srid=4326), gio_mo=datetime.time(7), gio_dong=datetime.time(17),
        )
        other_department = Khoa.objects.create(ten="Khoa chuyen", chi_nhanh=other_clinic)
        self.doctor.chi_nhanh = other_clinic
        self.doctor.khoa = other_department
        with self.assertRaises(ValidationError):
            self.doctor.full_clean()

    def test_staff_with_branch_manager_profile_cannot_use_global_admin_crud(self):
        hybrid = User.objects.create_user("hybrid_manager", password="pass12345", is_staff=True)
        QuanLyChiNhanh.objects.create(user=hybrid, chi_nhanh=self.clinic)
        self.client.force_login(hybrid)
        routes = [
            reverse("custom_admin_dashboard"),
            reverse("custom_admin_model_list", args=["chi-nhanh"]),
            reverse("custom_admin_model_create", args=["chi-nhanh"]),
            reverse("custom_admin_model_edit", args=["chi-nhanh", self.clinic.pk]),
            reverse("custom_admin_model_delete", args=["chi-nhanh", self.clinic.pk]),
        ]
        for url in routes:
            response = self.client.get(url)
            self.assertRedirects(response, reverse("home"))
        self.assertEqual(self.client.get(reverse("custom_admin_departments_api")).status_code, 403)

    def test_django_admin_url_disabled(self):
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 404)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="no-reply@test.local",
    CONTACT_RECEIVER_EMAIL="feedback@test.local",
)
class ContactFeedbackTests(TestCase):
    def test_contact_feedback_page_renders(self):
        response = self.client.get(reverse("contact_feedback"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "EyeViora luôn lắng nghe")

    def test_submit_feedback_sends_email(self):
        response = self.client.post(
            reverse("contact_feedback"),
            {
                "ho_ten": "Nguoi dung test",
                "email": "sender@example.com",
                "chu_de": "Gop y giao dien",
                "noi_dung": "Trang web de dung va can them mot vai tinh nang nho.",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "\u0110\u00e3 g\u1eedi g\u00f3p \u00fd th\u00e0nh c\u00f4ng")
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["feedback@test.local"])
        self.assertIn("[G\u00d3P \u00dd] Gop y giao dien", sent.subject)
        self.assertIn("Nguoi dung test", sent.body)
        self.assertIn("sender@example.com", sent.body)

    def test_submit_feedback_invalid_payload(self):
        response = self.client.post(
            reverse("contact_feedback"),
            {
                "ho_ten": "",
                "email": "invalid-email",
                "chu_de": "",
                "noi_dung": "ngan",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)


class CoordinateConstraintTests(TestCase):
    def _chi_nhanh_kwargs(self, vi_tri):
        return {
            "ten": "BV Toa Do Test",
            "dia_chi": "1 Duong Test",
            "phuong": "Sai Gon",
            "vi_tri": vi_tri,
            "gio_mo": datetime.time(7, 0),
            "gio_dong": datetime.time(17, 0),
        }

    def test_chinhanh_rejects_out_of_range_wgs84(self):
        chi_nhanh = ChiNhanh(**self._chi_nhanh_kwargs(Point(181, 10, srid=4326)))
        with self.assertRaises(ValidationError):
            chi_nhanh.full_clean()

    def test_chinhanh_accepts_valid_wgs84(self):
        chi_nhanh = ChiNhanh(**self._chi_nhanh_kwargs(Point(106.7, 10.77, srid=4326)))
        chi_nhanh.full_clean()
        chi_nhanh.save()
        self.assertEqual(chi_nhanh.vi_tri.srid, 4326)
        self.assertTrue(-90 <= chi_nhanh.vi_tri.y <= 90)
        self.assertTrue(-180 <= chi_nhanh.vi_tri.x <= 180)

    def test_admin_chinhanh_form_rejects_invalid_lat_lon(self):
        form = AdminChiNhanhForm(data={
            "ten": "BV Form Test",
            "dia_chi": "2 Duong Test",
            "phuong": "Sai Gon",
            "lat": 95,
            "lon": 106.7,
        })
        self.assertFalse(form.is_valid())
        self.assertIn("lat", form.errors)


class AuthFlowTests(TestCase):
    def test_register_creates_patient_linked_to_user(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "0909777888",
                "password": "StrongPass@123",
                "name": "Nguoi Dang Ky",
                "email": "dangky@example.com",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        created_user = User.objects.get(username="0909777888")
        benh_nhan = BenhNhan.objects.get(so_dien_thoai="0909777888")
        self.assertEqual(benh_nhan.user_id, created_user.id)

    def test_forgot_password_rejects_weak_password(self):
        user = User.objects.create_user(
            username="0909555123",
            email="recover@example.com",
            password="OldPass@123",
        )
        response = self.client.post(
            reverse("forgot_password"),
            {
                "username": user.username,
                "email": user.email,
                "new_password": "123",
                "confirm_password": "123",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password("OldPass@123"))
