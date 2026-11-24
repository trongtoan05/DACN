import hashlib
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand

from core.models import ChiNhanh, ChiNhanhHinhAnh


class Command(BaseCommand):
    help = (
        "Relink clinic images from media/chi_nhanh/<old_id> directories to current "
        "ChiNhanh IDs using new_id = old_id - offset. Removes duplicate images by content."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--offset",
            type=int,
            default=99,
            help="Mapping offset: new_id = old_id - offset (default: 99).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without writing to database.",
        )
        parser.add_argument(
            "--keep-existing",
            action="store_true",
            help="Do not delete existing ChiNhanhHinhAnh rows before relinking.",
        )

    def handle(self, *args, **options):
        offset = options["offset"]
        dry_run = options["dry_run"]
        keep_existing = options["keep_existing"]

        media_root = Path(settings.MEDIA_ROOT)
        base_dir = media_root / "chi_nhanh"
        if not base_dir.exists():
            self.stdout.write(self.style.ERROR(f"Directory not found: {base_dir}"))
            return

        valid_ext = {".jpg", ".jpeg", ".png", ".webp"}
        updated_clinics = 0
        created_images = 0
        skipped_dirs = 0
        skipped_missing_clinic = 0
        skipped_empty_dirs = 0

        for folder in sorted(base_dir.iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 10**9):
            if not folder.is_dir() or not folder.name.isdigit():
                continue

            old_id = int(folder.name)
            new_id = old_id - offset
            clinic = ChiNhanh.objects.filter(id=new_id).first()
            if not clinic:
                skipped_missing_clinic += 1
                continue

            files = sorted(
                [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in valid_ext]
            )
            if not files:
                skipped_empty_dirs += 1
                continue

            # Keep the first file for each unique content hash (remove duplicates by content).
            unique_files = []
            seen_hashes = set()
            for file_path in files:
                digest = hashlib.md5(file_path.read_bytes()).hexdigest()
                if digest in seen_hashes:
                    continue
                seen_hashes.add(digest)
                unique_files.append(file_path)

            if not keep_existing and not dry_run:
                ChiNhanhHinhAnh.objects.filter(chi_nhanh=clinic).delete()

            for idx, image_path in enumerate(unique_files):
                rel_path = (Path("chi_nhanh") / folder.name / image_path.name).as_posix()
                if dry_run:
                    created_images += 1
                    continue

                ChiNhanhHinhAnh.objects.create(
                    chi_nhanh=clinic,
                    hinh_anh=rel_path,
                    mo_ta="",
                    thu_tu=idx,
                    la_anh_dai_dien=(idx == 0),
                )
                created_images += 1

            updated_clinics += 1

        if not dry_run:
            cache.clear()

        self.stdout.write(self.style.SUCCESS("Relink completed."))
        self.stdout.write(f"offset={offset}, dry_run={dry_run}, keep_existing={keep_existing}")
        self.stdout.write(f"updated_clinics={updated_clinics}")
        self.stdout.write(f"created_images={created_images}")
        self.stdout.write(f"skipped_missing_clinic={skipped_missing_clinic}")
        self.stdout.write(f"skipped_empty_dirs={skipped_empty_dirs}")
        self.stdout.write(f"skipped_dirs={skipped_dirs}")
        if not dry_run:
            self.stdout.write("cache_cleared=True")

