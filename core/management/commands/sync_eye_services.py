"""Idempotently synchronize the simulated EyeViora service catalogue."""
import ipaddress
import os
import re
import shutil
import subprocess
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.db.models import Q
from django.utils import timezone

from core.models import (
    BacSiDichVu,
    ChiNhanh,
    ChiTietHoaDon,
    DanhMucDichVu,
    DichVuChiNhanh,
    DichVuThucHien,
    LichKham,
)


ACTIVE_BRANCH_IDS = tuple(range(684, 693))
DEMO_SERVICE_CODE = "DEMO_MAT_KHAM_TQ"

# These are EyeViora's simulated reference prices, not a national or statutory tariff.
SERVICE_SPECS = (
    {"code": "MAT_KHAM_TQ", "name": "Khám mắt tổng quát", "group": "Khám & tư vấn",
     "price": 120000, "bookable": True,
     "description": "Khám và tư vấn mắt tổng quát. Giá tham khảo cho lượt khám; bác sĩ chỉ ghi nhận kỹ thuật bổ sung khi thực tế có chỉ định và thực hiện."},
    {"code": "MAT_KHAM_KHUC_XA", "name": "Khám và tư vấn tật khúc xạ", "group": "Khám & tư vấn",
     "price": 180000, "bookable": True,
     "description": "Khám và tư vấn tật khúc xạ. Không tự động cộng riêng kỹ thuật vào hóa đơn nếu đã nằm trong nội dung lượt khám."},
    {"code": "MAT_KHAM_TRE_EM", "name": "Khám mắt trẻ em", "group": "Khám & tư vấn",
     "price": 200000, "bookable": True,
     "description": "Khám và tư vấn mắt trẻ em. Giá tham khảo; kỹ thuật phát sinh chỉ tính khi bác sĩ xác nhận thực hiện riêng."},
    {"code": "MAT_KHAM_KHO_MAT", "name": "Khám và tư vấn khô mắt", "group": "Khám & tư vấn",
     "price": 180000, "bookable": True,
     "description": "Khám và tư vấn khô mắt. Test chuyên biệt, nếu được chỉ định và thực hiện riêng, được ghi nhận thành dịch vụ phát sinh."},
    {"code": "MAT_KHAM_GLOCOM", "name": "Khám và tư vấn glôcôm", "group": "Khám & tư vấn",
     "price": 200000, "bookable": True,
     "description": "Khám và tư vấn glôcôm. Các kỹ thuật chẩn đoán chuyên biệt chỉ tính riêng khi bác sĩ xác nhận đã thực hiện."},
    {"code": "MAT_KHAM_VONG_MAC", "name": "Khám và tư vấn bệnh lý võng mạc", "group": "Khám & tư vấn",
     "price": 200000, "bookable": True,
     "description": "Khám và tư vấn bệnh lý võng mạc. Chẩn đoán hình ảnh chuyên biệt chỉ tính riêng khi bác sĩ xác nhận đã thực hiện."},
    {"code": "MAT_DO_THI_LUC", "name": "Đo thị lực", "group": "Kỹ thuật nhãn khoa",
     "price": 50000, "bookable": False,
     "description": "Kỹ thuật theo chỉ định; bác sĩ ghi nhận khi thực tế thực hiện. Không tự động cộng vào lượt khám."},
    {"code": "MAT_DO_KHUC_XA", "name": "Đo khúc xạ máy", "group": "Kỹ thuật nhãn khoa",
     "price": 50000, "bookable": False,
     "description": "Kỹ thuật theo chỉ định; cần xác minh thiết bị và nhân sự từng chi nhánh trước triển khai thực tế."},
    {"code": "MAT_DO_NHAN_AP", "name": "Đo nhãn áp", "group": "Kỹ thuật nhãn khoa",
     "price": 60000, "bookable": False,
     "description": "Kỹ thuật theo chỉ định; cần xác minh thiết bị và nhân sự từng chi nhánh trước triển khai thực tế."},
    {"code": "MAT_TEST_KHO_MAT", "name": "Test phát hiện khô mắt", "group": "Kỹ thuật nhãn khoa",
     "price": 60000, "bookable": False,
     "description": "Kỹ thuật theo chỉ định; bác sĩ chỉ ghi nhận khi thực tế phát sinh và thực hiện."},
    {"code": "MAT_DO_THI_TRUONG", "name": "Đo thị trường", "group": "Chẩn đoán hình ảnh",
     "price": 250000, "bookable": False,
     "description": "Kỹ thuật theo chỉ định; chưa xác minh thiết bị hoặc khả năng cung cấp thực tế tại từng chi nhánh."},
    {"code": "MAT_CHUP_OCT", "name": "Chụp OCT mắt", "group": "Chẩn đoán hình ảnh",
     "price": 350000, "bookable": False,
     "description": "Kỹ thuật theo chỉ định; chưa xác minh thiết bị hoặc khả năng cung cấp thực tế tại từng chi nhánh."},
    {"code": "MAT_CHUP_DAY_MAT", "name": "Chụp đáy mắt không huỳnh quang", "group": "Chẩn đoán hình ảnh",
     "price": 350000, "bookable": False,
     "description": "Kỹ thuật theo chỉ định; chưa xác minh thiết bị hoặc khả năng cung cấp thực tế tại từng chi nhánh."},
    {"code": "MAT_BAN_DO_GIAC_MAC", "name": "Chụp bản đồ giác mạc", "group": "Chẩn đoán hình ảnh",
     "price": 300000, "bookable": False,
     "description": "Kỹ thuật theo chỉ định; chưa xác minh thiết bị hoặc khả năng cung cấp thực tế tại từng chi nhánh."},
    {"code": "MAT_SIEU_AM", "name": "Siêu âm mắt", "group": "Chẩn đoán hình ảnh",
     "price": 350000, "bookable": False,
     "description": "Kỹ thuật theo chỉ định; chưa xác minh thiết bị hoặc khả năng cung cấp thực tế tại từng chi nhánh."},
)


class Command(BaseCommand):
    help = "Synchronize EyeViora's 15 simulated ophthalmology services and reference prices."

    def add_arguments(self, parser):
        parser.add_argument("--database", default="default", help="Django database alias (default: default).")
        parser.add_argument("--dry-run", action="store_true", help="Validate and show planned changes without writes.")
        parser.add_argument("--confirm-target", help="For eyeviora_clinic, type the exact database name to confirm.")
        parser.add_argument("--backup-file", help="Recent readable custom-format PostgreSQL backup of eyeviora_clinic.")
        parser.add_argument("--confirm-restore-plan", action="store_true", help="Confirm the backup restore procedure is available.")

    def handle(self, *args, **options):
        alias = options["database"]
        self._verify_database(alias, options)
        branches = self._verify_branches(alias)
        plan = self._build_plan(alias, branches)
        self.stdout.write("EyeViora service sync plan:")
        self.stdout.write(f"- official target services: {len(SERVICE_SPECS)} (6 bookable, 9 doctor-directed)")
        self.stdout.write(f"- new catalogue rows: {plan['services_create']}; metadata/price updates: {plan['services_update']}")
        self.stdout.write(f"- branch-service links to create: {plan['branch_links_create']}")
        self.stdout.write(f"- doctor eligibility links to create: {plan['doctor_links_create']}")
        self.stdout.write(f"- disable booking for existing {DEMO_SERVICE_CODE}: {plan['demo_disable_booking']}")
        self.stdout.write("- historical appointments, performed-service rows, invoices, invoice snapshots and payment transactions: no changes")
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(f"Dry run only; no data written to '{alias}'."))
            return

        with transaction.atomic(using=alias):
            # Re-check planned identities under the same transaction before writing.
            locked_branches = self._verify_branches(alias, lock=True)
            self._apply(alias, locked_branches)
        self.stdout.write(self.style.SUCCESS("Synchronized the EyeViora service catalogue; no invoice or payment data was written."))

    def _verify_database(self, alias, options):
        if alias not in settings.DATABASES:
            raise CommandError(f"Unknown database alias '{alias}'.")
        cfg = settings.DATABASES[alias]
        name = str(cfg.get("NAME") or "").strip()
        env = " ".join((os.environ.get("DJANGO_ENV", ""), os.environ.get("ENVIRONMENT", ""))).casefold()
        if any(marker in env for marker in ("prod", "production")) or re.search(r"(^|[_-])(prod|production)([_-]|$)", name, re.I):
            raise CommandError("Production databases are never allowed for service sync.")
        if name.casefold() not in {"eyeviora_clinic"} and not re.search(r"(^|[_-])test([_-]|$)", name, re.I):
            raise CommandError("Target must be eyeviora_clinic or a clearly named test database.")
        conn = connections[alias]
        try:
            conn.ensure_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT current_database(), inet_server_addr()")
                actual_name, server_address = cursor.fetchone()
        except Exception as exc:
            raise CommandError("Could not verify the selected PostgreSQL database connection.") from exc
        if actual_name.casefold() != name.casefold():
            raise CommandError("Connected database name differs from the configured target.")
        if server_address is None:
            raise CommandError("Database server address is not available for local-host verification.")
        try:
            if not ipaddress.ip_address(str(server_address)).is_loopback:
                raise CommandError("Service sync is allowed only on a local PostgreSQL server.")
        except ValueError as exc:
            raise CommandError("Could not verify that PostgreSQL is local.") from exc
        if name.casefold() == "eyeviora_clinic" and not options["dry_run"]:
            if options.get("confirm_target") != "eyeviora_clinic":
                raise CommandError("Writing eyeviora_clinic requires --confirm-target eyeviora_clinic.")
            self._verify_backup(options.get("backup_file"))
            if not options.get("confirm_restore_plan"):
                raise CommandError("Writing eyeviora_clinic requires --confirm-restore-plan.")

    @staticmethod
    def _verify_backup(path):
        if not path:
            raise CommandError("A recent custom-format backup is required before writing eyeviora_clinic.")
        try:
            stat = os.stat(path)
        except OSError as exc:
            raise CommandError("The backup path is not readable.") from exc
        age = timezone.now().timestamp() - stat.st_mtime
        if not os.path.isfile(path) or stat.st_size <= 0 or age < 0 or age > 24 * 60 * 60:
            raise CommandError("Backup must be a non-empty regular file created within the last 24 hours.")
        pg_restore = shutil.which("pg_restore")
        if not pg_restore:
            for candidate in (r"C:\Program Files\PostgreSQL\18\bin\pg_restore.exe",):
                if os.path.isfile(candidate):
                    pg_restore = candidate
                    break
        if not pg_restore:
            raise CommandError("pg_restore is required to validate the backup archive.")
        try:
            result = subprocess.run([pg_restore, "--list", path], capture_output=True, text=True, timeout=45, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CommandError("Could not validate the PostgreSQL backup archive.") from exc
        if result.returncode or not result.stdout.strip():
            raise CommandError("Backup is not a readable PostgreSQL custom-format archive.")

    @staticmethod
    def _verify_branches(alias, lock=False):
        rows = ChiNhanh.objects.using(alias).filter(pk__in=ACTIVE_BRANCH_IDS, dang_hoat_dong=True)
        if lock:
            rows = rows.select_for_update()
        branches = list(rows.order_by("pk"))
        if tuple(branch.pk for branch in branches) != ACTIVE_BRANCH_IDS:
            raise CommandError("Expected exactly the nine active EyeViora branches 684–692; no data was changed.")
        other_active = ChiNhanh.objects.using(alias).filter(dang_hoat_dong=True).exclude(pk__in=ACTIVE_BRANCH_IDS).exists()
        if other_active:
            raise CommandError("Unexpected active branches exist outside 684–692; refusing broad catalogue synchronization.")
        return branches

    @staticmethod
    def _service_references(alias, service):
        return {
            "appointments": LichKham.objects.using(alias).filter(
                Q(dich_vu_dat_truoc=service) | Q(dich_vu_dat_truoc_nhieu=service)
            ).distinct().count(),
            "performed": DichVuThucHien.objects.using(alias).filter(dich_vu=service).count(),
            "invoice_lines": ChiTietHoaDon.objects.using(alias).filter(dich_vu=service).count(),
        }

    def _build_plan(self, alias, branches):
        target_codes = [item["code"] for item in SERVICE_SPECS]
        existing = {row.ma_dich_vu: row for row in DanhMucDichVu.objects.using(alias).filter(ma_dich_vu__in=target_codes)}
        creates = updates = 0
        for spec in SERVICE_SPECS:
            current = existing.get(spec["code"])
            if current is None:
                creates += 1
                continue
            desired = (spec["name"], spec["group"], spec["description"], Decimal(spec["price"]), True, spec["bookable"])
            actual = (current.ten, current.nhom, current.mo_ta, current.don_gia, current.dang_ap_dung, current.cho_phep_dat_lich)
            if actual != desired:
                refs = self._service_references(alias, current)
                if refs["appointments"] or refs["performed"]:
                    raise CommandError(f"Service {spec['code']} has appointment/exam references; refusing to change its catalog identity or price.")
                updates += 1
        links = assignments = 0
        for spec in SERVICE_SPECS:
            service = existing.get(spec["code"])
            for branch in branches:
                offer = (DichVuChiNhanh.objects.using(alias).filter(dich_vu=service, chi_nhanh=branch).first()
                         if service else None)
                if offer is None:
                    links += 1
                if spec["bookable"]:
                    if offer and service:
                        eligible = BacSiDichVu.objects.using(alias).filter(dich_vu_chi_nhanh=offer, dang_ap_dung=True)
                    else:
                        demo_offer = DichVuChiNhanh.objects.using(alias).filter(
                            chi_nhanh=branch, dich_vu__ma_dich_vu=DEMO_SERVICE_CODE, dang_ap_dung=True
                        ).first()
                        eligible = (BacSiDichVu.objects.using(alias).filter(
                            dich_vu_chi_nhanh=demo_offer, dang_ap_dung=True
                        ) if demo_offer else BacSiDichVu.objects.using(alias).none())
                    if service:
                        assignments += sum(not BacSiDichVu.objects.using(alias).filter(
                            bac_si_id=row.bac_si_id,
                            dich_vu_chi_nhanh__dich_vu=service,
                            dich_vu_chi_nhanh__chi_nhanh=branch,
                        ).exists() for row in eligible)
                    else:
                        assignments += eligible.count()
        demo = DanhMucDichVu.objects.using(alias).filter(ma_dich_vu=DEMO_SERVICE_CODE).first()
        return {
            "services_create": creates,
            "services_update": updates,
            "branch_links_create": links,
            "doctor_links_create": assignments,
            "demo_disable_booking": bool(demo and demo.cho_phep_dat_lich),
        }

    def _apply(self, alias, branches):
        existing = {row.ma_dich_vu: row for row in DanhMucDichVu.objects.using(alias).select_for_update().filter(
            ma_dich_vu__in=[item["code"] for item in SERVICE_SPECS]
        )}
        services = {}
        for spec in SERVICE_SPECS:
            values = {
                "ten": spec["name"], "nhom": spec["group"], "mo_ta": spec["description"],
                "don_gia": Decimal(spec["price"]), "dang_ap_dung": True,
                "cho_phep_dat_lich": spec["bookable"],
            }
            service = existing.get(spec["code"])
            if service is None:
                service = DanhMucDichVu(ma_dich_vu=spec["code"], **values)
                service._state.db = alias
                service.full_clean()
                service.save(using=alias)
            elif any(getattr(service, field) != value for field, value in values.items()):
                for field, value in values.items():
                    setattr(service, field, value)
                service._state.db = alias
                service.full_clean()
                service.save(using=alias, update_fields=[*values, "ngay_cap_nhat"])
            services[spec["code"]] = service

        demo = DanhMucDichVu.objects.using(alias).select_for_update().filter(ma_dich_vu=DEMO_SERVICE_CODE).first()
        if demo and demo.cho_phep_dat_lich:
            demo.cho_phep_dat_lich = False
            demo._state.db = alias
            demo.full_clean()
            demo.save(using=alias, update_fields=["cho_phep_dat_lich", "ngay_cap_nhat"])

        for spec in SERVICE_SPECS:
            service = services[spec["code"]]
            for branch in branches:
                offer, _ = DichVuChiNhanh.objects.using(alias).get_or_create(
                    dich_vu=service, chi_nhanh=branch, defaults={"dang_ap_dung": True}
                )
                # Never re-enable an existing branch/service assignment.
                if spec["bookable"]:
                    demo_offer = DichVuChiNhanh.objects.using(alias).filter(
                        dich_vu__ma_dich_vu=DEMO_SERVICE_CODE, chi_nhanh=branch, dang_ap_dung=True
                    ).first()
                    if not demo_offer:
                        continue
                    doctor_ids = BacSiDichVu.objects.using(alias).filter(
                        dich_vu_chi_nhanh=demo_offer, dang_ap_dung=True,
                        bac_si__chi_nhanh=branch,
                    ).values_list("bac_si_id", flat=True)
                    for doctor_id in doctor_ids:
                        BacSiDichVu.objects.using(alias).get_or_create(
                            bac_si_id=doctor_id, dich_vu_chi_nhanh=offer,
                            defaults={"dang_ap_dung": True},
                        )

        self.stdout.write("Technical services remain non-bookable. No new doctor eligibility was assigned for technical procedures.")
