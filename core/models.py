import datetime
import math
import uuid
from decimal import Decimal

from django.contrib.gis.db import models
from django.contrib.auth.models import User
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MinValueValidator, MaxValueValidator, RegexValidator
from django.db.models.deletion import ProtectedError
from django.db.models.lookups import GreaterThanOrEqual, LessThanOrEqual
from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver
from django.utils import timezone
from django.db import transaction
from .private_media import private_exam_storage
from .exam_uploads import validate_exam_image, MAX_IMAGE_COUNT
from django.urls import reverse

phone_validator = RegexValidator(
    regex=r"^\d{9,15}$",
    message="Số điện thoại phải gồm 9-15 chữ số."
)
name_validator = RegexValidator(
    regex=r".*[A-Za-zÀ-ỹ].*",
    message="Tên phải có ít nhất một chữ cái."
)
visual_acuity_validator = RegexValidator(
    regex=r'(?i)^(?:\d+(?:\.\d+)?|(?:6|20)/\d{1,3}|CF(?:\s+\d+(?:\.\d+)?\s*m)?|HM|LP|NLP)$',
    message='Nhập thị lực dạng 6/12, 20/40, số thập phân, CF, HM, LP hoặc NLP.',
)


def payment_reference():
    return uuid.uuid4().hex


class DanhMucDichVu(models.Model):
    """Global catalog; later usage/invoice rows must snapshot the unit price."""

    ma_dich_vu = models.CharField(
        'Mã dịch vụ', max_length=32, unique=True,
        validators=[RegexValidator(r'^[A-Z0-9][A-Z0-9_-]*$', 'Mã chỉ gồm chữ không dấu, số, dấu gạch ngang hoặc gạch dưới.')],
    )
    ten = models.CharField('Tên dịch vụ', max_length=255)
    nhom = models.CharField('Nhóm dịch vụ', max_length=100, db_index=True)
    mo_ta = models.TextField('Mô tả', blank=True, default='')
    don_gia = models.DecimalField('Đơn giá (VND)', max_digits=10, decimal_places=0, validators=[MinValueValidator(Decimal('0'))])
    thoi_luong_phut = models.PositiveSmallIntegerField(
        'Thời lượng dự kiến (phút)', default=30,
        validators=[MinValueValidator(15), MaxValueValidator(480)],
        help_text='Thời lượng ước tính để kiểm tra khung giờ; không ảnh hưởng giá dịch vụ.',
    )
    dang_ap_dung = models.BooleanField('Đang áp dụng', default=True, db_index=True)
    cho_phep_dat_lich = models.BooleanField(
        'Cho phép đặt lịch trước',
        default=False,
        db_index=True,
        help_text='Bật cho dịch vụ bệnh nhân có thể chọn ban đầu; kỹ thuật chỉ định khi khám để tắt.',
    )
    ngay_tao = models.DateTimeField('Ngày tạo', auto_now_add=True)
    ngay_cap_nhat = models.DateTimeField('Ngày cập nhật', auto_now=True)

    class Meta:
        ordering = ['ma_dich_vu']
        verbose_name = 'Dịch vụ phòng khám'
        verbose_name_plural = 'Danh mục dịch vụ phòng khám'
        constraints = [
            models.CheckConstraint(condition=models.Q(don_gia__gte=0), name='dichvu_don_gia_nonnegative'),
            models.CheckConstraint(condition=models.Q(thoi_luong_phut__gte=15, thoi_luong_phut__lte=480), name='dichvu_duration_range'),
            models.CheckConstraint(condition=models.Q(ma_dich_vu__regex=r'^[A-Z0-9][A-Z0-9_-]*$'), name='dichvu_ma_canonical'),
            models.CheckConstraint(condition=models.Q(ten__regex=r'\S', nhom__regex=r'\S'), name='dichvu_required_text'),
        ]

    def clean_fields(self, exclude=None):
        for name in ('ma_dich_vu', 'ten', 'nhom', 'mo_ta'):
            value = getattr(self, name)
            if isinstance(value, str):
                setattr(self, name, value.strip().upper() if name == 'ma_dich_vu' else value.strip())
        super().clean_fields(exclude=exclude)

    def save(self, *args, **kwargs):
        # Reject fractional/non-finite money before the database can round it.
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.ma_dich_vu} — {self.ten}'


class DichVuChiNhanh(models.Model):
    """A global catalog service offered at one clinic branch."""

    dich_vu = models.ForeignKey(
        DanhMucDichVu,
        on_delete=models.PROTECT,
        related_name='chi_nhanh_ap_dung',
        verbose_name='Dịch vụ',
    )
    chi_nhanh = models.ForeignKey(
        'ChiNhanh',
        on_delete=models.PROTECT,
        related_name='dich_vu_cung_cap',
        verbose_name='Chi nhánh',
    )
    dang_ap_dung = models.BooleanField('Đang cung cấp', default=True, db_index=True)
    ngay_tao = models.DateTimeField(auto_now_add=True)
    ngay_cap_nhat = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['chi_nhanh__ten', 'dich_vu__ma_dich_vu']
        verbose_name = 'Dịch vụ tại chi nhánh'
        verbose_name_plural = 'Dịch vụ tại chi nhánh'
        constraints = [
            models.UniqueConstraint(fields=['dich_vu', 'chi_nhanh'], name='clinic_service_unique'),
        ]

    def clean(self):
        super().clean()
        previous = type(self).objects.filter(pk=self.pk).first() if self.pk else None
        relationship_changed = previous is None or (
            previous.chi_nhanh_id != self.chi_nhanh_id or previous.dich_vu_id != self.dich_vu_id
        )
        reactivated = previous is not None and not previous.dang_ap_dung and self.dang_ap_dung
        if self.dang_ap_dung and (relationship_changed or reactivated):
            errors = {}
            if self.chi_nhanh_id and not ChiNhanh.objects.filter(
                pk=self.chi_nhanh_id, dang_hoat_dong=True,
            ).exists():
                errors['chi_nhanh'] = 'Không thể cung cấp dịch vụ tại chi nhánh đã ngừng hoạt động.'
            if self.dich_vu_id and not DanhMucDichVu.objects.filter(
                pk=self.dich_vu_id, dang_ap_dung=True,
            ).exists():
                errors['dich_vu'] = 'Không thể phân công dịch vụ đã ngừng áp dụng trong danh mục chung.'
            if errors:
                raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.chi_nhanh.ten} · {self.dich_vu.ma_dich_vu}'


@receiver(pre_delete, sender=DanhMucDichVu)
def preserve_service_catalog(sender, instance, **kwargs):
    # Applies to both instance.delete() and QuerySet.delete(). No hard-delete UI.
    raise ProtectedError('Không xóa dịch vụ; hãy chuyển sang ngừng áp dụng để giữ lịch sử.', [instance])


@receiver(pre_delete, sender=DichVuChiNhanh)
def preserve_branch_service(sender, instance, **kwargs):
    raise ProtectedError('Không xóa quan hệ dịch vụ-chi nhánh; hãy ngừng cung cấp để giữ lịch sử.', [instance])


class HoaDon(models.Model):
    class Nguon(models.TextChoices):
        THU_NGHIEM = 'manual', 'Hóa đơn thử nghiệm cũ'
        DICH_VU = 'services', 'Tính từ dịch vụ thực hiện'

    class TrangThai(models.TextChoices):
        CHUA_THANH_TOAN = 'unpaid', 'Chưa thanh toán'
        DA_THANH_TOAN = 'paid', 'Đã thanh toán'

    lich_kham = models.OneToOneField('LichKham', on_delete=models.PROTECT, related_name='hoa_don')
    phieu_kham = models.OneToOneField('PhieuKham', on_delete=models.PROTECT, related_name='hoa_don', null=True, blank=True)
    nguon = models.CharField(max_length=10, choices=Nguon.choices, default=Nguon.THU_NGHIEM)
    phien_ban = models.PositiveIntegerField(default=1)
    so_tien = models.DecimalField(max_digits=10, decimal_places=0)
    tong_chi_phi = models.DecimalField(max_digits=10, decimal_places=0, null=True, blank=True)
    trang_thai = models.CharField(max_length=10, choices=TrangThai.choices, default=TrangThai.CHUA_THANH_TOAN)
    can_doi_soat = models.BooleanField(default=False)
    nguoi_tao = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    ngay_tao = models.DateTimeField(auto_now_add=True)
    ngay_cap_nhat = models.DateTimeField(auto_now=True)
    ngay_thanh_toan = models.DateTimeField(null=True, blank=True)

    class Meta:
        permissions = [
            ('thu_vnpay', 'Thu tiền VNPAY Sandbox tại quầy'),
            ('quan_tri_he_thong', 'Quản trị toàn hệ thống'),
        ]
        constraints = [
            models.CheckConstraint(condition=(models.Q(so_tien__gte=1, so_tien__lte=9999999999) | models.Q(so_tien=0, trang_thai='paid')), name='hoadon_self_pay_amount'),
            models.CheckConstraint(condition=(models.Q(trang_thai='unpaid', ngay_thanh_toan__isnull=True) | models.Q(trang_thai='paid', ngay_thanh_toan__isnull=False)), name='hoadon_valid_state'),
            models.CheckConstraint(condition=models.Q(phien_ban__gte=1), name='hoadon_version_positive'),
            models.CheckConstraint(condition=models.Q(nguon='manual') | models.Q(nguon='services', phieu_kham__isnull=False), name='hoadon_source_exam'),
        ]
        verbose_name = 'Hóa đơn'
        verbose_name_plural = 'Hóa đơn'

    def __str__(self):
        return f'Hóa đơn #{self.pk} / lịch khám #{self.lich_kham_id}'

    @property
    def chi_tiet_hien_hanh(self):
        return self.chi_tiets.filter(phien_ban=self.phien_ban).order_by('id')



class ChiTietHoaDon(models.Model):
    """Append-only price snapshots; old versions remain available for audit."""
    hoa_don = models.ForeignKey(HoaDon, on_delete=models.PROTECT, related_name='chi_tiets')
    dich_vu_thuc_hien = models.ForeignKey('DichVuThucHien', on_delete=models.PROTECT, related_name='chi_tiet_hoa_don')
    dich_vu = models.ForeignKey(DanhMucDichVu, on_delete=models.PROTECT, related_name='+')
    phien_ban = models.PositiveIntegerField(default=1)
    ma_dich_vu = models.CharField(max_length=32)
    ten_dich_vu = models.CharField(max_length=255)
    nhom_dich_vu = models.CharField(max_length=100)
    so_luong = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    la_dich_vu_dat_truoc = models.BooleanField(
        'Nguồn là dịch vụ đặt trước đã thực hiện', default=False,
    )
    don_gia = models.DecimalField(max_digits=10, decimal_places=0, validators=[MinValueValidator(Decimal('0'))])
    thanh_tien = models.DecimalField(max_digits=10, decimal_places=0, validators=[MinValueValidator(Decimal('0'))])
    thoi_gian_tao = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['phien_ban', 'id']
        constraints = [
            models.UniqueConstraint(fields=['hoa_don', 'phien_ban', 'dich_vu'], name='cthd_version_service_unique'),
            models.CheckConstraint(condition=models.Q(phien_ban__gte=1, so_luong__gte=1, don_gia__gte=0, thanh_tien__gte=0), name='cthd_valid_numbers'),
            models.CheckConstraint(condition=models.Q(thanh_tien=models.F('don_gia') * models.F('so_luong')), name='cthd_exact_total'),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Chi tiết đã chốt không được ghi đè; hãy tạo phiên bản hóa đơn mới.')
        return super().save(*args, **kwargs)


@receiver(pre_delete, sender=ChiTietHoaDon)
def preserve_invoice_lines(sender, instance, **kwargs):
    raise ProtectedError('Không xóa chi tiết hóa đơn đã chốt.', [instance])


class GiaoDichThanhToan(models.Model):
    class PhuongThuc(models.TextChoices):
        VNPAY = 'VNPAY', 'VNPAY'
        TIEN_MAT = 'CASH', 'Tiền mặt'

    class TrangThai(models.TextChoices):
        CHO = 'pending', 'Đang chờ xác nhận IPN'
        THANH_CONG = 'success', 'Thành công'
        THAT_BAI = 'failed', 'Thất bại'
        HUY = 'cancelled', 'Đã hủy'
        HET_HAN = 'expired', 'Hết hạn — VNPAY báo hết hạn'
        # Local expiry only: VNPAY never told us the outcome. A late IPN may still
        # prove the money moved, so this state must never be treated as final.
        QUA_HAN_CHO = 'timeout', 'Quá hạn chờ IPN — chưa có kết quả'
        # A verified success arrived after the invoice was settled: money was
        # taken twice and has to be refunded, not collected again.
        THU_TRUNG = 'duplicate', 'Thu trùng — cần hoàn tiền'

    hoa_don = models.ForeignKey(HoaDon, on_delete=models.PROTECT, related_name='giao_dichs')
    ma_tham_chieu = models.CharField(max_length=32, unique=True, default=payment_reference, editable=False)
    so_tien = models.DecimalField(max_digits=10, decimal_places=0)
    phuong_thuc = models.CharField(max_length=10, choices=PhuongThuc.choices, default='VNPAY', editable=False)
    chi_nhanh = models.ForeignKey('ChiNhanh', on_delete=models.PROTECT, null=True, blank=True, related_name='giao_dichs')
    tmn_code = models.CharField(max_length=32, blank=True)
    trang_thai = models.CharField(max_length=12, choices=TrangThai.choices, default=TrangThai.CHO)
    ma_phan_hoi = models.CharField(max_length=2, blank=True)
    trang_thai_vnpay = models.CharField(max_length=2, blank=True)
    ma_giao_dich_vnpay = models.CharField(max_length=15, blank=True)
    vnp_pay_date = models.CharField(max_length=14, blank=True)
    nguoi_tao = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    ngay_tao = models.DateTimeField(auto_now_add=True)
    ngay_cap_nhat = models.DateTimeField(auto_now=True)
    het_han_luc = models.DateTimeField(null=True, blank=True)
    hoan_tat_luc = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-ngay_tao', '-pk']
        constraints = [
            models.CheckConstraint(condition=(models.Q(so_tien__lte=9999999999) & (models.Q(phuong_thuc='VNPAY', so_tien__gte=5000) | models.Q(phuong_thuc='CASH', so_tien__gte=1))), name='giaodich_method_amount'),
            models.CheckConstraint(condition=(models.Q(phuong_thuc='VNPAY', het_han_luc__isnull=False) | models.Q(phuong_thuc='CASH', trang_thai='success', chi_nhanh__isnull=False, het_han_luc__isnull=True, tmn_code='', ma_phan_hoi='', trang_thai_vnpay='', ma_giao_dich_vnpay='', vnp_pay_date='')), name='giaodich_valid_method'),
            models.CheckConstraint(condition=(models.Q(trang_thai='pending', hoan_tat_luc__isnull=True) | models.Q(trang_thai__in=['success', 'failed', 'cancelled', 'expired', 'timeout', 'duplicate'], hoan_tat_luc__isnull=False)), name='giaodich_valid_state'),
            models.UniqueConstraint(fields=['hoa_don'], condition=models.Q(trang_thai='pending'), name='giaodich_one_pending_invoice'),
            models.UniqueConstraint(fields=['hoa_don'], condition=models.Q(trang_thai='success'), name='giaodich_one_success_invoice'),
        ]
        verbose_name = 'Giao dịch thanh toán'
        verbose_name_plural = 'Giao dịch thanh toán'

    def __str__(self):
        return self.ma_tham_chieu


class EmailPhieuThanhToan(models.Model):
    """One durable delivery record per invoice, separate from settlement state."""
    class TrangThai(models.TextChoices):
        CHO = 'pending', 'Chờ gửi vào Mailtrap Testing'
        DANG_GUI = 'sending', 'Đang gửi / chưa có kết quả cuối'
        DA_GUI = 'sent', 'Đã gửi vào Mailtrap Testing'
        LOI = 'failed', 'Gửi lỗi — có thể thử lại'
        BO_QUA = 'skipped', 'Bỏ qua — chưa có email hợp lệ'
        CHUA_RO = 'unknown', 'Chưa rõ kết quả — cần kiểm tra Mailtrap'

    hoa_don = models.OneToOneField(HoaDon, on_delete=models.PROTECT, related_name='email_phieu')
    trang_thai = models.CharField(max_length=10, choices=TrangThai.choices, default=TrangThai.CHO)
    email_nhan = models.EmailField(blank=True)
    ma_thu = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    so_lan_thu = models.PositiveIntegerField(default=0)
    ma_loi = models.CharField(max_length=40, blank=True)
    bat_dau_luc = models.DateTimeField(null=True, blank=True)
    gui_luc = models.DateTimeField(null=True, blank=True)
    nguoi_thu_lai = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    ngay_tao = models.DateTimeField(auto_now_add=True)
    ngay_cap_nhat = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Email phiếu thanh toán'
        verbose_name_plural = 'Email phiếu thanh toán'
        constraints = [
            models.CheckConstraint(condition=models.Q(trang_thai__in=['pending', 'sending', 'sent', 'failed', 'skipped', 'unknown']), name='receipt_email_valid_status'),
            models.CheckConstraint(condition=(models.Q(trang_thai='sent', gui_luc__isnull=False) | (~models.Q(trang_thai='sent') & models.Q(gui_luc__isnull=True))), name='receipt_email_sent_timestamp'),
        ]


class QuanLyChiNhanh(models.Model):
    """Legacy profile retained for migration and data recovery only.

    Active branch-scoped staff accounts use LeTanChiNhanh. This model no longer
    grants permissions or has an account-management screen.
    """
    user = models.OneToOneField(User, on_delete=models.PROTECT, related_name='quan_ly_chi_nhanh')
    chi_nhanh = models.ForeignKey('ChiNhanh', on_delete=models.PROTECT, related_name='quan_ly_chi_nhanhs')
    ngay_tao = models.DateTimeField(auto_now_add=True)
    ngay_cap_nhat = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Quản lý chi nhánh'
        verbose_name_plural = 'Quản lý chi nhánh'

    def __str__(self):
        return f'{self.user.username} · Quản lý {self.chi_nhanh.ten}'


class LeTanChiNhanh(models.Model):
    """Reception identity, isolated from branch cashier profiles."""
    user = models.OneToOneField(User, on_delete=models.PROTECT, related_name='le_tan_chi_nhanh')
    chi_nhanh = models.ForeignKey('ChiNhanh', on_delete=models.PROTECT, related_name='le_tans')
    ngay_tao = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Lễ tân chi nhánh'
        verbose_name_plural = 'Lễ tân chi nhánh'

    def __str__(self):
        return f'{self.user.username} · Lễ tân {self.chi_nhanh.ten}'


# ========================= 
# CHI NHÁNH
# ========================= 
class ChiNhanh(models.Model):
    ten = models.CharField(max_length=255, db_index=True, validators=[name_validator])
    dia_chi = models.TextField()
    so_dien_thoai = models.CharField(max_length=15, blank=True, default='', validators=[phone_validator])
    email = models.EmailField(max_length=254, blank=True, default='')
    phuong = models.CharField(max_length=100, db_index=True)
    vi_tri = models.PointField(srid=4326, spatial_index=True)
    dang_hoat_dong = models.BooleanField('Đang hoạt động', default=True, db_index=True)
    gio_mo = models.TimeField()
    gio_dong = models.TimeField()


    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(gio_mo__lt=models.F("gio_dong")),
                name="chinhanh_gio_mo_lt_gio_dong",
            ),
            models.CheckConstraint(
                condition=(
                    GreaterThanOrEqual(models.Func(models.F("vi_tri"), function="ST_X"), models.Value(-180))
                    & LessThanOrEqual(models.Func(models.F("vi_tri"), function="ST_X"), models.Value(180))
                    & GreaterThanOrEqual(models.Func(models.F("vi_tri"), function="ST_Y"), models.Value(-90))
                    & LessThanOrEqual(models.Func(models.F("vi_tri"), function="ST_Y"), models.Value(90))
                ),
                name="chinhanh_vi_tri_wgs84_range",
            ),
        ]
        verbose_name = 'Chi nhánh'
        verbose_name_plural = 'Chi nhánh'

    def clean(self):
        super().clean()
        if self.gio_mo and self.gio_dong and self.gio_mo >= self.gio_dong:
            raise ValidationError({"gio_dong": "Giờ đóng phải sau giờ mở."})
        if self.vi_tri:
            x = self.vi_tri.x
            y = self.vi_tri.y
            srid = self.vi_tri.srid

            if srid in (None, 0, 4326):
                if not (-180 <= x <= 180 and -90 <= y <= 90):
                    raise ValidationError({
                        "vi_tri": "Tọa độ không hợp lệ. Vĩ độ phải trong [-90, 90], kinh độ trong [-180, 180]."
                    })
                self.vi_tri = Point(x, y, srid=4326)
                return

            is_web_mercator = srid == 3857 or abs(x) > 180 or abs(y) > 90
            if is_web_mercator:
                max_merc = 20037508.34
                world_width = max_merc * 2
                if x > max_merc or x < -max_merc:
                    x = ((x + max_merc) % world_width) - max_merc
                if y > max_merc:
                    y = max_merc
                elif y < -max_merc:
                    y = -max_merc
                lon = x * 180.0 / 20037508.34
                lat = y * 180.0 / 20037508.34
                lat = 180.0 / math.pi * (2 * math.atan(math.exp(lat * math.pi / 180.0)) - math.pi / 2)
                if abs(lat) <= 90 and abs(lon) <= 180:
                    self.vi_tri = Point(lon, lat, srid=4326)
                    return

            raise ValidationError({
                "vi_tri": "Tọa độ không hợp lệ. Hệ thống yêu cầu tọa độ WGS84 (EPSG:4326)."
            })
    def __str__(self):
        return self.ten


# ========================= 
# ẢNH CHI NHÁNH
# ========================= 
def chi_nhanh_image_upload_to(instance, filename):
    ext = "jpg"
    if "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower() or "jpg"
    timestamp = timezone.now().strftime("%Y%m%d%H%M%S%f")
    return f"chi_nhanh/{instance.chi_nhanh_id}/{timestamp}.{ext}"


# Historical migrations serialize this callable path; keep it stable for migration replay.
benh_vien_image_upload_to = chi_nhanh_image_upload_to


class ChiNhanhHinhAnh(models.Model):
    chi_nhanh = models.ForeignKey(
        ChiNhanh,
        on_delete=models.CASCADE,
        related_name="hinh_anhs",
    )
    hinh_anh = models.ImageField(
        upload_to=chi_nhanh_image_upload_to,
        validators=[FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png", "webp"])],
    )
    mo_ta = models.CharField(max_length=255, blank=True, default="")
    thu_tu = models.PositiveIntegerField(default=0)
    la_anh_dai_dien = models.BooleanField(default=False, db_index=True)
    ngay_tao = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-la_anh_dai_dien", "thu_tu", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["chi_nhanh"],
                condition=models.Q(la_anh_dai_dien=True),
                name="chinhanh_single_cover_image",
            ),
        ]
        indexes = [
            models.Index(fields=["chi_nhanh", "thu_tu"]),
        ]
        verbose_name = "Hình ảnh chi nhánh"
        verbose_name_plural = "Hình ảnh chi nhánh"

    def __str__(self):
        return f"{self.chi_nhanh.ten} - ảnh #{self.pk}"


class GioLamViecChiNhanh(models.Model):
    chi_nhanh = models.ForeignKey(
        ChiNhanh,
        on_delete=models.CASCADE,
        related_name='gio_lam_viecs'
    )
    thu = models.IntegerField(
        choices=[
            (0, 'Chủ nhật'),
            (1, 'Thứ 2'),
            (2, 'Thứ 3'),
            (3, 'Thứ 4'),
            (4, 'Thứ 5'),
            (5, 'Thứ 6'),
            (6, 'Thứ 7'),
        ],
        db_index=True
    )
    gio_mo = models.TimeField()
    gio_dong = models.TimeField()
    nghi = models.BooleanField(default=False)

    class Meta:
        unique_together = [['chi_nhanh', 'thu']]
        ordering = ['chi_nhanh', 'thu']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(nghi=True) | models.Q(gio_mo__lt=models.F("gio_dong")),
                name="giolamviec_chinhanh_gio_mo_lt_gio_dong",
            ),
        ]
        verbose_name = 'Giờ làm việc chi nhánh'
        verbose_name_plural = 'Giờ làm việc chi nhánh'

    def clean(self):
        super().clean()
        if not self.nghi and self.gio_mo and self.gio_dong and self.gio_mo >= self.gio_dong:
            raise ValidationError({"gio_dong": "Giờ đóng phải sau giờ mở."})

    def __str__(self):
        return f"{self.chi_nhanh.ten} - {self.get_thu_display()}"


# ========================= 
# KHOA
# ========================= 
class Khoa(models.Model):
    ten = models.CharField(max_length=200, db_index=True, validators=[name_validator])
    chi_nhanh = models.ForeignKey(
        ChiNhanh,
        on_delete=models.PROTECT,
        related_name='khoas',
        db_index=True
    )

    class Meta:
        unique_together = [['ten', 'chi_nhanh']]
        verbose_name = 'Khoa'
        verbose_name_plural = 'Khoa'

    def __str__(self):
        return f"{self.ten} - {self.chi_nhanh.ten}"


# ========================= 
# BÁC SĨ
# ========================= 
class BacSi(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.PROTECT,
        related_name='bac_si',
        null=True,
        blank=True
    )
    ho_ten = models.CharField(max_length=255, db_index=True, validators=[name_validator])
    chuyen_khoa = models.CharField(max_length=200, db_index=True)
    khoa = models.ForeignKey(
        Khoa,
        on_delete=models.PROTECT,
        related_name='bac_sis'
    )
    chi_nhanh = models.ForeignKey(
        ChiNhanh,
        on_delete=models.PROTECT,
        related_name='bac_sis'
    )
    so_dien_thoai = models.CharField(max_length=15, validators=[phone_validator], unique=True)

    class Meta:
        indexes = [
            models.Index(fields=['chi_nhanh', 'chuyen_khoa']),
        ]
        permissions = [
            ('view_cross_branch_medical_history', 'Can view cross-branch medical history with a documented reason'),
        ]
        constraints = [
            models.UniqueConstraint(fields=["so_dien_thoai"], name="bacsi_so_dien_thoai_unique"),
        ]
        verbose_name = 'Bác sĩ'
        verbose_name_plural = 'Bác sĩ'

    def clean(self):
        super().clean()
        if self.pk:
            previous_clinic_id = type(self).objects.filter(pk=self.pk).values_list('chi_nhanh_id', flat=True).first()
            if previous_clinic_id and previous_clinic_id != self.chi_nhanh_id and LichKham.objects.filter(bac_si_id=self.pk).exists():
                raise ValidationError({
                    "chi_nhanh": "Không thể chuyển bác sĩ sang chi nhánh khác vì đã có lịch khám/hóa đơn lịch sử.",
                })
        if self.khoa_id and self.chi_nhanh_id:
            if self.khoa.chi_nhanh_id != self.chi_nhanh_id:
                raise ValidationError({
                    "khoa": "Chuyên khoa không thuộc chi nhánh đã chọn.",
                    "chi_nhanh": "Chi nhánh không khớp với chuyên khoa."
                })

    def save(self, *args, **kwargs):
        if self.pk:
            previous_clinic_id = type(self).objects.filter(pk=self.pk).values_list('chi_nhanh_id', flat=True).first()
            if previous_clinic_id and previous_clinic_id != self.chi_nhanh_id and LichKham.objects.filter(bac_si_id=self.pk).exists():
                raise ValidationError({
                    "chi_nhanh": "Không thể chuyển bác sĩ sang chi nhánh khác vì đã có lịch khám/hóa đơn lịch sử.",
                })
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.ho_ten


class BacSiDichVu(models.Model):
    """Explicit eligibility of a doctor for a service at their own branch."""

    bac_si = models.ForeignKey(BacSi, on_delete=models.PROTECT, related_name='dich_vu_duoc_phep')
    dich_vu_chi_nhanh = models.ForeignKey(
        DichVuChiNhanh,
        on_delete=models.PROTECT,
        related_name='bac_si_duoc_phep',
    )
    dang_ap_dung = models.BooleanField('Đang được phân công', default=True, db_index=True)
    ngay_tao = models.DateTimeField(auto_now_add=True)
    ngay_cap_nhat = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['bac_si__ho_ten', 'dich_vu_chi_nhanh__dich_vu__ma_dich_vu']
        verbose_name = 'Dịch vụ bác sĩ được thực hiện'
        verbose_name_plural = 'Dịch vụ bác sĩ được thực hiện'
        constraints = [
            models.UniqueConstraint(fields=['bac_si', 'dich_vu_chi_nhanh'], name='doctor_clinic_service_unique'),
        ]

    def clean(self):
        super().clean()
        if self.bac_si_id and self.dich_vu_chi_nhanh_id:
            doctor = BacSi.objects.filter(pk=self.bac_si_id).select_related('chi_nhanh').first()
            branch_service = DichVuChiNhanh.objects.filter(
                pk=self.dich_vu_chi_nhanh_id,
            ).select_related('chi_nhanh', 'dich_vu').first()
            if not doctor or not branch_service:
                return
            if doctor.chi_nhanh_id != branch_service.chi_nhanh_id:
                raise ValidationError({
                    'dich_vu_chi_nhanh': 'Dịch vụ phải thuộc chi nhánh nơi bác sĩ đang làm việc.',
                })
            previous = type(self).objects.filter(pk=self.pk).first() if self.pk else None
            assignment_changed = previous is None or (
                previous.bac_si_id != self.bac_si_id
                or previous.dich_vu_chi_nhanh_id != self.dich_vu_chi_nhanh_id
            )
            reactivated = previous is not None and not previous.dang_ap_dung and self.dang_ap_dung
            if self.dang_ap_dung and (assignment_changed or reactivated):
                if not doctor.chi_nhanh.dang_hoat_dong:
                    raise ValidationError({'bac_si': 'Không thể phân công tại chi nhánh đã ngừng hoạt động.'})
                if not branch_service.dang_ap_dung:
                    raise ValidationError({'dich_vu_chi_nhanh': 'Chi nhánh đã ngừng cung cấp dịch vụ này.'})
                if not branch_service.dich_vu.dang_ap_dung:
                    raise ValidationError({'dich_vu_chi_nhanh': 'Dịch vụ đã ngừng áp dụng trong danh mục chung.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.bac_si.ho_ten} · {self.dich_vu_chi_nhanh.dich_vu.ma_dich_vu}'


@receiver(pre_delete, sender=BacSiDichVu)
def preserve_doctor_service_assignment(sender, instance, **kwargs):
    raise ProtectedError('Không xóa phân công dịch vụ của bác sĩ; hãy ngừng phân công để giữ lịch sử.', [instance])


# ========================= 
# GIỜ LÀM VIỆC BÁC SĨ
# ========================= 
class GioLamViecBacSi(models.Model):
    bac_si = models.ForeignKey(
        BacSi,
        on_delete=models.CASCADE,
        related_name='gio_lam_viecs'
    )
    thu = models.IntegerField(
        choices=[
            (0, 'Chủ nhật'),
            (1, 'Thứ 2'),
            (2, 'Thứ 3'),
            (3, 'Thứ 4'),
            (4, 'Thứ 5'),
            (5, 'Thứ 6'),
            (6, 'Thứ 7'),
        ],
        db_index=True
    )
    gio_bat_dau = models.TimeField()
    gio_ket_thuc = models.TimeField()
    nghi = models.BooleanField(default=False)

    class Meta:
        unique_together = [['bac_si', 'thu']]
        ordering = ['bac_si', 'thu']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(nghi=True) | models.Q(gio_bat_dau__lt=models.F("gio_ket_thuc")),
                name="giolamviec_bacsi_gio_bat_dau_lt_gio_ket_thuc",
            ),
        ]
        verbose_name = 'Giờ làm việc bác sĩ'
        verbose_name_plural = 'Giờ làm việc bác sĩ'

    def clean(self):
        super().clean()
        if not self.nghi and self.gio_bat_dau and self.gio_ket_thuc and self.gio_bat_dau >= self.gio_ket_thuc:
            raise ValidationError({"gio_ket_thuc": "Giờ kết thúc phải sau giờ bắt đầu."})

    def __str__(self):
        return f"{self.bac_si.ho_ten} - {self.get_thu_display()}"


class BacSiNghiDotXuat(models.Model):
    """A dated doctor absence; affected appointments are handled explicitly by reception."""
    bac_si = models.ForeignKey(BacSi, on_delete=models.PROTECT, related_name='dot_nghi')
    bat_dau = models.DateTimeField(db_index=True)
    ket_thuc = models.DateTimeField(db_index=True)
    ly_do = models.CharField(max_length=500)
    dang_ap_dung = models.BooleanField(default=True, db_index=True)
    nguoi_tao = models.ForeignKey(User, on_delete=models.PROTECT, related_name='dot_nghi_bac_si_da_tao')
    ngay_tao = models.DateTimeField(auto_now_add=True)
    ngay_cap_nhat = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['bat_dau', 'bac_si_id']
        indexes = [models.Index(fields=['bac_si', 'dang_ap_dung', 'bat_dau', 'ket_thuc'])]
        verbose_name = 'Lịch nghỉ đột xuất bác sĩ'
        verbose_name_plural = 'Lịch nghỉ đột xuất bác sĩ'

    def clean(self):
        super().clean()
        if self.bat_dau and self.ket_thuc and self.bat_dau >= self.ket_thuc:
            raise ValidationError({'ket_thuc': 'Thời điểm kết thúc phải sau thời điểm bắt đầu.'})
        if self.bat_dau and self.ket_thuc and self.bac_si_id:
            overlaps = type(self).objects.filter(
                bac_si_id=self.bac_si_id, dang_ap_dung=True,
                bat_dau__lt=self.ket_thuc, ket_thuc__gt=self.bat_dau,
            )
            if self.pk:
                overlaps = overlaps.exclude(pk=self.pk)
            if overlaps.exists():
                raise ValidationError('Khoảng nghỉ này chồng lấn một lịch nghỉ đang áp dụng của bác sĩ.')

    def __str__(self):
        return f'{self.bac_si} — {self.bat_dau:%d/%m/%Y %H:%M} đến {self.ket_thuc:%d/%m/%Y %H:%M}'


# =========================
# BỆNH NHÂN
# =========================
class BenhNhan(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='benh_nhan'
    )
    ho_ten = models.CharField(max_length=255, db_index=True, validators=[name_validator])
    ngay_sinh = models.DateField()
    gioi_tinh = models.CharField(
        max_length=10,
        choices=[
            ('nam', 'Nam'),
            ('nu', 'Nữ'),
            ('khac', 'Khác')
        ]
    )
    so_dien_thoai = models.CharField(
        max_length=15,
        db_index=True,
        validators=[phone_validator],
        unique=True,
    )
    dia_chi = models.TextField()


    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["so_dien_thoai"], name="benhnhan_so_dien_thoai_unique"),
        ]
        verbose_name = 'Bệnh nhân'
        verbose_name_plural = 'Bệnh nhân'

    def clean(self):
        super().clean()
        if self.ngay_sinh and self.ngay_sinh > timezone.localdate():
            raise ValidationError({"ngay_sinh": "Ngày sinh không hợp lệ."})

    def __str__(self):
        return self.ho_ten
    
# ========================= 
# LỊCH KHÁM
# ========================= 
class LichKham(models.Model):
    benh_nhan = models.ForeignKey(
        BenhNhan,
        on_delete=models.PROTECT,
        related_name='lich_khams'
    )
    bac_si = models.ForeignKey(
        BacSi,
        on_delete=models.PROTECT,
        related_name='lich_khams'
    )
    dich_vu_dat_truoc = models.ForeignKey(
        DanhMucDichVu,
        on_delete=models.PROTECT,
        related_name='lich_dat_truoc',
        null=True,
        blank=True,
        verbose_name='Dịch vụ đặt trước',
    )
    dich_vu_dat_truoc_nhieu = models.ManyToManyField(
        DanhMucDichVu,
        through='DichVuDatTruocLichKham',
        related_name='lich_dat_truoc_nhieu',
        blank=True,
        verbose_name='Các dịch vụ đặt trước',
    )
    ngay_kham = models.DateField(db_index=True)
    gio_kham = models.TimeField()
    thoi_luong_du_kien = models.PositiveSmallIntegerField(
        'Thời lượng dự kiến đã chốt (phút)', default=30,
        validators=[MinValueValidator(15), MaxValueValidator(480)],
        help_text='Snapshot thời lượng tại thời điểm đặt lịch; lịch cũ dùng mặc định 30 phút.',
    )
    trang_thai = models.CharField(
        max_length=20,
        choices=[
            ('cho', 'Chờ khám'),
            ('dang', 'Đang khám'),
            ('xong', 'Đã xong'),
            ('huy', 'Đã hủy')
        ],
        default='cho',
        db_index=True
    )
    trang_thai_tiep_nhan = models.CharField(
        max_length=20,
        choices=[('cho_xac_nhan', 'Đang chờ'), ('da_xac_nhan', 'Đã xác nhận'),
                 ('da_tiep_nhan', 'Đã tiếp nhận'), ('vang_mat', 'Vắng mặt')],
        default='cho_xac_nhan', db_index=True,
    )
    ghi_chu = models.TextField(blank=True, default='')
    ngay_tao = models.DateTimeField(auto_now_add=True)
    ngay_cap_nhat = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = [
            ('kham_som', 'Cho phép khám trước thời điểm hẹn'),
            ('kham_hoi_to', 'Cho phép ghi nhận khám của ngày trước'),
        ]
        indexes = [
            models.Index(fields=['bac_si', 'ngay_kham', 'trang_thai']),
            models.Index(fields=['benh_nhan', 'ngay_kham']),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(thoi_luong_du_kien__gte=15, thoi_luong_du_kien__lte=480), name='lichkham_duration_range'),
            models.UniqueConstraint(
                fields=["benh_nhan", "ngay_kham", "gio_kham"],
                condition=~models.Q(trang_thai="huy"),
                name="lichkham_benhnhan_ngay_gio_active_unique",
            ),
            models.UniqueConstraint(
                fields=["bac_si", "ngay_kham", "gio_kham"],
                condition=~models.Q(trang_thai="huy"),
                name="lichkham_bacsi_ngay_gio_active_unique",
            ),
        ]
        ordering = ['-ngay_kham', '-gio_kham']
        verbose_name = 'Lịch khám'
        verbose_name_plural = 'Lịch khám'

    def clean(self):
        super().clean()
        previous = type(self).objects.filter(pk=self.pk).first() if self.pk else None
        changed_slot = previous is None or (previous.ngay_kham, previous.gio_kham) != (self.ngay_kham, self.gio_kham)
        if changed_slot and self.ngay_kham and self.gio_kham:
            scheduled = timezone.make_aware(datetime.datetime.combine(self.ngay_kham, self.gio_kham),
                                            timezone.get_current_timezone())
            if scheduled <= timezone.now():
                raise ValidationError({'gio_kham': 'Không thể đặt lịch vào ngày/giờ đã qua.'})
            if scheduled < timezone.now() + datetime.timedelta(minutes=60):
                raise ValidationError({'gio_kham': 'Vui lòng đặt lịch trước giờ khám ít nhất 60 phút.'})

        if self.gio_kham and (self.gio_kham.minute % 30 != 0 or self.gio_kham.second or self.gio_kham.microsecond):
            raise ValidationError({"gio_kham": "Giờ khám chỉ nhận các mốc 30 phút (00 hoặc 30)."})

        if self.benh_nhan_id and self.ngay_kham:
            qs = LichKham.objects.filter(
                benh_nhan=self.benh_nhan,
                ngay_kham=self.ngay_kham
            ).exclude(trang_thai="huy")
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError({"ngay_kham": "Bệnh nhân đã có lịch trong ngày này."})

        if self.bac_si_id and self.ngay_kham and self.gio_kham:
            doctor_changed = previous is None or previous.bac_si_id != self.bac_si_id
            if doctor_changed and not self.bac_si.chi_nhanh.dang_hoat_dong:
                raise ValidationError({'bac_si': 'Chi nhánh đã ngừng hoạt động, không nhận lịch mới.'})
            if doctor_changed and self.bac_si.user_id and not self.bac_si.user.is_active:
                raise ValidationError({'bac_si': 'Tài khoản bác sĩ hiện không hoạt động.'})
            service_changed = (
                doctor_changed
                or previous.dich_vu_dat_truoc_id != self.dich_vu_dat_truoc_id
            )
            if self.dich_vu_dat_truoc_id and service_changed:
                branch_service = DichVuChiNhanh.objects.filter(
                    chi_nhanh_id=self.bac_si.chi_nhanh_id,
                    dich_vu_id=self.dich_vu_dat_truoc_id,
                    dang_ap_dung=True,
                    dich_vu__dang_ap_dung=True,
                    dich_vu__cho_phep_dat_lich=True,
                ).first()
                if not branch_service:
                    raise ValidationError({
                        'dich_vu_dat_truoc': 'Dịch vụ không được cung cấp tại chi nhánh đã chọn hoặc đã ngừng áp dụng.',
                    })
                if not BacSiDichVu.objects.filter(
                    bac_si_id=self.bac_si_id,
                    dich_vu_chi_nhanh=branch_service,
                    dang_ap_dung=True,
                ).exists():
                    raise ValidationError({
                        'dich_vu_dat_truoc': 'Bác sĩ chưa được phân công thực hiện dịch vụ tại chi nhánh này.',
                    })

            thu = (self.ngay_kham.weekday() + 1) % 7
            gio_lv = GioLamViecBacSi.objects.filter(bac_si=self.bac_si, thu=thu).first()
            if gio_lv:
                if gio_lv.nghi:
                    raise ValidationError({"gio_kham": "Bác sĩ nghỉ vào ngày đã chọn."})
                if not (gio_lv.gio_bat_dau <= self.gio_kham <= gio_lv.gio_ket_thuc):
                    raise ValidationError({"gio_kham": "Giờ khám ngoài khung giờ làm việc của bác sĩ."})
            else:
                raise ValidationError({"gio_kham": "Bác sĩ chưa có lịch làm việc cho ngày này."})

            chi_nhanh = self.bac_si.chi_nhanh
            gio_lv_bv = GioLamViecChiNhanh.objects.filter(chi_nhanh=chi_nhanh, thu=thu).first()
            if gio_lv_bv:
                if gio_lv_bv.nghi:
                    raise ValidationError({"gio_kham": "Chi nhánh nghỉ vào ngày đã chọn."})
                branch_open = gio_lv_bv.gio_mo
                branch_close_time = gio_lv_bv.gio_dong
            else:
                branch_open = chi_nhanh.gio_mo
                branch_close_time = chi_nhanh.gio_dong
            if not (branch_open <= self.gio_kham <= branch_close_time):
                raise ValidationError({"gio_kham": "Giờ khám ngoài giờ làm việc của chi nhánh."})

            duration = self.thoi_luong_du_kien or 30
            start_minute = self.gio_kham.hour * 60 + self.gio_kham.minute
            doctor_close = gio_lv.gio_ket_thuc.hour * 60 + gio_lv.gio_ket_thuc.minute
            branch_close = branch_close_time.hour * 60 + branch_close_time.minute
            if start_minute + duration > doctor_close:
                raise ValidationError({'gio_kham': 'Thời lượng dịch vụ vượt quá giờ làm việc của bác sĩ.'})
            if start_minute + duration > branch_close:
                raise ValidationError({'gio_kham': 'Thời lượng dịch vụ vượt quá giờ hoạt động của chi nhánh.'})

            existing = LichKham.objects.filter(
                bac_si=self.bac_si,
                ngay_kham=self.ngay_kham,
            ).exclude(trang_thai="huy")
            if self.pk:
                existing = existing.exclude(pk=self.pk)
            if existing.exists():
                current_dt = datetime.datetime.combine(self.ngay_kham, self.gio_kham)
                current_end = current_dt + datetime.timedelta(minutes=self.thoi_luong_du_kien or 30)
                for other in existing:
                    other_dt = datetime.datetime.combine(other.ngay_kham, other.gio_kham)
                    other_end = other_dt + datetime.timedelta(minutes=other.thoi_luong_du_kien or 30)
                    if current_dt < other_end and current_end > other_dt:
                        raise ValidationError({
                            "gio_kham": "Khoảng thời gian khám bị chồng lấn với lịch khác của bác sĩ."
                        })

    def __str__(self):
        return f"{self.benh_nhan.ho_ten} - {self.ngay_kham}"


# =========================
# DỊCH VỤ ĐẶT TRƯỚC CỦA LỊCH KHÁM
# =========================
class DichVuDatTruocLichKham(models.Model):
    lich_kham = models.ForeignKey(LichKham, on_delete=models.CASCADE, related_name='dich_vu_dat_truoc_rows')
    dich_vu = models.ForeignKey(DanhMucDichVu, on_delete=models.PROTECT, related_name='lich_dat_truoc_rows')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['lich_kham', 'dich_vu'], name='lich_dv_dat_truoc_unique')]
        verbose_name = 'Dịch vụ đặt trước trong lịch'
        verbose_name_plural = 'Dịch vụ đặt trước trong lịch'


# =========================
# PHIẾU KHÁM
# ========================= 
class PhieuKham(models.Model):
    benh_nhan = models.ForeignKey(
        BenhNhan,
        on_delete=models.PROTECT,
        related_name='phieu_khams'
    )
    lich_kham = models.OneToOneField(
        LichKham,
        on_delete=models.PROTECT,
        related_name='phieu_kham'
    )
    trieu_chung = models.TextField()
    chan_doan = models.TextField()
    huong_dieu_tri = models.TextField()
    ngay_lap = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-ngay_lap']
        verbose_name = 'Phiếu khám'
        verbose_name_plural = 'Phiếu khám'

    def clean(self):
        super().clean()
        if self.lich_kham_id and self.benh_nhan_id:
            if self.lich_kham.benh_nhan_id != self.benh_nhan_id:
                raise ValidationError({"benh_nhan": "Bệnh nhân không khớp với lịch khám."})

    def __str__(self):
        return f"Phiếu {self.benh_nhan.ho_ten} - {self.ngay_lap.date()}"

    def save(self, *args, **kwargs):
        # The canonical record, history projection and audit must commit together.
        with transaction.atomic():
            if self.pk:
                previous = type(self).objects.select_for_update().get(pk=self.pk)
                self._audit_before = {key: getattr(previous, key) for key in
                                      ('trieu_chung', 'chan_doan', 'huong_dieu_tri')}
            else:
                self._audit_before = None
            return super().save(*args, **kwargs)


class ChiTietKhamMat(models.Model):
    """Optional ophthalmology findings, separated by eye and kept off legacy exams."""
    phieu_kham = models.OneToOneField(PhieuKham, on_delete=models.PROTECT, related_name='chi_tiet_mat')
    thi_luc_phai_khong_kinh = models.CharField(max_length=32, blank=True, default='', validators=[visual_acuity_validator])
    thi_luc_phai_co_kinh = models.CharField(max_length=32, blank=True, default='', validators=[visual_acuity_validator])
    thi_luc_trai_khong_kinh = models.CharField(max_length=32, blank=True, default='', validators=[visual_acuity_validator])
    thi_luc_trai_co_kinh = models.CharField(max_length=32, blank=True, default='', validators=[visual_acuity_validator])
    nhan_ap_phai = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))])
    nhan_ap_trai = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))])
    sph_phai = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('-30')), MaxValueValidator(Decimal('30'))])
    sph_trai = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('-30')), MaxValueValidator(Decimal('30'))])
    cyl_phai = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('-20')), MaxValueValidator(Decimal('20'))])
    cyl_trai = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('-20')), MaxValueValidator(Decimal('20'))])
    axis_phai = models.PositiveSmallIntegerField(null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(180)])
    axis_trai = models.PositiveSmallIntegerField(null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(180)])
    ban_phan_truoc = models.TextField(blank=True, default='')
    day_mat = models.TextField(blank=True, default='')
    ghi_nhan_khac = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = 'Chi tiết khám mắt'
        verbose_name_plural = 'Chi tiết khám mắt'

    def clean(self):
        super().clean()
        for eye, cyl_field, axis_field in (
            ('phải', 'cyl_phai', 'axis_phai'), ('trái', 'cyl_trai', 'axis_trai'),
        ):
            if getattr(self, cyl_field) is None and getattr(self, axis_field) is not None:
                raise ValidationError({axis_field: f'Cần có CYL mắt {eye} khi ghi AXIS.'})


class YeuCauChinhSuaPhieuKham(models.Model):
    class TrangThai(models.TextChoices):
        CHO_DUYET = 'pending', 'Chờ duyệt'
        DA_DUYET = 'approved', 'Đã duyệt'
        TU_CHOI = 'rejected', 'Từ chối'

    phieu_kham = models.ForeignKey(PhieuKham, on_delete=models.PROTECT, related_name='yeu_cau_chinh_suas')
    nguoi_de_nghi = models.ForeignKey(User, on_delete=models.PROTECT, related_name='yeu_cau_sua_phieu_kham')
    ly_do = models.TextField(max_length=1000)
    du_lieu_truoc = models.JSONField()
    du_lieu_de_xuat = models.JSONField()
    trang_thai = models.CharField(max_length=10, choices=TrangThai.choices, default=TrangThai.CHO_DUYET, db_index=True)
    nguoi_duyet = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name='duyet_sua_phieu_kham')
    ghi_chu_duyet = models.CharField(max_length=1000, blank=True, default='')
    du_lieu_sau = models.JSONField(null=True, blank=True)
    ngay_tao = models.DateTimeField(auto_now_add=True, db_index=True)
    ngay_xu_ly = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-ngay_tao']
        constraints = [models.UniqueConstraint(
            fields=['phieu_kham'], condition=models.Q(trang_thai='pending'),
            name='one_pending_exam_correction',
        )]
        indexes = [models.Index(fields=['trang_thai', '-ngay_tao'])]
        verbose_name = 'Yêu cầu chỉnh sửa phiếu khám'
        verbose_name_plural = 'Yêu cầu chỉnh sửa phiếu khám'

    def __str__(self):
        return f'Yêu cầu #{self.pk} · phiếu khám #{self.phieu_kham_id}'


class DanhGiaLuotKham(models.Model):
    lich_kham = models.OneToOneField(LichKham, on_delete=models.PROTECT, related_name='danh_gia')
    nguoi_danh_gia = models.ForeignKey(User, on_delete=models.PROTECT, related_name='danh_gia_luot_khams')
    diem = models.PositiveSmallIntegerField()
    nhan_xet = models.TextField(blank=True, default='', max_length=1000)
    dang_hien_thi = models.BooleanField(default=True, db_index=True)
    ghi_chu_quan_tri = models.CharField(max_length=1000, blank=True, default='')
    quan_tri_vien = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='danh_gia_da_quan_ly')
    ngay_tao = models.DateTimeField(auto_now_add=True, db_index=True)
    ngay_quan_tri = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-ngay_tao']
        constraints = [models.CheckConstraint(condition=models.Q(diem__gte=1, diem__lte=5), name='clinic_rating_score_1_5')]
        indexes = [models.Index(fields=['dang_hien_thi', '-ngay_tao'])]
        verbose_name = 'Đánh giá lượt khám'
        verbose_name_plural = 'Đánh giá lượt khám'

    def clean(self):
        super().clean()
        if self.lich_kham_id and self.lich_kham.trang_thai != 'xong':
            raise ValidationError({'lich_kham': 'Chỉ được đánh giá lượt khám đã hoàn tất.'})

    def __str__(self):
        return f'{self.get_diem_display() if hasattr(self, "get_diem_display") else self.diem}/5 · lịch #{self.lich_kham_id}'


class DonThuoc(models.Model):
    phieu_kham = models.ForeignKey(PhieuKham, on_delete=models.PROTECT, related_name='don_thuocs')
    ten_thuoc = models.CharField(max_length=255)
    lieu_dung = models.CharField(max_length=255)
    huong_dan = models.TextField()
    thoi_gian_dung = models.CharField(max_length=120, blank=True, default='')
    ghi_chu = models.TextField(blank=True, default='')
    ngay_tao = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
        verbose_name = 'Dòng đơn thuốc'
        verbose_name_plural = 'Đơn thuốc'


class DonKinh(models.Model):
    phieu_kham = models.OneToOneField(PhieuKham, on_delete=models.PROTECT, related_name='don_kinh')
    sph_phai = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('-30')), MaxValueValidator(Decimal('30'))])
    cyl_phai = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('-20')), MaxValueValidator(Decimal('20'))])
    axis_phai = models.PositiveSmallIntegerField(null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(180)])
    sph_trai = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('-30')), MaxValueValidator(Decimal('30'))])
    cyl_trai = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('-20')), MaxValueValidator(Decimal('20'))])
    axis_trai = models.PositiveSmallIntegerField(null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(180)])
    ghi_chu = models.TextField(blank=True, default='')
    bac_si_xac_nhan = models.BooleanField(default=False)
    ngay_tao = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()
        has_prescription = any(getattr(self, field) is not None for field in (
            'sph_phai', 'cyl_phai', 'axis_phai', 'sph_trai', 'cyl_trai', 'axis_trai'
        )) or bool(self.ghi_chu)
        if self.bac_si_xac_nhan and not has_prescription:
            raise ValidationError({'bac_si_xac_nhan': 'Nhập ít nhất một thông số trước khi xác nhận đơn kính.'})
        if has_prescription and not self.bac_si_xac_nhan:
            raise ValidationError({'bac_si_xac_nhan': 'Bác sĩ cần xác nhận đơn kính trước khi lưu.'})
        for eye, cyl_field, axis_field in (
            ('phải', 'cyl_phai', 'axis_phai'), ('trái', 'cyl_trai', 'axis_trai'),
        ):
            if getattr(self, cyl_field) is None and getattr(self, axis_field) is not None:
                raise ValidationError({axis_field: f'Cần có CYL mắt {eye} khi ghi AXIS.'})
        for eye, cyl_field, axis_field in (
            ('phải', 'cyl_phai', 'axis_phai'), ('trái', 'cyl_trai', 'axis_trai'),
        ):
            if getattr(self, cyl_field) is None and getattr(self, axis_field) is not None:
                raise ValidationError({axis_field: f'Cần có CYL mắt {eye} khi ghi AXIS.'})


# ========================= 
# DỊCH VỤ THỰC HIỆN TRONG PHIẾU KHÁM
# ========================= 
class DichVuThucHien(models.Model):
    phieu_kham = models.ForeignKey(PhieuKham, on_delete=models.PROTECT, related_name='dich_vu_thuc_hien')
    dich_vu = models.ForeignKey(DanhMucDichVu, on_delete=models.PROTECT, related_name='lan_thuc_hien')
    so_luong = models.PositiveIntegerField('Số lượng', default=1, validators=[MinValueValidator(1)])
    ghi_chu = models.TextField('Ghi chú', blank=True, default='', max_length=2000)
    nguoi_thuc_hien = models.ForeignKey(BacSi, on_delete=models.PROTECT, related_name='dich_vu_da_thuc_hien', verbose_name='Bác sĩ thực hiện')
    nguoi_ghi_nhan = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+', verbose_name='Người ghi nhận tại quầy')
    thoi_gian_tao = models.DateTimeField(auto_now_add=True)
    thoi_gian_cap_nhat = models.DateTimeField(auto_now=True)
    da_huy = models.BooleanField(default=False)
    la_dich_vu_dat_truoc = models.BooleanField(
        'Dịch vụ đặt trước đã được bác sĩ xác nhận thực hiện', default=False,
        help_text='Đánh dấu khi dịch vụ đặt trong lịch hẹn thực sự đã được cung cấp.',
    )
    da_xac_nhan_bac_si = models.BooleanField(
        'Đã được bác sĩ phụ trách xác nhận', default=False,
    )

    class Meta:
        ordering = ['id']
        verbose_name = 'Dịch vụ thực hiện'
        verbose_name_plural = 'Dịch vụ thực hiện'
        constraints = [
            models.UniqueConstraint(fields=['phieu_kham', 'dich_vu'], condition=models.Q(da_huy=False), name='dvth_active_service_unique'),
            models.CheckConstraint(condition=models.Q(so_luong__gte=1), name='dvth_so_luong_positive'),
        ]

    def clean(self):
        super().clean()
        if self.la_dich_vu_dat_truoc and self.phieu_kham_id and self.dich_vu_id:
            appointment = self.phieu_kham.lich_kham
            booked_ids = set(appointment.dich_vu_dat_truoc_nhieu.values_list('pk', flat=True))
            if appointment.dich_vu_dat_truoc_id:
                booked_ids.add(appointment.dich_vu_dat_truoc_id)
            if self.dich_vu_id not in booked_ids:
                raise ValidationError({'la_dich_vu_dat_truoc': 'Chỉ dịch vụ đã đặt trong lịch này mới có thể được đánh dấu là dịch vụ đặt trước.'})

    def __str__(self):
        return f'Phiếu #{self.phieu_kham_id} / {self.dich_vu.ma_dich_vu} / {self.so_luong}'


# =========================
# HO TRO ANH PHIEU KHAM
# =========================
def phieu_kham_image_upload_to(instance, filename):
    ext = "jpg"
    if "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower() or "jpg"
    timestamp = timezone.now().strftime("%Y%m%d%H%M%S%f")
    return f"phieu_kham/{instance.phieu_kham_id}/{timestamp}.{ext}"


class PhieuKhamHinhAnh(models.Model):
    phieu_kham = models.ForeignKey(
        PhieuKham,
        on_delete=models.CASCADE,
        related_name='hinh_anhs',
    )
    hinh_anh = models.ImageField(
        upload_to=phieu_kham_image_upload_to,
        storage=private_exam_storage,
        validators=[FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png", "webp"]), validate_exam_image],
    )
    mo_ta = models.CharField(max_length=255, blank=True, default='')
    thu_tu = models.PositiveIntegerField(default=0)
    ngay_tao = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['thu_tu', 'id']
        indexes = [
            models.Index(fields=['phieu_kham', 'thu_tu']),
        ]
        verbose_name = 'Hình ảnh phiếu khám'
        verbose_name_plural = 'Hình ảnh phiếu khám'

    def __str__(self):
        return f"Phiếu {self.phieu_kham_id} - ảnh #{self.pk}"

    def save(self, *args, **kwargs):
        # Covers the single-image generic CRUD path as well as concurrent uploads.
        with transaction.atomic():
            PhieuKham.objects.select_for_update().get(pk=self.phieu_kham_id)
            if type(self).objects.filter(phieu_kham_id=self.phieu_kham_id).exclude(pk=self.pk).count() >= MAX_IMAGE_COUNT:
                raise ValidationError('Mỗi phiếu khám tối đa 10 ảnh.')
            if not self.hinh_anh._committed:
                validate_exam_image(self.hinh_anh)
            return super().save(*args, **kwargs)


# ========================= 
# LICH SU KHAM BENH
# ========================= 
class LichSuKhamBenh(models.Model):
    benh_nhan = models.ForeignKey(
        BenhNhan,
        on_delete=models.PROTECT,
        related_name='lich_su_khams',
        db_index=True
    )
    bac_si = models.ForeignKey(
        BacSi,
        on_delete=models.SET_NULL,
        null=True,
        related_name='lich_su_khams'
    )
    chi_nhanh = models.ForeignKey(
        ChiNhanh,
        on_delete=models.SET_NULL,
        null=True,
        related_name='lich_su_khams'
    )
    phieu_kham = models.ForeignKey(
        PhieuKham,
        on_delete=models.PROTECT,
        related_name='lich_sus',
        null=True,
        blank=True
    )
    ngay_kham = models.DateField(db_index=True)
    trieu_chung = models.TextField()
    chan_doan = models.TextField()
    huong_dieu_tri = models.TextField()
    ghi_chu = models.TextField(blank=True, default='')
    ngay_tao = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['benh_nhan', '-ngay_kham']),
            models.Index(fields=['bac_si', '-ngay_kham']),
            models.Index(fields=['chi_nhanh', '-ngay_kham']),
        ]
        ordering = ['-ngay_kham', '-ngay_tao']
        verbose_name = 'Lịch sử khám bệnh'
        verbose_name_plural = 'Lịch sử khám bệnh'

    def clean(self):
        super().clean()
        if self.phieu_kham_id:
            if self.phieu_kham.benh_nhan_id != self.benh_nhan_id:
                raise ValidationError({"benh_nhan": "Bệnh nhân không khớp với phiếu khám."})
            if self.bac_si_id and self.phieu_kham.lich_kham.bac_si_id != self.bac_si_id:
                raise ValidationError({"bac_si": "Bác sĩ không khớp với phiếu khám."})
            if self.chi_nhanh_id and self.phieu_kham.lich_kham.bac_si.chi_nhanh_id != self.chi_nhanh_id:
                raise ValidationError({"chi_nhanh": "Chi nhánh không khớp với phiếu khám."})

    def __str__(self):
        return f"{self.benh_nhan.ho_ten} - {self.ngay_kham}"


# ========================= 
# LOG LỊCH KHÁM
# ========================= 
class LogLichKham(models.Model):
    lich_kham = models.ForeignKey(
        LichKham,
        on_delete=models.PROTECT,
        related_name='logs'
    )
    hanh_dong = models.CharField(
        max_length=50,
        choices=[
            ('tao_moi', 'Tạo mới'),
            ('cap_nhat', 'Cập nhật'),
            ('doi_trang_thai', 'Đổi trạng thái'),
            ('huy', 'Hủy lịch'),
            ('hoan_thanh', 'Hoàn thành'),
        ],
        db_index=True
    )
    trang_thai_cu = models.CharField(max_length=20, blank=True, default='')
    trang_thai_moi = models.CharField(max_length=20, blank=True, default='')
    nguoi_thuc_hien = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='log_lich_khams'
    )
    mo_ta = models.TextField(blank=True, default='')
    thoi_gian = models.DateTimeField(auto_now_add=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['lich_kham', '-thoi_gian']),
            models.Index(fields=['nguoi_thuc_hien', '-thoi_gian']),
            models.Index(fields=['hanh_dong', '-thoi_gian']),
        ]
        ordering = ['-thoi_gian']
        verbose_name = 'Log lịch khám'
        verbose_name_plural = 'Log lịch khám'

    def __str__(self):
        return f"{self.get_hanh_dong_display()} - {self.thoi_gian}"


# ========================= 
# LOG HỆ THỐNG
# ========================= 
class LogHeThong(models.Model):
    model_name = models.CharField(max_length=100, db_index=True)
    object_id = models.IntegerField(db_index=True)
    hanh_dong = models.CharField(
        max_length=50,
        choices=[
            ('create', 'Tạo mới'),
            ('update', 'Cập nhật'),
            ('delete', 'Xóa'),
        ],
        db_index=True
    )
    du_lieu_cu = models.JSONField(null=True, blank=True)
    du_lieu_moi = models.JSONField(null=True, blank=True)
    nguoi_thuc_hien = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='log_he_thongs'
    )
    thoi_gian = models.DateTimeField(auto_now_add=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    ghi_chu = models.TextField(blank=True, default='')

    class Meta:
        indexes = [
            models.Index(fields=['model_name', 'object_id', '-thoi_gian']),
            models.Index(fields=['nguoi_thuc_hien', '-thoi_gian']),
            models.Index(fields=['hanh_dong', '-thoi_gian']),
        ]
        ordering = ['-thoi_gian']
        verbose_name = 'Log hệ thống'
        verbose_name_plural = 'Log hệ thống'

    def __str__(self):
        return f"{self.model_name} - {self.get_hanh_dong_display()} - {self.thoi_gian}"


# ========================= 
# THÔNG BÁO
# ========================= 
class ThongBao(models.Model):
    nguoi_nhan = models.ForeignKey(
    User,
    on_delete=models.CASCADE,
    related_name='thong_baos',
    null=True,
    blank=True
    )
    loai = models.CharField(
        max_length=50,
        choices=[
            ('lich_kham', 'Lịch khám'),
            ('phieu_kham', 'Phiếu khám'),
            ('he_thong', 'Hệ thống'),
            ('nhac_nho', 'Nhắc nhở'),
        ],
        default='he_thong',
        db_index=True
    )
    tieu_de = models.CharField(max_length=255)
    noi_dung = models.TextField()
    lien_ket = models.CharField(max_length=500, blank=True, default='')
    thoi_gian = models.DateTimeField(auto_now_add=True, db_index=True)
    da_doc = models.BooleanField(default=False, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['nguoi_nhan', 'da_doc', '-thoi_gian']),
            models.Index(fields=['loai', '-thoi_gian']),
        ]
        ordering = ['-thoi_gian']
        verbose_name = 'Thông báo'
        verbose_name_plural = 'Thông báo'

    def __str__(self):
        return self.tieu_de


# =========================
# NỘI DUNG TRANG GIỚI THIỆU
# =========================
class NoiDungGioiThieu(models.Model):
    tieu_de_trang = models.CharField(max_length=255, default="Hệ thống tra cứu và đặt lịch khám bệnh tích hợp GIS")
    nut_lien_he = models.CharField(max_length=100, default="Liên hệ")
    nut_kham_pha = models.CharField(max_length=100, default="Khám phá ngay")
    tieu_de_gioi_thieu = models.CharField(max_length=255, default="Giới thiệu hệ thống")
    noi_dung_gioi_thieu = models.TextField(
        default="EyeViora là nền tảng tra cứu chuỗi phòng khám mắt và đặt lịch khám trực tuyến, tích hợp dữ liệu chi nhánh với bản đồ số."
    )
    tieu_de_van_de = models.CharField(max_length=255, default="Vấn đề & mục tiêu")
    noi_dung_van_de = models.TextField(
        default="Mục tiêu của đề tài là xây dựng hệ thống quản lý chuỗi phòng khám mắt, hỗ trợ đặt lịch trực tuyến và tìm kiếm chi nhánh bằng bản đồ GIS."
    )
    tieu_de_chuc_nang = models.CharField(max_length=255, default="Chức năng chính")
    ds_chuc_nang = models.TextField(
        default="Tra cứu chi nhánh phòng khám mắt theo từ khóa và trạng thái hoạt động.\nXem thông tin chi nhánh, giờ làm việc, chuyên khoa, bác sĩ và hình ảnh.\nĐịnh vị người dùng, so sánh khoảng cách và tìm đường đến chi nhánh.\nĐặt lịch khám trực tuyến và theo dõi lịch hẹn theo tài khoản.",
        help_text="Mỗi dòng là một chức năng.",
    )
    tieu_de_gis = models.CharField(max_length=255, default="Thành phần GIS")
    ds_thanh_phan_gis = models.TextField(
        default="Hiển thị bản đồ và marker chi nhánh bằng Leaflet.\nLưu và xử lý tọa độ chi nhánh bằng PointField (PostGIS).\nTính khoảng cách từ vị trí người dùng bằng GeoDjango.\nTìm đường bằng dịch vụ OSRM để ước lượng quãng đường và thời gian.",
        help_text="Mỗi dòng là một thành phần GIS.",
    )
    tieu_de_cong_nghe = models.CharField(max_length=255, default="Công nghệ sử dụng")
    ds_cong_nghe = models.TextField(
        default="Django\nGeoDjango\nPostgreSQL + PostGIS\nLeaflet\nOpenStreetMap\nOSRM\nNominatim\nJavaScript",
        help_text="Mỗi dòng là một công nghệ.",
    )
    tieu_de_cta = models.CharField(max_length=255, default="Khám phá ngay")
    mo_ta_cta = models.TextField(default="Truy cập trang chủ để tra cứu chi nhánh, xem bản đồ và đặt lịch khám mắt.")
    thu_tu = models.PositiveIntegerField(default=1, unique=True, editable=False)
    ngay_cap_nhat = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Nội dung giới thiệu"
        verbose_name_plural = "Nội dung giới thiệu"

    def save(self, *args, **kwargs):
        if not self.thu_tu:
            self.thu_tu = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return self.tieu_de_trang


# ========================= 
# SIGNALS - AUTO LOG
# ========================= 

# Auto log khi tạo lịch sử khám bệnh từ phiếu khám
@receiver(post_save, sender=PhieuKham)
def tao_lich_su_kham_benh(sender, instance, created, **kwargs):
    """Keep current history in sync and preserve previous clinical text in audit."""
    if kwargs.get('raw'):
        return
    # Read persisted values: save(update_fields=...) may leave unsaved attributes.
    persisted = sender.objects.select_related('lich_kham__bac_si').get(pk=instance.pk)
    data = {
        'benh_nhan_id': persisted.benh_nhan_id,
        'bac_si_id': persisted.lich_kham.bac_si_id,
        'chi_nhanh_id': persisted.lich_kham.bac_si.chi_nhanh_id,
        'ngay_kham': persisted.lich_kham.ngay_kham,
        'trieu_chung': persisted.trieu_chung,
        'chan_doan': persisted.chan_doan,
        'huong_dieu_tri': persisted.huong_dieu_tri,
        'ghi_chu': persisted.lich_kham.ghi_chu,
    }
    histories = list(LichSuKhamBenh.objects.filter(phieu_kham=instance).select_for_update())
    clinical_fields = ('trieu_chung', 'chan_doan', 'huong_dieu_tri')
    new = {key: data[key] for key in clinical_fields}
    previous = getattr(instance, '_audit_before', None)
    if previous is not None and previous != new:
        LogHeThong.objects.create(
            model_name='PhieuKham', object_id=instance.pk, hanh_dong='update',
            du_lieu_cu=previous, du_lieu_moi=new,
            nguoi_thuc_hien=getattr(instance, '_audit_user', None),
            ghi_chu='Cập nhật phiếu khám và đồng bộ lịch sử khám.',
        )
    for history in histories:
        old = {key: getattr(history, key) for key in clinical_fields}
        if old != new and old != previous:
            LogHeThong.objects.create(
                model_name='LichSuKhamBenh', object_id=history.pk, hanh_dong='update',
                du_lieu_cu=old, du_lieu_moi=new,
                nguoi_thuc_hien=getattr(instance, '_audit_user', None),
                ghi_chu='Đồng bộ lịch sử khám từ phiếu khám; giữ nội dung trước chỉnh sửa.',
            )
    if histories:
        LichSuKhamBenh.objects.filter(phieu_kham=instance).update(**data)
    else:
        LichSuKhamBenh.objects.create(phieu_kham=instance, **data)


# Auto log khi tạo lịch khám mới
@receiver(post_save, sender=LichKham)
def log_tao_lich_kham(sender, instance, created, **kwargs):
    """Tự động log khi tạo lịch khám mới"""
    if created:
        LogLichKham.objects.create(
            lich_kham=instance,
            hanh_dong='tao_moi',
            trang_thai_moi=instance.trang_thai,
            mo_ta=f"Tạo lịch khám mới cho bệnh nhân {instance.benh_nhan.ho_ten}"
        )


# Auto log khi thay đổi trạng thái lịch khám
@receiver(pre_save, sender=LichKham)
def log_thay_doi_trang_thai(sender, instance, **kwargs):
    """Tự động log khi thay đổi trạng thái lịch khám"""
    if instance.pk:
        try:
            old_instance = LichKham.objects.get(pk=instance.pk)
            if old_instance.trang_thai != instance.trang_thai:
                LogLichKham.objects.create(
                    lich_kham=instance,
                    hanh_dong='doi_trang_thai',
                    trang_thai_cu=old_instance.trang_thai,
                    trang_thai_moi=instance.trang_thai,
                    mo_ta=f"Đổi trạng thái từ {old_instance.get_trang_thai_display()} sang {instance.get_trang_thai_display()}"
                )
        except LichKham.DoesNotExist:
            pass


# Auto tạo thông báo khi có lịch khám mới
@receiver(post_save, sender=LichKham)
def tao_thong_bao_lich_kham(sender, instance, created, **kwargs):
    """Tự động tạo thông báo cho bác sĩ khi có lịch khám mới"""
    if created and instance.bac_si.user:
        ThongBao.objects.create(
            nguoi_nhan=instance.bac_si.user,
            loai='lich_kham',
            tieu_de='Lịch khám mới',
            noi_dung=f"Bệnh nhân {instance.benh_nhan.ho_ten} đã đặt lịch khám vào {instance.ngay_kham} lúc {instance.gio_kham}",
            lien_ket=reverse('doctor_exam', args=[instance.pk])
        )


# Auto tạo thông báo khi có phiếu khám mới
@receiver(post_save, sender=PhieuKham)
def tao_thong_bao_phieu_kham(sender, instance, created, **kwargs):
    """Tự động tạo thông báo khi có phiếu khám hoàn thành"""
    if created:
        ThongBao.objects.create(
            nguoi_nhan=instance.lich_kham.bac_si.user if instance.lich_kham.bac_si.user else None,
            loai='phieu_kham',
            tieu_de='Phiếu khám mới',
            noi_dung=f"Đã hoàn thành phiếu khám cho bệnh nhân {instance.benh_nhan.ho_ten}",
            lien_ket=f"/phieu-kham/{instance.pk}/"
        )

