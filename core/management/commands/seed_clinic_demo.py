"""Create isolated, clearly labelled eye-clinic demo data."""
import datetime
import ipaddress
import os
import re
import shutil
import subprocess
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.utils import timezone

from core.models import (
    BacSi, BacSiDichVu, BenhNhan, ChiNhanh, DanhMucDichVu,
    DichVuChiNhanh, GioLamViecBacSi, GioLamViecChiNhanh, Khoa,
    LeTanChiNhanh,
)


DEMO_PREFIX = "DEMO_CLINIC_"
BRANCHES = (
    {"key": "A", "name": "[DEMO] Phòng khám mắt A", "ward": "Phường Demo A",
     "address": "Địa chỉ minh họa A — không phải cơ sở y tế thật",
     "point": (106.7009, 10.7769)},
    {"key": "B", "name": "[DEMO] Phòng khám mắt B", "ward": "Phường Demo B",
     "address": "Địa chỉ minh họa B — không phải cơ sở y tế thật",
     "point": (106.7017, 10.7810)},
)
SERVICES = (
    {"code": "DEMO_MAT_KHAM_TQ", "name": "DEMO · Khám mắt tổng quát", "group": "Khám mắt",
     "price": Decimal("100000"), "bookable": True},
    {"code": "DEMO_MAT_DO_TLUC", "name": "DEMO · Đo thị lực", "group": "Khám mắt",
     "price": Decimal("50000"), "bookable": False},
    {"code": "DEMO_MAT_KHUC_XA", "name": "DEMO · Đo khúc xạ", "group": "Khám mắt",
     "price": Decimal("80000"), "bookable": False},
    {"code": "DEMO_MAT_NHAN_AP", "name": "DEMO · Đo nhãn áp", "group": "Khám mắt",
     "price": Decimal("70000"), "bookable": False},
)
WEEKDAYS = range(1, 7)  # Monday through Saturday, matching the app's weekday convention.


class Command(BaseCommand):
    help = "Prepare demo-only clinic branches, services, accounts and schedules on an isolated database."

    def add_arguments(self, parser):
        parser.add_argument("--database", required=True, help="Configured Django database alias.")
        parser.add_argument("--dry-run", action="store_true", help="Show planned records without writing anything.")
        parser.add_argument("--confirm-target", help="For eyeviora_clinic only: type the exact target database name to confirm.")
        parser.add_argument("--backup-file", help="For eyeviora_clinic only: path to a non-empty backup created within the last 24 hours.")
        parser.add_argument("--confirm-restore-plan", action="store_true", help="Confirm that a restore procedure for the supplied backup is available.")

    def handle(self, *args, **options):
        alias = options["database"]
        self._guard_database(alias, options)
        passwords = {
            "patient": os.environ.get("CLINIC_DEMO_PATIENT_PASSWORD", ""),
            "doctor_a": os.environ.get("CLINIC_DEMO_DOCTOR_A_PASSWORD", ""),
            "doctor_b": os.environ.get("CLINIC_DEMO_DOCTOR_B_PASSWORD", ""),
            "reception_a": os.environ.get("CLINIC_DEMO_RECEPTION_A_PASSWORD", ""),
            "reception_b": os.environ.get("CLINIC_DEMO_RECEPTION_B_PASSWORD", ""),
        }
        if not options["dry_run"]:
            missing = [name for name, value in passwords.items() if not value]
            if missing:
                raise CommandError("Set a separate password for every new demo role in the environment before seeding.")
            for password in passwords.values():
                validate_password(password)
        plan = self._plan()
        self.stdout.write("Demo seed plan (no real patient or payment records):")
        for line in plan:
            self.stdout.write(f"- {line}")
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(f"Dry run only; no rows written to database alias '{alias}'."))
            return
        try:
            with transaction.atomic(using=alias):
                self._seed(alias, passwords)
        except Exception as exc:
            raise CommandError(f"Demo seed rolled back: {exc}") from exc
        self.stdout.write(self.style.SUCCESS("Demo data is ready. Existing matching demo rows were reused; no appointments, invoices, or transactions were created."))

    def _guard_database(self, alias, options=None):
        options = options or {}
        if alias not in settings.DATABASES:
            raise CommandError(f"Unknown database alias '{alias}'. Configure and verify a dedicated demo/test database first.")
        cfg = settings.DATABASES[alias]
        name = str(cfg.get("NAME") or "").strip()
        environment = " ".join((os.environ.get("DJANGO_ENV", ""), os.environ.get("ENVIRONMENT", ""))).casefold()
        if any(marker in environment for marker in ("prod", "production")):
            raise CommandError("Demo seeding is disabled in production environments.")
        if not name or re.search(r"(^|[_-])(prod|production)([_-]|$)", name, re.I):
            raise CommandError("Database name is empty or marked production; refusing to seed.")
        if name.casefold() in {"postgres", "template0", "template1"}:
            raise CommandError("Refusing to seed a PostgreSQL system database.")
        if name.casefold() == "eyeviora_clinic":
            if options.get("dry_run"):
                pass
            else:
                if options.get("confirm_target") != "eyeviora_clinic":
                    raise CommandError("Seeding eyeviora_clinic requires --confirm-target eyeviora_clinic.")
                backup_file = options.get("backup_file")
                if not backup_file:
                    raise CommandError("Seeding eyeviora_clinic requires --backup-file pointing to a recent backup.")
                try:
                    backup_stat = os.stat(backup_file)
                except OSError as exc:
                    raise CommandError("The specified backup file does not exist or cannot be read.") from exc
                if not os.path.isfile(backup_file) or backup_stat.st_size <= 0:
                    raise CommandError("The specified backup must be a non-empty regular file.")
                backup_age = timezone.now().timestamp() - backup_stat.st_mtime
                if backup_age < 0 or backup_age > 24 * 60 * 60:
                    raise CommandError("The backup must have been created within the last 24 hours.")
                pg_restore = shutil.which("pg_restore")
                if not pg_restore:
                    raise CommandError("pg_restore is required to validate a custom-format PostgreSQL backup.")
                try:
                    backup_check = subprocess.run(
                        [pg_restore, "--list", backup_file], capture_output=True,
                        text=True, timeout=30, check=False,
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise CommandError("Could not validate the PostgreSQL backup archive.") from exc
                if backup_check.returncode != 0 or not backup_check.stdout.strip():
                    raise CommandError("The specified file is not a readable PostgreSQL custom-format backup.")
                if not options.get("confirm_restore_plan"):
                    raise CommandError("Seeding eyeviora_clinic requires --confirm-restore-plan.")
                host = str(cfg.get("HOST") or "localhost").strip().casefold()
                if host not in {"localhost", "127.0.0.1", "::1"}:
                    raise CommandError("eyeviora_clinic seeding is allowed only through a local PostgreSQL host.")
        else:
            if not re.search(r"(^|[_-])(demo|test)([_-]|$)", name, re.I):
                raise CommandError("Target database name must clearly contain 'demo' or 'test'.")
            if "test" not in name.casefold() and environment.strip() not in {"demo", "development", "test"}:
                raise CommandError("For a non-test database, set DJANGO_ENV=demo and select a dedicated demo database alias.")
        try:
            connections[alias].ensure_connection()
            if name.casefold() == "eyeviora_clinic":
                with connections[alias].cursor() as cursor:
                    cursor.execute("SELECT current_database(), inet_server_addr()")
                    actual_name, server_address = cursor.fetchone()
                if actual_name.casefold() != "eyeviora_clinic" or not server_address:
                    raise CommandError("Connected database identity could not be verified as local eyeviora_clinic.")
                try:
                    if not ipaddress.ip_address(str(server_address)).is_loopback:
                        raise CommandError("Connected PostgreSQL server is not local; refusing to seed eyeviora_clinic.")
                except ValueError as exc:
                    raise CommandError("Connected PostgreSQL server address is not verifiably local.") from exc
        except Exception as exc:
            if isinstance(exc, CommandError):
                raise
            raise CommandError(f"Cannot connect to the selected isolated database: {exc}") from exc

    def _plan(self):
        return [
            "2 explicitly labelled demo branches with illustrative GIS coordinates",
            "4 demo eye-service catalog entries with illustrative prices (1 bookable, 3 doctor-added)",
            "2 demo doctors, 1 demo patient, and 2 branch-scoped receptionist accounts",
            "weekday branch/doctor schedules; no appointments, exams, invoices, or payment transactions",
            "existing global admin account is not created or changed; use an authorized admin in the isolated demo database",
        ]

    @staticmethod
    def _save(instance, alias):
        # Custom model save() methods call full_clean(); bind validation reads to
        # the selected alias before save so no validation query touches default.
        instance._state.db = alias
        instance.full_clean()
        instance.save(using=alias)
        return instance

    @staticmethod
    def _ensure(model, alias, lookup, defaults):
        qs = model.objects.using(alias)
        rows = list(qs.filter(**lookup)[:2])
        if len(rows) > 1:
            raise CommandError(f"Ambiguous existing demo identity for {model.__name__}: {lookup}")
        if rows:
            obj = rows[0]
            for field, expected in defaults.items():
                actual = getattr(obj, f"{field}_id") if field.endswith("_id") else getattr(obj, field)
                if field.endswith("_id"):
                    pass
                elif hasattr(expected, "pk"):
                    actual = getattr(obj, f"{field}_id")
                    expected = expected.pk
                if actual != expected:
                    raise CommandError(f"Existing demo record {model.__name__} {lookup} differs from the seed specification; refusing to overwrite it.")
            return obj, False
        obj = model(**lookup, **defaults)
        return Command._save(obj, alias), True

    def _ensure_user(self, alias, username, role, password, branch=None):
        User = get_user_model()
        email = f"{username}@demo.invalid"
        qs = User.objects.db_manager(alias)
        user = qs.filter(username=username).first()
        if user:
            if (user.email != email or user.first_name != "DEMO" or user.last_name != role
                    or not user.is_active or user.is_staff or user.is_superuser):
                raise CommandError(f"User '{username}' exists with unexpected attributes; refusing to overwrite it.")
            other_profiles = {
                "patient": ("bac_si", "le_tan_chi_nhanh"),
                "doctor": ("benh_nhan", "le_tan_chi_nhanh"),
                "receptionist": ("benh_nhan", "bac_si"),
            }.get(role, ())
            if any(hasattr(user, profile) for profile in other_profiles):
                raise CommandError(f"User '{username}' has an incompatible role profile; refusing to reuse it.")
        else:
            user = User(username=username, email=email, first_name="DEMO", last_name=role)
            user.set_password(password)
            user.save(using=alias)
        if role == "patient":
            phone = "00000000001"
            patient, _ = self._ensure(BenhNhan, alias, {"user_id": user.pk}, {
                "ho_ten": "Bệnh nhân DEMO", "ngay_sinh": timezone.localdate().replace(
                    year=timezone.localdate().year - 30,
                    day=min(timezone.localdate().day, 28) if timezone.localdate().month == 2 else timezone.localdate().day,
                ),
                "gioi_tinh": "khac", "so_dien_thoai": phone, "dia_chi": "Địa chỉ minh họa",
            })
            return user, patient
        if role == "doctor":
            dept, _ = self._ensure(Khoa, alias, {"ten": f"{DEMO_PREFIX}KHOA_{branch['key']}", "chi_nhanh_id": branch["obj"].pk}, {})
            doctor, _ = self._ensure(BacSi, alias, {"user_id": user.pk}, {
                "ho_ten": f"Bác sĩ DEMO {branch['key']}", "chuyen_khoa": "Nhãn khoa (demo)",
                "khoa": dept, "chi_nhanh": branch["obj"], "so_dien_thoai": f"0000000000{1 + (branch['key'] == 'B')}"
            })
            return user, doctor
        profile, _ = self._ensure(LeTanChiNhanh, alias, {"user_id": user.pk}, {"chi_nhanh": branch["obj"]})
        if profile.chi_nhanh_id != branch["obj"].pk:
            raise CommandError(f"Receptionist '{username}' is not assigned to the expected demo branch.")
        return user, profile

    def _seed(self, alias, passwords):
        branch_rows = []
        for spec in BRANCHES:
            defaults = {
                "dia_chi": spec["address"], "so_dien_thoai": "", "email": f"branch-{spec['key'].lower()}@demo.invalid",
                "phuong": spec["ward"], "vi_tri": Point(*spec["point"], srid=4326),
                "gio_mo": datetime.time(8), "gio_dong": datetime.time(17),
            }
            branch, _ = self._ensure(ChiNhanh, alias, {"ten": spec["name"]}, defaults)
            branch_rows.append({**spec, "obj": branch})

        services = []
        for spec in SERVICES:
            defaults = {"ten": spec["name"], "nhom": spec["group"],
                        "mo_ta": "DỮ LIỆU DEMO; giá minh họa, không phải bảng giá chính thức.",
                        "don_gia": spec["price"], "dang_ap_dung": True,
                        "cho_phep_dat_lich": spec["bookable"]}
            service, _ = self._ensure(DanhMucDichVu, alias, {"ma_dich_vu": spec["code"]}, defaults)
            services.append(service)

        demo_doctors = []
        for branch in branch_rows:
            key = branch["key"].lower()
            doctor_user, doctor = self._ensure_user(alias, f"demo_doctor_{key}", "doctor", passwords[f"doctor_{key}"], branch)
            demo_doctors.append(doctor)
            self._ensure_user(alias, f"demo_reception_{key}", "receptionist", passwords[f"reception_{key}"], branch)
            for service in services:
                offer, _ = self._ensure(DichVuChiNhanh, alias,
                    {"chi_nhanh_id": branch["obj"].pk, "dich_vu_id": service.pk}, {"dang_ap_dung": True})
                self._ensure(BacSiDichVu, alias,
                    {"bac_si_id": doctor.pk, "dich_vu_chi_nhanh_id": offer.pk}, {"dang_ap_dung": True})
            for weekday in WEEKDAYS:
                self._ensure(GioLamViecChiNhanh, alias,
                    {"chi_nhanh_id": branch["obj"].pk, "thu": weekday},
                    {"gio_mo": datetime.time(8), "gio_dong": datetime.time(17), "nghi": False})
                self._ensure(GioLamViecBacSi, alias,
                    {"bac_si_id": doctor.pk, "thu": weekday},
                    {"gio_bat_dau": datetime.time(8),
                     "gio_ket_thuc": datetime.time(17), "nghi": False})
        self._ensure_user(alias, "demo_patient", "patient", passwords["patient"])
        self.stdout.write(f"Prepared {len(branch_rows)} branches, {len(services)} services and {len(demo_doctors)} doctors.")
