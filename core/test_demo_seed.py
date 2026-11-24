import os
import secrets
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.management import call_command, CommandError
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from core.appointments import available_booking_slots
from core.models import (
    BacSi, BacSiDichVu, BenhNhan, ChiNhanh, DanhMucDichVu,
    DichVuChiNhanh,
    GiaoDichThanhToan, GioLamViecBacSi, GioLamViecChiNhanh,
    HoaDon, LeTanChiNhanh, LichKham,
)

DEMO_PASSWORDS = {
    name: secrets.token_urlsafe(32) for name in (
        "CLINIC_DEMO_PATIENT_PASSWORD", "CLINIC_DEMO_DOCTOR_A_PASSWORD",
        "CLINIC_DEMO_DOCTOR_B_PASSWORD", "CLINIC_DEMO_RECEPTION_A_PASSWORD",
        "CLINIC_DEMO_RECEPTION_B_PASSWORD",
    )
}


class DemoSeedCommandTests(TestCase):
    alias = "default"

    def setUp(self):
        db_name = str(connection.settings_dict.get("NAME", ""))
        if "test" not in db_name.casefold():
            self.skipTest("Seed command integration tests require Django's isolated test database.")

    def test_dry_run_reports_plan_without_writing_rows_or_needing_password(self):
        before = (ChiNhanh.objects.count(), DanhMucDichVu.objects.count(), get_user_model().objects.count())
        out = []
        call_command("seed_clinic_demo", database=self.alias, dry_run=True, stdout=Capture(out))
        after = (ChiNhanh.objects.count(), DanhMucDichVu.objects.count(), get_user_model().objects.count())

        self.assertEqual(before, after)
        self.assertTrue(any("Dry run only" in line for line in out))

    def test_seed_is_repeatable_branch_scoped_and_respects_official_branch_allowlist(self):
        with patch.dict(os.environ, DEMO_PASSWORDS, clear=False):
            call_command("seed_clinic_demo", database=self.alias, stdout=Capture([]))
            expected = {
                "branches": ChiNhanh.objects.count(),
                "services": DanhMucDichVu.objects.count(),
                "users": get_user_model().objects.count(),
                "doctors": BacSi.objects.count(),
                "receptionists": LeTanChiNhanh.objects.count(),
                "branch_services": DichVuChiNhanh.objects.count(),
                "doctor_services": BacSiDichVu.objects.count(),
                "branch_hours": GioLamViecChiNhanh.objects.count(),
                "doctor_hours": GioLamViecBacSi.objects.count(),
                "appointments": LichKham.objects.count(),
                "invoices": HoaDon.objects.count(),
                "transactions": GiaoDichThanhToan.objects.count(),
            }
            call_command("seed_clinic_demo", database=self.alias, stdout=Capture([]))
        actual = {
            "branches": ChiNhanh.objects.count(),
            "services": DanhMucDichVu.objects.count(),
            "users": get_user_model().objects.count(),
            "doctors": BacSi.objects.count(),
            "receptionists": LeTanChiNhanh.objects.count(),
            "branch_services": DichVuChiNhanh.objects.count(),
            "doctor_services": BacSiDichVu.objects.count(),
            "branch_hours": GioLamViecChiNhanh.objects.count(),
            "doctor_hours": GioLamViecBacSi.objects.count(),
            "appointments": LichKham.objects.count(),
            "invoices": HoaDon.objects.count(),
            "transactions": GiaoDichThanhToan.objects.count(),
        }

        self.assertEqual(expected, actual)
        self.assertEqual(expected["branches"], 2)
        self.assertEqual(expected["services"], 4)
        self.assertEqual(expected["doctors"], 2)
        self.assertEqual(expected["receptionists"], 2)
        self.assertEqual(expected["appointments"], 0)
        self.assertEqual(expected["invoices"], 0)
        self.assertEqual(expected["transactions"], 0)
        patient = BenhNhan.objects.get(user__username="demo_patient")
        for branch in ChiNhanh.objects.order_by("ten"):
            self.assertIn("[DEMO]", branch.ten)
            self.assertIn("minh họa", branch.dia_chi)
            self.assertEqual(branch.vi_tri.srid, 4326)
            self.assertGreaterEqual(branch.vi_tri.x, -180)
            self.assertLessEqual(branch.vi_tri.x, 180)
            self.assertGreaterEqual(branch.vi_tri.y, -90)
            self.assertLessEqual(branch.vi_tri.y, 90)
            doctor = BacSi.objects.get(chi_nhanh=branch)
            service = DanhMucDichVu.objects.get(ma_dich_vu="DEMO_MAT_KHAM_TQ")
            self.assertTrue(BacSiDichVu.objects.filter(
                bac_si=doctor,
                dich_vu_chi_nhanh__chi_nhanh=branch,
                dich_vu_chi_nhanh__dich_vu=service,
                dang_ap_dung=True,
            ).exists())
            day = timezone.localdate() + timezone.timedelta(days=1)
            while day.weekday() == 6:
                day += timezone.timedelta(days=1)
            self.assertNotIn(branch.pk, settings.PUBLIC_CLINIC_BRANCH_IDS)
            self.assertFalse(available_booking_slots(doctor, day, patient.pk, service.pk))
            # The seed includes internally consistent schedules and service
            # assignments; it becomes bookable only if a deployment explicitly
            # adds that branch to its configured public allowlist.
            with override_settings(PUBLIC_CLINIC_BRANCH_IDS=(
                *settings.PUBLIC_CLINIC_BRANCH_IDS, branch.pk,
            )):
                self.assertTrue(available_booking_slots(doctor, day, patient.pk, service.pk))
            receptionist = LeTanChiNhanh.objects.get(chi_nhanh=branch)
            self.assertEqual(doctor.user.is_superuser, False)
            self.assertEqual(doctor.user.is_staff, False)
            self.assertEqual(doctor.user.email.endswith("@demo.invalid"), True)
            self.assertEqual(receptionist.user.is_superuser, False)
            self.assertEqual(receptionist.user.is_staff, False)
            self.assertEqual(receptionist.user.email.endswith("@demo.invalid"), True)

    def test_missing_password_and_production_environment_are_rejected(self):
        with patch.dict(os.environ, {key: "" for key in DEMO_PASSWORDS}, clear=False):
            with self.assertRaises(CommandError):
                call_command("seed_clinic_demo", database=self.alias)
        with patch.dict(os.environ, {"DJANGO_ENV": "production"}, clear=False):
            with self.assertRaises(CommandError):
                call_command("seed_clinic_demo", database=self.alias, dry_run=True)

    def test_refuses_development_database_and_does_not_overwrite_catalog_collision(self):
        from django.conf import settings
        from core.management.commands.seed_clinic_demo import Command

        config = dict(settings.DATABASES["default"])
        config["NAME"] = "eyeviora_clinic"
        with override_settings(DATABASES={"default": config}):
            with self.assertRaises(CommandError):
                Command()._guard_database("default")

        conflict = DanhMucDichVu.objects.create(
            ma_dich_vu="DEMO_MAT_KHAM_TQ", ten="Real catalog collision", nhom="Test",
            don_gia=12345, mo_ta="existing non-demo row", dang_ap_dung=True, cho_phep_dat_lich=True,
        )
        before = (ChiNhanh.objects.count(), get_user_model().objects.count())
        with patch.dict(os.environ, DEMO_PASSWORDS, clear=False):
            with self.assertRaises(CommandError):
                call_command("seed_clinic_demo", database=self.alias, stdout=Capture([]))
        conflict.refresh_from_db()
        self.assertEqual(conflict.ten, "Real catalog collision")
        self.assertEqual(before, (ChiNhanh.objects.count(), get_user_model().objects.count()))

    def test_development_database_requires_target_backup_and_restore_confirmations(self):
        from django.conf import settings
        from core.management.commands.seed_clinic_demo import Command

        config = dict(settings.DATABASES["default"], NAME="eyeviora_clinic", HOST="127.0.0.1")
        command = Command()
        with override_settings(DATABASES={"default": config}):
            for options in (
                {},
                {"confirm_target": "wrong"},
                {"confirm_target": "eyeviora_clinic", "backup_file": "missing.dump"},
            ):
                with self.subTest(options=options), self.assertRaises(CommandError):
                    command._guard_database("default", options)

    def test_confirmed_local_development_database_requires_validated_recent_backup(self):
        from django.conf import settings
        from core.management.commands.seed_clinic_demo import Command

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, query):
                self.query = query

            def fetchone(self):
                return ("eyeviora_clinic", "127.0.0.1")

        class FakeConnection:
            def ensure_connection(self):
                return None

            def cursor(self):
                return Cursor()

        class FakeConnections:
            def __getitem__(self, alias):
                return FakeConnection()

        config = dict(settings.DATABASES["default"], NAME="eyeviora_clinic", HOST="localhost")
        with tempfile.NamedTemporaryFile(suffix=".dump") as backup:
            backup.write(b"PGDMP-test-fixture")
            backup.flush()
            opts = {
                "confirm_target": "eyeviora_clinic", "backup_file": backup.name,
                "confirm_restore_plan": True,
            }
            with override_settings(DATABASES={"default": config}), \
                    patch("core.management.commands.seed_clinic_demo.connections", FakeConnections()), \
                    patch("core.management.commands.seed_clinic_demo.shutil.which", return_value="pg_restore.exe"), \
                    patch("core.management.commands.seed_clinic_demo.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="List of TOC entries")):
                Command()._guard_database("default", opts)

    def test_production_stays_blocked_even_with_development_confirmation(self):
        from django.conf import settings
        from core.management.commands.seed_clinic_demo import Command

        config = dict(settings.DATABASES["default"], NAME="eyeviora_clinic", HOST="localhost")
        with override_settings(DATABASES={"default": config}), \
                patch.dict(os.environ, {"DJANGO_ENV": "production"}, clear=False):
            with self.assertRaises(CommandError):
                Command()._guard_database("default", {
                    "confirm_target": "eyeviora_clinic", "backup_file": "unused.dump",
                    "confirm_restore_plan": True,
                })


class Capture:
    def __init__(self, lines):
        self.lines = lines

    def write(self, value):
        self.lines.append(str(value))

