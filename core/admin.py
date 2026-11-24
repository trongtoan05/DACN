from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin
from django.contrib.gis.forms import OSMWidget

from .models import (
    BacSi,
    BenhNhan,
    ChiNhanh,
    GioLamViecBacSi,
    GioLamViecChiNhanh,
    Khoa,
    LichKham,
    LichSuKhamBenh,
    LogHeThong,
    LogLichKham,
    NoiDungGioiThieu,
    PhieuKham,
    ThongBao,
)

admin.site.site_header = "Quản trị EyeViora"
admin.site.site_title = "EyeViora Admin"
admin.site.index_title = "Bảng điều khiển quản trị"


class GioLamViecChiNhanhInline(admin.TabularInline):
    model = GioLamViecChiNhanh
    extra = 0


class GioLamViecBacSiInline(admin.TabularInline):
    model = GioLamViecBacSi
    extra = 0


class ChiNhanhOSMWidget(OSMWidget):
    map_srid = 4326
    default_lat = 10.77
    default_lon = 106.7
    default_zoom = 12


@admin.register(ChiNhanh)
class ChiNhanhAdmin(GISModelAdmin):
    class Media:
        js = ("core/js/chinhanh_admin_map.js",)

    gis_widget = ChiNhanhOSMWidget
    gis_widget_kwargs = {
        "attrs": {
            "default_lat": 10.77,
            "default_lon": 106.7,
            "default_zoom": 12,
        }
    }
    list_display = ("ten", "dang_hoat_dong", "gio_mo", "gio_dong")
    search_fields = ("ten", "dia_chi", "phuong")
    exclude = ("dia_chi", "so_dien_thoai", "email", "phuong")

    ordering = ("ten",)
    inlines = (GioLamViecChiNhanhInline,)


@admin.register(GioLamViecChiNhanh)
class GioLamViecChiNhanhAdmin(admin.ModelAdmin):
    list_display = ("chi_nhanh", "thu", "gio_mo", "gio_dong", "nghi")
    list_filter = ("thu", "nghi", "chi_nhanh")
    ordering = ("chi_nhanh", "thu")


@admin.register(Khoa)
class KhoaAdmin(admin.ModelAdmin):
    list_display = ("ten", "chi_nhanh")
    search_fields = ("ten", "chi_nhanh__ten")
    list_filter = ("chi_nhanh",)
    ordering = ("ten",)


@admin.register(BacSi)
class BacSiAdmin(admin.ModelAdmin):
    list_display = ("ho_ten", "chuyen_khoa", "khoa", "chi_nhanh", "so_dien_thoai")
    search_fields = (
        "ho_ten",
        "chuyen_khoa",
        "so_dien_thoai",
        "khoa__ten",
        "chi_nhanh__ten",
    )
    list_filter = ("chi_nhanh", "khoa", "chuyen_khoa")
    ordering = ("ho_ten",)
    inlines = (GioLamViecBacSiInline,)


@admin.register(GioLamViecBacSi)
class GioLamViecBacSiAdmin(admin.ModelAdmin):
    list_display = ("bac_si", "thu", "gio_bat_dau", "gio_ket_thuc", "nghi")
    list_filter = ("thu", "nghi", "bac_si")
    ordering = ("bac_si", "thu")


@admin.register(BenhNhan)
class BenhNhanAdmin(admin.ModelAdmin):
    list_display = ("ho_ten", "ngay_sinh", "gioi_tinh", "so_dien_thoai")
    search_fields = ("ho_ten", "so_dien_thoai", "dia_chi")
    list_filter = ("gioi_tinh",)
    ordering = ("ho_ten",)


@admin.register(LichKham)
class LichKhamAdmin(admin.ModelAdmin):
    list_display = ("benh_nhan", "bac_si", "ngay_kham", "gio_kham", "trang_thai")
    search_fields = ("benh_nhan__ho_ten", "bac_si__ho_ten", "ghi_chu")
    list_filter = ("trang_thai", "ngay_kham", "bac_si", "benh_nhan")
    date_hierarchy = "ngay_kham"
    ordering = ("-ngay_kham", "-gio_kham")


@admin.register(PhieuKham)
class PhieuKhamAdmin(admin.ModelAdmin):
    list_display = ("benh_nhan", "lich_kham", "ngay_lap")
    search_fields = ("benh_nhan__ho_ten", "chan_doan", "trieu_chung")
    list_filter = ("ngay_lap",)
    date_hierarchy = "ngay_lap"
    ordering = ("-ngay_lap",)


@admin.register(LichSuKhamBenh)
class LichSuKhamBenhAdmin(admin.ModelAdmin):
    list_display = ("benh_nhan", "bac_si", "chi_nhanh", "ngay_kham", "ngay_tao")
    search_fields = ("benh_nhan__ho_ten", "bac_si__ho_ten", "chan_doan", "trieu_chung")
    list_filter = ("chi_nhanh", "bac_si", "ngay_kham")
    date_hierarchy = "ngay_kham"
    ordering = ("-ngay_kham", "-ngay_tao")


class ReadOnlyAuditAdmin(admin.ModelAdmin):
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LogLichKham)
class LogLichKhamAdmin(ReadOnlyAuditAdmin):
    list_display = (
        "lich_kham",
        "hanh_dong",
        "trang_thai_cu",
        "trang_thai_moi",
        "nguoi_thuc_hien",
        "thoi_gian",
    )
    search_fields = (
        "mo_ta",
        "lich_kham__benh_nhan__ho_ten",
        "lich_kham__bac_si__ho_ten",
    )
    list_filter = ("hanh_dong", "thoi_gian")
    date_hierarchy = "thoi_gian"
    ordering = ("-thoi_gian",)


@admin.register(LogHeThong)
class LogHeThongAdmin(ReadOnlyAuditAdmin):
    list_display = ("model_name", "object_id", "hanh_dong", "nguoi_thuc_hien", "thoi_gian")
    search_fields = ("model_name", "object_id", "ghi_chu")
    list_filter = ("model_name", "hanh_dong", "thoi_gian")
    date_hierarchy = "thoi_gian"
    ordering = ("-thoi_gian",)


@admin.register(ThongBao)
class ThongBaoAdmin(admin.ModelAdmin):
    list_display = ("tieu_de", "loai", "nguoi_nhan", "da_doc", "thoi_gian")
    search_fields = ("tieu_de", "noi_dung", "nguoi_nhan__username")
    list_filter = ("loai", "da_doc", "thoi_gian")
    date_hierarchy = "thoi_gian"
    ordering = ("-thoi_gian",)


@admin.register(NoiDungGioiThieu)
class NoiDungGioiThieuAdmin(admin.ModelAdmin):
    list_display = ("tieu_de_trang", "ngay_cap_nhat")
    ordering = ("thu_tu",)
