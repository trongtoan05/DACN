import datetime
import json
from io import BytesIO
from urllib.parse import parse_qs, urlsplit
from urllib.error import HTTPError
from unittest.mock import Mock, patch

from django.test import TestCase
from django.test import override_settings
from django.contrib.staticfiles import finders
from django.contrib.auth.models import User
from django.core.cache import cache
from django.contrib.gis.geos import Point
from django.urls import reverse

from core import tests as fixture_tests
from core.models import BacSiDichVu, ChiNhanh, DanhMucDichVu, DichVuChiNhanh


class PublicClinicPagesTests(TestCase):
    def setUp(self):
        fixture_tests.AppointmentBookingTests.setUp(self)
        self.public_ids_override = override_settings(
            PUBLIC_CLINIC_BRANCH_IDS=(self.clinic.pk, self.other_clinic.pk),
        )
        self.public_ids_override.enable()
        self.addCleanup(self.public_ids_override.disable)
        cache.clear()

    def test_home_shows_brand_booking_services_and_gis_branches(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "EyeViora")
        self.assertContains(response, "data-ev-slider")
        self.assertContains(response, "id=\"quick-booking-form\"")
        self.assertContains(response, self.clinic.ten)
        self.assertContains(response, self.service.ten)
        self.assertContains(response, "chăm sóc thị lực toàn diện")
        self.assertContains(response, "Khô mắt và mỏi mắt")
        self.assertContains(response, "Chăm sóc mắt khi dùng máy tính")
        self.assertContains(response, f'name="dich_vu_dat_truoc_nhieu" value="{self.service.pk}"')
        self.assertNotContains(response, "BHYT")
        map_data = response.context["map_data"]
        self.assertEqual([item["id"] for item in map_data], [self.clinic.pk, self.other_clinic.pk])
        self.assertEqual([item.pk for item in response.context["clinic_doctors"]], [self.doctor.pk])

    def test_home_uses_official_eyeviora_images_for_brand_banners_and_cards(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'src="/static/core/img/EyeViora/logoEyeViora.png"', count=2)
        for name in ("Banner1.png", "Banner2.png", "Banner3.png"):
            self.assertContains(response, f"/static/core/img/EyeViora/banners/{name}")
        for name in (
            "01_kham_mat_tong_quat.jpg", "02_tat_khuc_xa.jpg", "03_kho_mat_moi_mat.jpg",
            "04_do_nhan_ap.jpg", "05_day_mat_vong_mac.jpg", "06_cham_soc_mat_tre_em.jpg",
            "07_nghi_mat_khi_dung_may_tinh.jpg", "08_doc_sach_dung_anh_sang.jpg",
            "09_cham_soc_mat_nguoi_cao_tuoi.jpg",
        ):
            folder = "services" if name.startswith(("01_", "02_", "03_", "04_", "05_", "06_")) else "articles"
            self.assertContains(response, f"/static/core/img/EyeViora/{folder}/{name}")
        self.assertNotContains(response, "ev-placeholder-art")
        self.assertNotContains(response, "bộ ảnh EyeViora chưa có")

    def test_home_is_compact_and_branch_filter_does_not_change_booking_sources(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="dich-vu"')
        self.assertNotContains(response, "Danh mục đang áp dụng")
        self.assertNotContains(response, 'id="lat" name="lat" type="number"')
        self.assertNotContains(response, 'id="lon" name="lon" type="number"')
        self.assertContains(response, 'id="quick-service-picker"')
        self.assertContains(response, 'name="dich_vu_dat_truoc_nhieu"', count=1)
        self.assertEqual(len(response.context["map_data"]), 2)
        self.assertEqual(response.context["quick_branches"].count(), 2)

    def test_quick_booking_keeps_multiple_services_and_selected_branch(self):
        second_service = DanhMucDichVu.objects.create(
            ma_dich_vu="PUBLIC_MULTI_2", ten="Dịch vụ chọn thêm", nhom="Khám mắt",
            don_gia=200000, cho_phep_dat_lich=True,
        )
        second_assignment = DichVuChiNhanh.objects.create(
            chi_nhanh=self.clinic, dich_vu=second_service,
        )
        BacSiDichVu.objects.create(bac_si=self.doctor, dich_vu_chi_nhanh=second_assignment)

        home = self.client.get(reverse("home"))
        self.assertContains(home, f'value="{self.service.pk}"')
        self.assertContains(home, f'value="{second_service.pk}"')
        self.client.force_login(self.user)
        booking = self.client.get(reverse("dat_lich_picker"), {
            "chi_nhanh": self.clinic.pk,
            "dich_vu_dat_truoc_nhieu": [self.service.pk, second_service.pk],
        })
        self.assertEqual(booking.status_code, 200)
        self.assertEqual(booking.context["form"].initial["chi_nhanh"], self.clinic.pk)
        self.assertEqual(
            sorted(booking.context["form"].initial["dich_vu_dat_truoc_nhieu"]),
            sorted([self.service.pk, second_service.pk]),
        )

    def test_public_service_catalog_shows_booking_and_doctor_directed_services_separately(self):
        technical = DanhMucDichVu.objects.create(
            ma_dich_vu="PUBLIC_OCT", ten="Chụp OCT mắt", nhom="Chẩn đoán hình ảnh",
            mo_ta="Kỹ thuật theo chỉ định bác sĩ.", don_gia=350000, cho_phep_dat_lich=False,
        )
        DanhMucDichVu.objects.create(
            ma_dich_vu="DEMO_OLD_EYE", ten="DEMO · Dịch vụ cũ", nhom="Demo",
            don_gia=100000, cho_phep_dat_lich=False,
        )
        response = self.client.get(reverse("service_catalog_public"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.service.ten)
        self.assertContains(response, "Khám và tư vấn mắt")
        self.assertContains(response, technical.ten)
        self.assertNotContains(response, "DEMO · Dịch vụ cũ")
        self.assertContains(response, "Kỹ thuật hỗ trợ chẩn đoán")
        self.assertContains(response, "350.000 VND")
        self.assertNotContains(response, f"href=\"/dat-lich/?dich_vu_dat_truoc={technical.pk}\"")

    def test_public_branch_image_exists_and_detail_uses_common_illustration(self):
        image_path = "core/img/EyeViora/branches/branch-default.jpg"
        self.assertTrue(finders.find(image_path))
        response = self.client.get(reverse("branch_detail", args=[self.clinic.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/static/" + image_path)
        self.assertContains(response, "Ảnh minh họa chung")
        self.assertContains(response, f'href="/dat-lich/{self.clinic.pk}/"')

    def test_all_nine_official_branches_render_detail_with_real_coordinates_and_common_image(self):
        branch_ids = tuple(range(684, 693))
        for index, branch_id in enumerate(branch_ids):
            ChiNhanh.objects.create(
                id=branch_id, ten=f"EyeViora chi nhánh test {branch_id}",
                dia_chi=f"{index + 1} Đường kiểm thử", phuong="Phường kiểm thử",
                vi_tri=Point(106.7 + index / 1000, 10.77 + index / 1000, srid=4326), gio_mo=datetime.time(8),
                gio_dong=datetime.time(17), dang_hoat_dong=True,
            )

        with override_settings(PUBLIC_CLINIC_BRANCH_IDS=branch_ids):
            for index, branch_id in enumerate(branch_ids):
                with self.subTest(branch_id=branch_id):
                    response = self.client.get(reverse("branch_detail", args=[branch_id]))
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, f"EyeViora chi nhánh test {branch_id}")
                    self.assertContains(response, 'alt="Ảnh minh họa chung cho các chi nhánh EyeViora"')
                    self.assertAlmostEqual(response.context["map_lat"], 10.77 + index / 1000)
                    self.assertAlmostEqual(response.context["map_lon"], 106.7 + index / 1000)
                    self.assertContains(response, "Xem vị trí trên bản đồ")
                    self.assertContains(response, f'data-branch-id="{branch_id}"')
                    self.assertEqual(response.context["map_coordinates"]["lat"], response.context["map_lat"])
                    self.assertEqual(response.context["map_coordinates"]["lon"], response.context["map_lon"])

    def test_branch_map_is_before_services_and_has_interactive_routing_controls(self):
        self.clinic.vi_tri = Point(106.7, 10.77, srid=4326)
        self.clinic.save(update_fields=["vi_tri"])

        response = self.client.get(reverse("branch_detail", args=[self.clinic.pk]))
        html = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertLess(html.index('id="vi-tri"'), html.index('id="booking-services-title"'))
        self.assertEqual(html.count('id="map"'), 1)
        for control_id in ("route-locate", "route-select", "route-find", "route-clear"):
            self.assertContains(response, f'id="{control_id}"')
        self.assertContains(response, f'data-branch-id="{self.clinic.pk}"')
        self.assertNotContains(response, "google.com/maps")
        self.assertNotContains(response, "technical-services-title")
        self.assertContains(response, 'id="route-start-summary"')

    def test_branch_detail_hides_technical_services_but_preserves_catalog_and_doctor_booking(self):
        technical = DanhMucDichVu.objects.create(
            ma_dich_vu="BRANCH_OCT", ten="OCT branch test", nhom="Imaging",
            don_gia=350000, cho_phep_dat_lich=False,
        )
        assignment = DichVuChiNhanh.objects.create(chi_nhanh=self.clinic, dich_vu=technical)
        self.doctor.chuyen_khoa = "Nh\u00e3n khoa (d\u1eef li\u1ec7u demo)"
        self.doctor.save(update_fields=["chuyen_khoa"])

        response = self.client.get(reverse("branch_detail", args=[self.clinic.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "technical-services-title")
        self.assertNotContains(response, technical.ten)
        self.assertTrue(DichVuChiNhanh.objects.filter(pk=assignment.pk, dang_ap_dung=True).exists())
        self.assertContains(response, self.doctor.ho_ten)
        self.assertContains(response, "Nh\u00e3n khoa")
        self.assertNotContains(response, "(d\u1eef li\u1ec7u demo)")
        self.assertContains(response, f"?dich_vu_dat_truoc={self.service.pk}&bac_si={self.doctor.pk}")
        self.doctor.refresh_from_db()
        self.assertEqual(self.doctor.chuyen_khoa, "Nh\u00e3n khoa (d\u1eef li\u1ec7u demo)")

    def test_route_api_uses_database_branch_destination_and_returns_real_router_metrics(self):
        self.clinic.vi_tri = Point(106.7, 10.77, srid=4326)
        self.clinic.save(update_fields=["vi_tri"])
        upstream = Mock()
        upstream.__enter__ = Mock(return_value=upstream)
        upstream.__exit__ = Mock(return_value=False)
        upstream.read.return_value = json.dumps({
            "code": "Ok",
            "routes": [{
                "geometry": {"type": "LineString", "coordinates": [[106.68, 10.78], [106.69, 10.775], [106.7, 10.77]]},
                "distance": 1840,
                "duration": 420,
            }],
        }).encode("utf-8")

        with override_settings(
            PUBLIC_CLINIC_BRANCH_IDS=(self.clinic.pk,),
            EYEVIORA_OSRM_BASE_URL="https://routing.example.test",
            EYEVIORA_OSRM_API_TOKEN="",
        ), patch("core.routing.urlopen", return_value=upstream) as open_route:
            response = self.client.post(reverse("clinic_route_api"), data=json.dumps({
                "branch_id": self.clinic.pk,
                "start_lat": 10.78,
                "start_lon": 106.68,
            }), content_type="application/json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["distance_meters"], 1840)
        self.assertEqual(response.json()["duration_seconds"], 420)
        self.assertEqual(response.json()["geometry"]["coordinates"][-1], [106.7, 10.77])
        route_request = open_route.call_args.args[0]
        self.assertEqual(route_request.method, "POST")
        self.assertNotIn("10.78", route_request.full_url)
        route_body = json.loads(route_request.data.decode("utf-8"))
        self.assertEqual(route_body["coordinates"], [[106.68, 10.78], [106.7, 10.77]])
        self.clinic.refresh_from_db()
        self.assertEqual((self.clinic.vi_tri.y, self.clinic.vi_tri.x), (10.77, 106.7))

    def test_route_api_fails_clearly_without_a_configured_production_router(self):
        self.clinic.vi_tri = Point(106.7, 10.77, srid=4326)
        self.clinic.save(update_fields=["vi_tri"])
        with override_settings(
            PUBLIC_CLINIC_BRANCH_IDS=(self.clinic.pk,),
            EYEVIORA_OSRM_BASE_URL="",
        ), patch("core.routing.urlopen") as open_route:
            response = self.client.post(reverse("clinic_route_api"), data=json.dumps({
                "branch_id": self.clinic.pk,
                "start_lat": 10.78,
                "start_lon": 106.68,
            }), content_type="application/json")
        self.assertEqual(response.status_code, 503)
        self.assertIn("cau hinh", response.json()["error"])
        open_route.assert_not_called()

    def test_route_api_reports_no_route_without_returning_a_fabricated_line(self):
        self.clinic.vi_tri = Point(106.7, 10.77, srid=4326)
        self.clinic.save(update_fields=["vi_tri"])
        error = HTTPError(
            "https://routing.example.test/route/v1/driving", 400, "Bad Request", {},
            BytesIO(b'{"code":"NoRoute"}'),
        )
        with override_settings(
            PUBLIC_CLINIC_BRANCH_IDS=(self.clinic.pk,),
            EYEVIORA_OSRM_BASE_URL="https://routing.example.test",
        ), patch("core.routing.urlopen", side_effect=error):
            response = self.client.post(reverse("clinic_route_api"), data=json.dumps({
                "branch_id": self.clinic.pk,
                "start_lat": 10.78,
                "start_lon": 106.68,
            }), content_type="application/json")
        self.assertEqual(response.status_code, 422)
        self.assertIn("tuyen duong", response.json()["error"])

    def test_public_branch_detail_is_limited_by_fixed_ids(self):
        with override_settings(PUBLIC_CLINIC_BRANCH_IDS=(self.clinic.pk,)):
            response = self.client.get(reverse("branch_detail", args=[self.other_clinic.pk]))
        self.assertEqual(response.status_code, 404)

    def test_admin_branch_detail_access_is_not_limited_by_public_set(self):
        admin = User.objects.create_superuser(
            username="publicBranchAdmin", email="admin@example.invalid", password=None,
        )
        self.client.force_login(admin)
        with override_settings(PUBLIC_CLINIC_BRANCH_IDS=(self.clinic.pk,)):
            response = self.client.get(reverse("branch_detail", args=[self.other_clinic.pk]))
        self.assertEqual(response.status_code, 200)

    def test_public_service_catalog_shows_current_services_and_only_booking_options(self):
        response = self.client.get(reverse("service_catalog_public"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dịch vụ &amp; bảng giá")
        self.assertContains(response, self.service.ten)
        self.assertContains(response, f'href="/dat-lich/?dich_vu_dat_truoc={self.service.pk}"')
        self.assertContains(response, 'id="booking-services-title"')
        self.assertContains(response, 'id="technical-services-title"')
        self.assertNotContains(response, "BHYT")

    def test_service_catalog_renders_six_booking_cards_and_nine_technical_cards(self):
        booking_codes = (
            "MAT_KHAM_TQ", "MAT_KHAM_KHUC_XA", "MAT_KHAM_TRE_EM", "MAT_KHAM_KHO_MAT",
            "MAT_KHAM_GLOCOM", "MAT_KHAM_VONG_MAC",
        )
        technical_codes = (
            "MAT_DO_THI_LUC", "MAT_DO_KHUC_XA", "MAT_DO_NHAN_AP", "MAT_TEST_KHO_MAT",
            "MAT_DO_THI_TRUONG", "MAT_CHUP_OCT", "MAT_CHUP_DAY_MAT", "MAT_BAN_DO_GIAC_MAC", "MAT_SIEU_AM",
        )
        self.service.ma_dich_vu = booking_codes[0]
        self.service.save(update_fields=["ma_dich_vu"])
        for index, code in enumerate(booking_codes[1:], start=1):
            service = DanhMucDichVu.objects.create(
                ma_dich_vu=code, ten=f"Booking service {index}", nhom="Khám mắt",
                mo_ta=f"Public description {index}", don_gia=100000 + index, cho_phep_dat_lich=True,
            )
            assignment = DichVuChiNhanh.objects.create(chi_nhanh=self.clinic, dich_vu=service)
            BacSiDichVu.objects.create(bac_si=self.doctor, dich_vu_chi_nhanh=assignment)
        for index, code in enumerate(technical_codes):
            DanhMucDichVu.objects.create(
                ma_dich_vu=code, ten=f"Technical service {index}", nhom="Chẩn đoán",
                mo_ta=f"Technical description {index}", don_gia=50000 + index, cho_phep_dat_lich=False,
            )
        DanhMucDichVu.objects.create(
            ma_dich_vu="DEMO_SERVICE_PAGE", ten="DEMO hidden service", nhom="Demo",
            don_gia=1, cho_phep_dat_lich=False,
        )

        response = self.client.get(reverse("service_catalog_public"))
        html = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["booking_services"]), 6)
        self.assertEqual(len(response.context["technical_services"]), 9)
        self.assertEqual(html.count('class="ev-service-booking-card"'), 6)
        self.assertEqual(html.count('class="ev-service-technical-card"'), 9)
        self.assertEqual(html.count("ev-service-booking-cta\""), 6)
        self.assertNotIn("DEMO hidden service", html)
        self.assertEqual(
            [service.ma_dich_vu for service in response.context["booking_services"]], list(booking_codes),
        )
        self.assertEqual(
            [service.ma_dich_vu for service in response.context["technical_services"]], list(technical_codes),
        )

    def test_service_booking_cta_selection_survives_login_and_branch_choice(self):
        self.client.logout()
        url = reverse("dat_lich_picker")
        response = self.client.get(url, {"dich_vu_dat_truoc": self.service.pk})

        self.assertEqual(response.status_code, 302)
        next_value = parse_qs(urlsplit(response["Location"]).query)["next"][0]
        self.assertIn(f"dich_vu_dat_truoc={self.service.pk}", next_value)

        self.client.force_login(self.user)
        first_step = self.client.get(url, {"dich_vu_dat_truoc": self.service.pk})
        self.assertEqual(first_step.status_code, 200)
        self.assertEqual(first_step.context["initial_service_ids"], [self.service.pk])
        branch_step = self.client.get(url, {
            "chi_nhanh": self.clinic.pk, "dich_vu_dat_truoc": self.service.pk,
        })
        self.assertEqual(branch_step.status_code, 200)
        self.assertEqual(branch_step.context["initial_service_ids"], [self.service.pk])
        self.assertEqual(branch_step.context["form"].initial["dich_vu_dat_truoc_nhieu"], [self.service.pk])

    def test_public_service_catalog_is_not_limited_to_homepage_preview(self):
        extra_services = []
        for index in range(8):
            service = DanhMucDichVu.objects.create(
                ma_dich_vu=f"EXTRA_{index}", ten=f"Dịch vụ bổ sung {index}", nhom="Khám mắt",
                don_gia=150000, cho_phep_dat_lich=True,
            )
            branch_service = DichVuChiNhanh.objects.create(
                chi_nhanh=self.clinic, dich_vu=service,
            )
            BacSiDichVu.objects.create(bac_si=self.doctor, dich_vu_chi_nhanh=branch_service)
            extra_services.append(service)

        response = self.client.get(reverse("service_catalog_public"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["clinic_services"]), 9)
        for service in extra_services:
            self.assertContains(response, service.ten)

    def test_contact_page_is_available_without_login(self):
        response = self.client.get(reverse("contact_feedback"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Liên hệ &amp; góp ý")
        self.assertContains(response, "name=\"noi_dung\"")

    def test_inactive_branch_is_excluded_from_public_directory_and_map(self):
        self.other_clinic.dang_hoat_dong = False
        self.other_clinic.save(update_fields=["dang_hoat_dong"])

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.other_clinic.ten)
        self.assertEqual([item["id"] for item in response.context["map_data"]], [self.clinic.pk])
        detail = self.client.get(reverse("branch_detail", args=[self.other_clinic.pk]))
        self.assertEqual(detail.status_code, 404)

    def test_branch_detail_only_advertises_configured_services_and_booking_options(self):
        response = self.client.get(reverse("branch_detail", args=[self.clinic.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.service.ten)
        self.assertContains(response, self.doctor.ho_ten)
        doctor = next(item for item in response.context["bac_sis"] if item.pk == self.doctor.pk)
        self.assertEqual([item.pk for item in doctor.booking_services], [self.service.pk])
        self.assertTrue(response.context["booking_available"])

        BacSiDichVu.objects.filter(bac_si=self.doctor, dich_vu_chi_nhanh=self.branch_service).update(dang_ap_dung=False)
        response = self.client.get(reverse("branch_detail", args=[self.clinic.pk]))
        self.assertFalse(response.context["booking_available"])
        self.assertNotContains(response, f"dich_vu_dat_truoc={self.service.pk}")

    def test_doctor_card_prefills_only_a_service_assigned_at_selected_branch(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("dat_lich", args=[self.clinic.pk]), {"bac_si": self.doctor.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].initial["dich_vu_dat_truoc_nhieu"], [self.service.pk])
        self.assertEqual(response.context["form"].initial["bac_si"], self.doctor.pk)

    def test_about_page_uses_real_active_branch_markers_and_handles_empty_coordinates(self):
        response = self.client.get(reverse("gioi_thieu"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "EyeViora")
        self.assertContains(response, "about-map")
        self.assertNotContains(response, "BHYT")
        self.assertEqual(len(response.context["map_data"]), 2)


