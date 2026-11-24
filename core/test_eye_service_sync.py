import datetime
from decimal import Decimal
from io import StringIO

from django.contrib.gis.geos import Point
from django.core.management import call_command
from django.test import TestCase

from core.management.commands.sync_eye_services import ACTIVE_BRANCH_IDS, SERVICE_SPECS
from core.models import (
    BacSi,
    BacSiDichVu,
    ChiNhanh,
    DanhMucDichVu,
    DichVuChiNhanh,
    Khoa,
)


class EyeServiceSyncCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.branches = []
        for branch_id in ACTIVE_BRANCH_IDS:
            branch = ChiNhanh(
                pk=branch_id,
                ten=f"Chi nhánh EyeViora test {branch_id}",
                dia_chi="Địa chỉ kiểm thử",
                phuong="Phường kiểm thử",
                vi_tri=Point(106.7, 10.77, srid=4326),
                gio_mo=datetime.time(7),
                gio_dong=datetime.time(18),
                dang_hoat_dong=True,
            )
            branch.save(force_insert=True)
            cls.branches.append(branch)
            department = Khoa.objects.create(ten=f"Khoa test {branch_id}", chi_nhanh=branch)
            doctor = BacSi.objects.create(
                ho_ten=f"Bác sĩ test {branch_id}", chuyen_khoa="Nhãn khoa",
                khoa=department, chi_nhanh=branch, so_dien_thoai=f"0901234{branch_id:03d}",
            )
            cls._ = doctor

        cls.demo = DanhMucDichVu.objects.create(
            ma_dich_vu="DEMO_MAT_KHAM_TQ", ten="DEMO · Khám mắt tổng quát",
            nhom="Khám mắt", mo_ta="Dữ liệu cũ", don_gia=100000,
            dang_ap_dung=True, cho_phep_dat_lich=True,
        )
        for branch in cls.branches:
            offer = DichVuChiNhanh.objects.create(chi_nhanh=branch, dich_vu=cls.demo)
            doctor = BacSi.objects.get(chi_nhanh=branch)
            BacSiDichVu.objects.create(bac_si=doctor, dich_vu_chi_nhanh=offer)

    def test_dry_run_writes_nothing_then_sync_is_idempotent(self):
        before_services = DanhMucDichVu.objects.count()
        before_links = DichVuChiNhanh.objects.count()
        before_doctor_links = BacSiDichVu.objects.count()
        output = StringIO()
        call_command("sync_eye_services", database="default", dry_run=True, stdout=output)
        self.assertIn("Dry run only", output.getvalue())
        self.assertEqual(DanhMucDichVu.objects.count(), before_services)
        self.assertEqual(DichVuChiNhanh.objects.count(), before_links)
        self.assertEqual(BacSiDichVu.objects.count(), before_doctor_links)
        self.demo.refresh_from_db()
        self.assertTrue(self.demo.cho_phep_dat_lich)

        call_command("sync_eye_services", database="default", stdout=StringIO())
        call_command("sync_eye_services", database="default", stdout=StringIO())

        services = {row.ma_dich_vu: row for row in DanhMucDichVu.objects.filter(
            ma_dich_vu__in=[spec["code"] for spec in SERVICE_SPECS]
        )}
        self.assertEqual(len(services), 15)
        for spec in SERVICE_SPECS:
            with self.subTest(code=spec["code"]):
                service = services[spec["code"]]
                self.assertEqual(service.don_gia, Decimal(spec["price"]))
                self.assertEqual(service.cho_phep_dat_lich, spec["bookable"])
                self.assertEqual(service.chi_nhanh_ap_dung.filter(
                    chi_nhanh_id__in=ACTIVE_BRANCH_IDS, dang_ap_dung=True
                ).count(), 9)
                assignment_count = BacSiDichVu.objects.filter(
                    dich_vu_chi_nhanh__dich_vu=service,
                    dich_vu_chi_nhanh__chi_nhanh_id__in=ACTIVE_BRANCH_IDS,
                    dang_ap_dung=True,
                ).count()
                self.assertEqual(assignment_count, 9 if spec["bookable"] else 0)
        self.assertEqual(DanhMucDichVu.objects.filter(ma_dich_vu__startswith="MAT_").count(), 15)
        self.demo.refresh_from_db()
        self.assertFalse(self.demo.cho_phep_dat_lich)
        self.assertEqual(self.demo.don_gia, Decimal("100000"))
        self.assertEqual(DichVuChiNhanh.objects.filter(dich_vu=self.demo).count(), 9)

    def test_command_does_not_activate_historical_branches_or_create_billing(self):
        from core.models import GiaoDichThanhToan, HoaDon

        call_command("sync_eye_services", database="default", stdout=StringIO())
        self.assertEqual(ChiNhanh.objects.filter(dang_hoat_dong=True).count(), 9)
        self.assertFalse(ChiNhanh.objects.filter(pk__gt=692, dang_hoat_dong=True).exists())
        self.assertFalse(HoaDon.objects.exists())
        self.assertFalse(GiaoDichThanhToan.objects.exists())

    def test_missing_official_branch_aborts_before_writes(self):
        ChiNhanh.objects.filter(pk=692).update(dang_hoat_dong=False)
        with self.assertRaisesMessage(Exception, "Expected exactly the nine active EyeViora branches"):
            call_command("sync_eye_services", database="default", stdout=StringIO())
        self.assertEqual(DanhMucDichVu.objects.filter(ma_dich_vu__startswith="MAT_").count(), 0)

