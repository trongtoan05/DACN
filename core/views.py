from django.shortcuts import render, get_object_or_404, redirect
import datetime
import json
import math
import secrets
import re
from urllib.parse import parse_qs, urlsplit
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.models import User
from django.contrib import messages
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.templatetags.static import static
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.forms import modelform_factory
from django.http import Http404, JsonResponse, FileResponse
from django.db import IntegrityError, transaction
from django.views.decorators.http import require_POST
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache
from django.db.models.deletion import ProtectedError
from django.db.models import Count, Max, Q
from django.db import models as db_models
from django.contrib.gis.geos import Point
from django.contrib.gis.db.models.functions import Distance
from .branches import active_branch_queryset

from .models import (
    ChiNhanh,
    ChiNhanhHinhAnh,
    BenhNhan,
    BacSi,
    BacSiDichVu,
    DanhMucDichVu,
    Khoa,
    DichVuChiNhanh,
    GiaoDichThanhToan,
    GioLamViecChiNhanh,
    GioLamViecBacSi,
    LichKham,
    LichSuKhamBenh,
    HoaDon,
    PhieuKham,
    ChiTietKhamMat,
    DonKinh,
    DonThuoc,
    DichVuThucHien,
    PhieuKhamHinhAnh,
    LogLichKham,
    LogHeThong,
    ThongBao,
    NoiDungGioiThieu,
    YeuCauChinhSuaPhieuKham,
)

from .forms import (
    AdminBacSiForm,
    AdminChiNhanhForm,
    AdminBacSiDichVuForm,
    AdminBenhNhanForm,
    AdminDichVuChiNhanhForm,
    AdminGioLamViecChiNhanhForm,
    AdminGioLamViecBacSiForm,
    AdminKhoaForm,
    AdminUserForm,
    ContactFeedbackForm,
    DatLichForm,
)
from .billing.services import billing_context as exam_billing_context
from .billing.services import bill_exam as prepare_exam_invoice
from .receipts.booking import schedule_booking_confirmation
from .payments.access import can_admin, can_collect
from .appointments import (
    available_booking_slots,
    booking_slot_options,
    bookable_services_for_branch,
    book_appointment,
    eligible_doctors_for_service,
    eligible_doctors_for_services,
    transition_exam,
)
from .exam_uploads import validate_exam_images
from .exam_forms import ExamEyeForm, GlassesPrescriptionForm, MedicineFormSet
from .exam_services.services import doctor_form_context, sync_doctor_services
from .geo import point_to_latlon
from .routing import RouteNotFound, RoutingNotConfigured, RoutingUnavailable, fetch_osrm_route
from .service_catalog.presentation import catalog_description
from django.utils.http import url_has_allowed_host_and_scheme
from django.urls import reverse, resolve, Resolver404


# ==========================
# TRANG CHỦ
# ==========================
def _patient_for_user(user):
    patient = BenhNhan.objects.filter(user=user).first()
    if patient is None:
        patient = BenhNhan.objects.filter(user__isnull=True, so_dien_thoai=user.username).first()
    return patient


def _public_eye_directory(service_limit=8):
    """Build public service/doctor cards only from active, actually bookable assignments."""
    catalog = list(DanhMucDichVu.objects.filter(
        dang_ap_dung=True, cho_phep_dat_lich=True,
        chi_nhanh_ap_dung__dang_ap_dung=True,
        chi_nhanh_ap_dung__chi_nhanh__dang_hoat_dong=True,
        chi_nhanh_ap_dung__bac_si_duoc_phep__dang_ap_dung=True,
    ).distinct().order_by('ten', 'ma_dich_vu'))
    for service in catalog:
        service.display_description = catalog_description(service.ma_dich_vu, service.mo_ta)
        service.booking_branches = []
        offers = service.chi_nhanh_ap_dung.filter(
            dang_ap_dung=True, chi_nhanh__dang_hoat_dong=True,
            bac_si_duoc_phep__dang_ap_dung=True,
        ).select_related('chi_nhanh').distinct().order_by('chi_nhanh__ten')
        for offer in offers:
            if eligible_doctors_for_service(offer.chi_nhanh_id, service.pk).exists():
                service.booking_branches.append(offer.chi_nhanh)

    doctors = list(BacSi.objects.filter(
        chi_nhanh__dang_hoat_dong=True,
        dich_vu_duoc_phep__dang_ap_dung=True,
        dich_vu_duoc_phep__dich_vu_chi_nhanh__dang_ap_dung=True,
        dich_vu_duoc_phep__dich_vu_chi_nhanh__dich_vu__dang_ap_dung=True,
        dich_vu_duoc_phep__dich_vu_chi_nhanh__dich_vu__cho_phep_dat_lich=True,
    ).filter(Q(user__isnull=True) | Q(user__is_active=True))
        .select_related('chi_nhanh').distinct().order_by('ho_ten')[:6])
    for doctor in doctors:
        doctor.booking_services = list(bookable_services_for_branch(doctor.chi_nhanh_id).filter(
            chi_nhanh_ap_dung__bac_si_duoc_phep__bac_si_id=doctor.pk,
            chi_nhanh_ap_dung__bac_si_duoc_phep__dang_ap_dung=True,
        ).distinct().order_by('ten', 'ma_dich_vu')[:5])
    visible_services = [service for service in catalog if service.booking_branches]
    if service_limit is not None:
        visible_services = visible_services[:service_limit]
    return visible_services, doctors


def _has_public_booking(branch_id):
    return any(eligible_doctors_for_service(branch_id, service.pk).exists()
               for service in bookable_services_for_branch(branch_id))


def _public_clinic_queryset():
    """The fixed EyeViora set used only by public clinic discovery pages."""
    return ChiNhanh.objects.filter(
        id__in=settings.PUBLIC_CLINIC_BRANCH_IDS,
        dang_hoat_dong=True,
    ).order_by("id")


def home(request):
    bvs = _public_clinic_queryset().prefetch_related("gio_lam_viecs")
    quick_branches = active_branch_queryset().order_by("ten")
    clinic_services, clinic_doctors = _public_eye_directory()
    unread_count = 0
    display_name = None
    doctor_info = None
    doctor_today = []
    user_lat = None
    user_lon = None
    radius_km = None
    # Public clinic search and map filters.
    filter_open = request.GET.get("open") == "1"
    phuong_filter = (request.GET.get("phuong") or request.GET.get("quan") or "").strip()
    search_query = request.GET.get("q", "").strip()

    all_phuong = (
        _public_clinic_queryset().values_list("phuong", flat=True)
        .distinct()
        .order_by("phuong")
    )

    lat_str = request.GET.get("lat")
    lon_str = request.GET.get("lon")
    radius_str = request.GET.get("radius")

    user_point = None
    if lat_str and lon_str:
        try:
            user_lat = float(lat_str)
            user_lon = float(lon_str)
            if -90 <= user_lat <= 90 and -180 <= user_lon <= 180:
                # GIS: build user location (SRID 4326) for distance/radius filtering
                user_point = Point(user_lon, user_lat, srid=4326)
        except ValueError:
            user_point = None

    if radius_str:
        try:
            radius_km = float(radius_str)
            if radius_km <= 0:
                radius_km = None
        except ValueError:
            radius_km = None

    if phuong_filter:
        bvs = bvs.filter(phuong=phuong_filter)
    if search_query:
        bvs = bvs.filter(
            Q(ten__icontains=search_query)
            | Q(dia_chi__icontains=search_query)
            | Q(phuong__icontains=search_query)
        )

    if request.user.is_authenticated:
        unread_count = ThongBao.objects.filter(
            nguoi_nhan=request.user,
            da_doc=False,
        ).count()
        try:
            bac_si = request.user.bac_si
        except BacSi.DoesNotExist:
            bac_si = None
        if bac_si:
            display_name = bac_si.ho_ten
            doctor_info = {
                "khoa": bac_si.khoa.ten,
                "chi_nhanh": bac_si.chi_nhanh.ten,
            }
            today = timezone.localdate()
            doctor_today = list(
                LichKham.objects.filter(
                    bac_si=bac_si,
                    ngay_kham=today,
                )
                .select_related("benh_nhan")
                .order_by("gio_kham")
            )
            for lich in doctor_today:
                lich.ca_label = "Ca sáng" if lich.gio_kham < datetime.time(12, 0) else "Ca chiều"
        else:
            benh_nhan = _patient_for_user(request.user)
            display_name = benh_nhan.ho_ten if benh_nhan else request.user.username

    query_params = request.GET.copy()
    query_params.pop("page", None)
    query_string_without_page = query_params.urlencode()

    def paginate_clinics(items):
        paginator = Paginator(items, 9)
        page_obj = paginator.get_page(request.GET.get("page"))
        return paginator, page_obj, list(page_obj.object_list)

    now = timezone.localtime()
    time_bucket = now.strftime("%Y%m%d%H%M")
    cache_key = (
        f"home_filter_v2:"
        f"lat={user_lat}|lon={user_lon}|radius={radius_km}|"
        f"open={int(filter_open)}|"
        f"phuong={phuong_filter or 'all'}|q={search_query.lower()}|t={time_bucket}"
    )
    cached = cache.get(cache_key)
    if cached:
        ids = cached.get("ids", [])
        computed_map = cached.get("computed", {})
        bvs_qs = _public_clinic_queryset().filter(id__in=ids).prefetch_related("gio_lam_viecs")
        bvs_map = {bv.id: bv for bv in bvs_qs}
        cached_bvs = []
        for bv_id in ids:
            bv = bvs_map.get(bv_id)
            if not bv:
                continue
            info = computed_map.get(str(bv_id)) or computed_map.get(bv_id, {})
            bv.map_lat = info.get("map_lat")
            bv.map_lon = info.get("map_lon")
            bv.is_open = info.get("is_open")
            bv.has_reliable_hours = info.get("has_reliable_hours", False)
            bv.emergency_active = info.get("emergency_active", False)
            bv.distance_km = info.get("distance_km")
            cached_bvs.append(bv)
        bvs = cached_bvs

        total_bvs = len(bvs)
        paginator, page_obj, paged_bvs = paginate_clinics(bvs)
        for branch in bvs:
            branch.has_booking_options = _has_public_booking(branch.pk)

        map_data = []
        for bv in bvs:
            if bv.map_lat is None or bv.map_lon is None:
                continue
            map_data.append({
                "id": bv.id,
                "name": bv.ten,
                "phuong": bv.phuong,
                "address": bv.dia_chi,
                "lat": bv.map_lat,
                "lon": bv.map_lon,
                "open": bv.is_open,
                "has_reliable_hours": bv.has_reliable_hours,
                "distance_km": bv.distance_km,
                "has_booking_options": bv.has_booking_options,
                "detail_url": reverse("branch_detail", args=[bv.pk]),
                "booking_url": f"{reverse('dat_lich_picker')}?chi_nhanh={bv.pk}",
                "image_url": static("core/img/EyeViora/branches/branch-default.jpg"),
            })

        return render(request, "core/home.html", {
            "bvs": paged_bvs,
            "total_bvs": total_bvs,
            "paginator": paginator,
            "page_obj": page_obj,
            "query_string_without_page": query_string_without_page,
            "unread_count": unread_count,
            "display_name": display_name,
            "doctor_info": doctor_info,
            "doctor_today": doctor_today,
            "user_lat": user_lat,
            "user_lon": user_lon,
            "radius_km": radius_km,
            "filter_open": filter_open,
            "phuong_filter": phuong_filter,
            "q": search_query,
            "all_phuong": all_phuong,
            "map_data": map_data,
            "clinic_services": clinic_services,
            "clinic_doctors": clinic_doctors,
            "quick_branches": quick_branches,
            "branch_image_url": static("core/img/EyeViora/branches/branch-default.jpg"),
            "has_reliable_branch_hours": any(branch.has_reliable_hours for branch in bvs),
            "today": timezone.localdate(),
            "home_map_options": {"detail_url": reverse("branch_detail", args=[0])},
        })

    if user_point:
        # GIS: annotate distance from user, filter by radius, sort nearest first
        bvs = bvs.annotate(distance_m=Distance("vi_tri", user_point, spheroid=False))
        if radius_km:
            bvs = bvs.filter(distance_m__lte=radius_km * 1000)
        bvs = bvs.order_by("distance_m")

    thu = (now.weekday() + 1) % 7
    computed_bvs = []
    for bv in bvs:
        gio_lam = None
        for glv in bv.gio_lam_viecs.all():
            if glv.thu == thu:
                gio_lam = glv
                break

        if gio_lam:
            has_reliable_hours = True
            if gio_lam.nghi:
                is_open = False
            else:
                is_open = gio_lam.gio_mo <= now.time() <= gio_lam.gio_dong
        else:
            has_reliable_hours = False
            is_open = None

        emergency_active = False

        map_lat, map_lon = point_to_latlon(bv.vi_tri) if bv.vi_tri else (None, None)
        setattr(bv, "map_lat", map_lat)
        setattr(bv, "map_lon", map_lon)
        setattr(bv, "is_open", is_open)
        setattr(bv, "has_reliable_hours", has_reliable_hours)
        setattr(bv, "emergency_active", emergency_active)

        distance_km = None
        if hasattr(bv, "distance_m") and bv.distance_m is not None:
            if hasattr(bv.distance_m, "km"):
                distance_km = round(bv.distance_m.km, 2)
            elif hasattr(bv.distance_m, "m"):
                distance_km = round(bv.distance_m.m / 1000.0, 2)
            else:
                distance_km = round(float(bv.distance_m) / 1000.0, 2)
        setattr(bv, "distance_km", distance_km)
        if filter_open and (not bv.has_reliable_hours or not bv.is_open):
            continue
        computed_bvs.append(bv)

    if user_point:
        computed_bvs.sort(
            key=lambda item: (
                item.distance_km is None,
                item.distance_km if item.distance_km is not None else 1e9,
                item.ten.lower(),
            )
        )
    else:
        computed_bvs.sort(
            key=lambda item: (
                not getattr(item, "is_open", False),
                item.ten.lower(),
            )
        )
    bvs = computed_bvs

    total_bvs = len(bvs)
    paginator, page_obj, paged_bvs = paginate_clinics(bvs)
    for branch in bvs:
        branch.has_booking_options = _has_public_booking(branch.pk)

    map_data = []
    for bv in bvs:
        if bv.map_lat is None or bv.map_lon is None:
            continue
        map_data.append({
            "id": bv.id,
            "name": bv.ten,
            "phuong": bv.phuong,
            "address": bv.dia_chi,
            "lat": bv.map_lat,
            "lon": bv.map_lon,
            "open": bv.is_open,
            "has_reliable_hours": bv.has_reliable_hours,
            "distance_km": bv.distance_km,
            "has_booking_options": bv.has_booking_options,
            "detail_url": reverse("branch_detail", args=[bv.pk]),
            "booking_url": f"{reverse('dat_lich_picker')}?chi_nhanh={bv.pk}",
            "image_url": static("core/img/EyeViora/branches/branch-default.jpg"),
        })

    computed_payload = {}
    for bv in bvs:
        computed_payload[str(bv.id)] = {
            "map_lat": getattr(bv, "map_lat", None),
            "map_lon": getattr(bv, "map_lon", None),
            "is_open": getattr(bv, "is_open", False),
            "has_reliable_hours": getattr(bv, "has_reliable_hours", False),
            "emergency_active": getattr(bv, "emergency_active", False),
            "distance_km": getattr(bv, "distance_km", None),
        }
    cache.set(cache_key, {
        "ids": [bv.id for bv in bvs],
        "computed": computed_payload,
    }, timeout=60)

    return render(request, "core/home.html", {
        "bvs": paged_bvs,
        "total_bvs": total_bvs,
        "paginator": paginator,
        "page_obj": page_obj,
        "query_string_without_page": query_string_without_page,
        "unread_count": unread_count,
        "display_name": display_name,
        "doctor_info": doctor_info,
        "doctor_today": doctor_today,
        "user_lat": user_lat,
        "user_lon": user_lon,
        "radius_km": radius_km,
        "filter_open": filter_open,
        "phuong_filter": phuong_filter,
        "q": search_query,
        "all_phuong": all_phuong,
        "map_data": map_data,
        "clinic_services": clinic_services,
        "clinic_doctors": clinic_doctors,
        "quick_branches": quick_branches,
        "branch_image_url": static("core/img/EyeViora/branches/branch-default.jpg"),
        "has_reliable_branch_hours": any(branch.has_reliable_hours for branch in bvs),
        "today": timezone.localdate(),
        "home_map_options": {"detail_url": reverse("branch_detail", args=[0])},
    })


def public_service_catalog(request):
    # The public catalog includes both appointment services and doctor-directed
    # techniques. Only appointment services receive bookable branch links.
    services = list(DanhMucDichVu.objects.filter(dang_ap_dung=True)
                    .exclude(ma_dich_vu__startswith='DEMO_')
                    .order_by('nhom', 'ten', 'ma_dich_vu'))
    for service in services:
        service.display_description = catalog_description(service.ma_dich_vu, service.mo_ta)
        service.booking_branches = []
        if not service.cho_phep_dat_lich:
            continue
        offers = service.chi_nhanh_ap_dung.filter(
            dang_ap_dung=True, chi_nhanh__dang_hoat_dong=True,
            chi_nhanh__pk__in=active_branch_queryset().values('pk'),
        ).select_related('chi_nhanh').distinct().order_by('chi_nhanh__ten')
        service.booking_branches = [offer.chi_nhanh for offer in offers if
                                    eligible_doctors_for_service(offer.chi_nhanh_id, service.pk).exists()]
    booking_order = (
        "MAT_KHAM_TQ", "MAT_KHAM_KHUC_XA", "MAT_KHAM_TRE_EM",
        "MAT_KHAM_KHO_MAT", "MAT_KHAM_GLOCOM", "MAT_KHAM_VONG_MAC",
    )
    technical_order = (
        "MAT_DO_THI_LUC", "MAT_DO_KHUC_XA", "MAT_DO_NHAN_AP", "MAT_TEST_KHO_MAT",
        "MAT_DO_THI_TRUONG", "MAT_CHUP_OCT", "MAT_CHUP_DAY_MAT",
        "MAT_BAN_DO_GIAC_MAC", "MAT_SIEU_AM",
    )
    by_code = {service.ma_dich_vu: service for service in services}
    booking_services = [by_code[code] for code in booking_order if code in by_code]
    booking_services += [service for service in services
                         if service.cho_phep_dat_lich and service.ma_dich_vu not in booking_order]
    technical_services = [by_code[code] for code in technical_order if code in by_code]
    technical_services += [service for service in services
                           if not service.cho_phep_dat_lich and service.ma_dich_vu not in technical_order]
    return render(request, "core/public_services.html", {
        "clinic_services": services,
        "booking_services": booking_services,
        "technical_services": technical_services,
    })


# ==========================
# GIOI THIEU
# ==========================
def gioi_thieu(request):
    content = NoiDungGioiThieu.objects.filter(thu_tu=1).first()
    bvs = _public_clinic_queryset().only("id", "ten", "phuong", "dia_chi", "vi_tri")

    map_data = []
    for bv in bvs:
        lat, lon = point_to_latlon(bv.vi_tri) if bv.vi_tri else (None, None)
        if lat is None or lon is None:
            continue
        map_data.append({
            "id": bv.id,
            "name": bv.ten,
            "phuong": bv.phuong,
            "address": bv.dia_chi,
            "lat": lat,
            "lon": lon,
            "detail_url": reverse("branch_detail", args=[bv.pk]),
            "booking_url": f"{reverse('dat_lich_picker')}?chi_nhanh={bv.pk}",
            "image_url": static("core/img/EyeViora/branches/branch-default.jpg"),
            "has_booking_options": _has_public_booking(bv.pk),
        })

    return render(request, "core/about.html", {
        "content": content,
        "public_title": "Chuỗi phòng khám mắt và đặt lịch trực tuyến",
        "public_intro_title": "Tra cứu chi nhánh, dịch vụ và bác sĩ",
        "public_intro": "Tìm phòng khám mắt đang hoạt động, xem dịch vụ được cấu hình cho đặt lịch, đội ngũ bác sĩ được phân công và thông tin liên hệ từng chi nhánh.",
        "public_goal_title": "Đặt lịch thuận tiện, thông tin rõ ràng",
        "public_goal": "Quy trình trực tuyến giúp người bệnh chọn chi nhánh, dịch vụ, bác sĩ, ngày và giờ. Thanh toán được thực hiện sau khi bác sĩ hoàn tất khám.",
        "features": [
            "Tìm chi nhánh đang hoạt động theo tên, phường và địa chỉ.",
            "Xem dịch vụ đặt trước và bác sĩ đang được phân công tại mỗi chi nhánh.",
            "Định vị khi người dùng chủ động yêu cầu, xem khoảng cách và đường đi.",
            "Đặt lịch theo chuỗi chi nhánh → dịch vụ → bác sĩ → ngày, giờ.",
            "Theo dõi lịch khám bằng tài khoản người bệnh.",
        ],
        "gis_components": [
            "Leaflet và OpenStreetMap hiển thị chi nhánh có tọa độ hợp lệ.",
            "PostGIS lưu vị trí chi nhánh; GeoDjango tính khoảng cách khi có vị trí đầu vào.",
            "OSRM ước lượng tuyến đường tới một chi nhánh đã chọn.",
            "Không tạo marker thay thế cho chi nhánh thiếu tọa độ hợp lệ.",
        ],
        "tech_tags": [line.strip() for line in content.ds_cong_nghe.splitlines() if line.strip()] if content else [],
        "public_cta_title": "Tìm chi nhánh phòng khám mắt",
        "public_cta_description": "Xem các chi nhánh đang hoạt động và bắt đầu đặt lịch khám trực tuyến.",
        "map_data": map_data,
        "total_clinics": len(map_data),
        "branch_image_url": static("core/img/EyeViora/branches/branch-default.jpg"),
    })


# ==========================
# CHI TIẾT CHI NHÁNH
# ==========================
@require_POST
@never_cache
def clinic_route_api(request):
    if len(request.body) > 2048:
        return JsonResponse({"error": "Yêu cầu tìm đường quá lớn."}, status=413)
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"error": "Dữ liệu tìm đường không hợp lệ."}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"error": "Dữ liệu tìm đường không hợp lệ."}, status=400)

    branch_id = data.get("branch_id")
    if not isinstance(branch_id, int) or isinstance(branch_id, bool):
        return JsonResponse({"error": "Chi nhánh không hợp lệ."}, status=400)
    branch = active_branch_queryset().filter(pk=branch_id).first()
    if not branch:
        return JsonResponse({"error": "Không tìm thấy chi nhánh đang hoạt động."}, status=404)
    destination = point_to_latlon(branch.vi_tri)
    if not destination or destination[0] is None or destination[1] is None:
        return JsonResponse({"error": "Chi nhánh chưa có tọa độ hợp lệ."}, status=422)

    referrer = request.headers.get("Referer", "")
    referrer_parts = urlsplit(referrer)
    if referrer_parts.scheme != request.scheme or referrer_parts.netloc != request.get_host():
        referrer = None
    try:
        route = fetch_osrm_route(
            (data.get("start_lat"), data.get("start_lon")),
            destination,
            referer=referrer,
        )
    except RouteNotFound as exc:
        return JsonResponse({"error": str(exc)}, status=422)
    except RoutingNotConfigured as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    except RoutingUnavailable as exc:
        return JsonResponse({"error": str(exc)}, status=502)

    return JsonResponse(route)


def branch_detail(request, id):
    if can_admin(request.user):
        # Admin can inspect retired-branch details for records and reconciliation.
        detail_branches = ChiNhanh.objects.all()
    else:
        detail_branches = active_branch_queryset().filter(id__in=settings.PUBLIC_CLINIC_BRANCH_IDS)
    bv = get_object_or_404(
        detail_branches.prefetch_related("hinh_anhs"),
        id=id,
    )

    services = [service for service in bookable_services_for_branch(bv.pk)
                if eligible_doctors_for_service(bv.pk, service.pk).exists()]
    for service in services:
        service.display_description = catalog_description(service.ma_dich_vu, service.mo_ta)
    bac_sis = list(BacSi.objects.filter(
        chi_nhanh=bv,
        dich_vu_duoc_phep__dang_ap_dung=True,
        dich_vu_duoc_phep__dich_vu_chi_nhanh__dang_ap_dung=True,
        dich_vu_duoc_phep__dich_vu_chi_nhanh__dich_vu__dang_ap_dung=True,
        dich_vu_duoc_phep__dich_vu_chi_nhanh__dich_vu__cho_phep_dat_lich=True,
    ).filter(Q(user__isnull=True) | Q(user__is_active=True)).distinct().order_by('ho_ten'))
    for doctor in bac_sis:
        doctor.booking_services = [service for service in services if
            eligible_doctors_for_service(bv.pk, service.pk).filter(pk=doctor.pk).exists()]
        doctor.booking_service_count = len(doctor.booking_services)
        doctor.public_specialty = re.sub(
            r'\s*\(dữ liệu demo\)\s*$', '', doctor.chuyen_khoa or '', flags=re.IGNORECASE,
        ).strip() or 'Bác sĩ nhãn khoa'
    gio_lam_viec = GioLamViecChiNhanh.objects.filter(chi_nhanh=bv).order_by("thu")
    clinic_images = list(bv.hinh_anhs.all())
    primary_image = next((img for img in clinic_images if img.la_anh_dai_dien), None)
    if not primary_image and clinic_images:
        primary_image = clinic_images[0]
    map_lat, map_lon = point_to_latlon(bv.vi_tri)

    return render(request, "core/branch_detail.html", {
        "bv": bv,
        "services": services,
        "booking_available": bool(services),
        "bac_sis": bac_sis,
        "gio_lam_viec": gio_lam_viec,
        "clinic_images": clinic_images,
        "primary_image": primary_image,
        "branch_image_url": static("core/img/EyeViora/branches/branch-default.jpg"),
        "map_lat": map_lat,
        "map_lon": map_lon,
        "map_available": map_lat is not None and map_lon is not None,
        "map_coordinates": {"lat": map_lat, "lon": map_lon},
        "route_api_url": reverse("clinic_route_api"),
        "routing_configured": bool(settings.EYEVIORA_OSRM_BASE_URL),
        "routing_demo": urlsplit(settings.EYEVIORA_OSRM_BASE_URL).hostname in {
            "router.project-osrm.org", "routing.openstreetmap.de", "router.openstreetmap.de",
        } if settings.EYEVIORA_OSRM_BASE_URL else False,
    })


# ==========================
# ĐẶT LỊCH KHÁM (BỆNH NHÂN)
# ==========================
@login_required
def dat_lich(request, bv_id=None):
    benh_nhan = _patient_for_user(request.user)
    if not benh_nhan:
        return render(request, "core/error.html", {
            "msg": "Bạn chưa có hồ sơ bệnh nhân"
        })

    branches = active_branch_queryset().order_by("ten")
    source = request.POST if request.method == "POST" else request.GET
    branch_raw = source.get("chi_nhanh", "")
    if request.method == "GET" and not branch_raw and bv_id:
        branch_raw = str(bv_id)
    branch_id = int(branch_raw) if branch_raw.isascii() and branch_raw.isdigit() else None
    selected_branch = branches.filter(pk=branch_id).first() if branch_id else None
    services = bookable_services_for_branch(selected_branch.pk) if selected_branch else DanhMucDichVu.objects.none()
    service_raws = source.getlist("dich_vu_dat_truoc_nhieu") or source.getlist("dich_vu_dat_truoc")
    if request.method == "GET" and not service_raws and source.get("dich_vu_dat_truoc"):
        service_raws = [source.get("dich_vu_dat_truoc")]
    service_ids = list(dict.fromkeys(int(value) for value in service_raws if value.isascii() and value.isdigit()))
    requested_service_ids = list(service_ids)
    doctor_raw = source.get("bac_si", "")
    doctor_id = int(doctor_raw) if doctor_raw.isascii() and doctor_raw.isdigit() else None
    if request.method == "GET" and selected_branch and not service_ids and doctor_id:
        # A doctor card may preselect one currently eligible service, but only
        # through the same active branch/service assignment used by booking.
        service_id = services.filter(
            chi_nhanh_ap_dung__bac_si_duoc_phep__bac_si_id=doctor_id,
            chi_nhanh_ap_dung__bac_si_duoc_phep__dang_ap_dung=True,
        ).values_list("pk", flat=True).order_by("ten", "ma_dich_vu").first()
        service_ids = [service_id] if service_id else []
    selected_services = list(services.filter(pk__in=service_ids)) if service_ids else []
    service_ids = [service.pk for service in selected_services]
    selected_service = selected_services[0] if selected_services else None
    doctors = eligible_doctors_for_services(selected_branch.pk, service_ids) if selected_services else BacSi.objects.none()
    selected_doctor = doctors.filter(pk=doctor_id).first() if doctor_id else None
    date_raw = source.get("ngay_kham", "")
    try:
        selected_date = datetime.date.fromisoformat(date_raw) if date_raw else None
    except ValueError:
        selected_date = None
    slot_options = booking_slot_options(
        selected_doctor, selected_date, patient_id=benh_nhan.pk, service_ids=service_ids,
    ) if selected_doctor and selected_date else []
    slots = [slot for slot, available in slot_options if available]

    initial = {}
    if selected_branch:
        initial["chi_nhanh"] = selected_branch.pk
    if selected_service:
        initial["dich_vu_dat_truoc_nhieu"] = service_ids
    if selected_doctor:
        initial["bac_si"] = selected_doctor.pk
    if selected_date:
        initial["ngay_kham"] = selected_date
    requested_time = source.get("gio_kham", "")
    if requested_time and requested_time in {slot.strftime("%H:%M") for slot in slots}:
        initial["gio_kham"] = requested_time

    form = DatLichForm(
        request.POST if request.method == "POST" else None,
        initial=initial,
        branches=branches,
        services=services,
        doctors=doctors,
        slots=slots,
        slot_options=slot_options,
    )
    form.fields["ngay_kham"].widget.attrs["min"] = timezone.localdate().isoformat()
    form.fields["ngay_kham"].widget.attrs["disabled"] = selected_doctor is None
    form.fields["gio_kham"].widget.attrs["disabled"] = not slots
    if request.method == "POST":
        if form.is_valid():
            lich = form.save(commit=False)
            lich.benh_nhan = benh_nhan
            try:
                book_appointment(lich, selected_services=form.cleaned_data['dich_vu_dat_truoc_nhieu'])
            except ValidationError as exc:
                for field, errors in exc.message_dict.items():
                    if field in form.fields:
                        for error in errors:
                            form.add_error(field, error)
                    else:
                        for error in errors:
                            form.add_error(None, error)
            except IntegrityError:
                form.add_error(None, "Khung giờ vừa được đặt hoặc bạn đã có lịch trùng. Vui lòng chọn lại ngày/giờ.")
            else:
                messages.success(request, "Đặt lịch thành công. Thanh toán được thực hiện sau khi khám.")
                ThongBao.objects.create(
                    nguoi_nhan=request.user,
                    loai="lich_kham",
                    tieu_de="Đặt lịch thành công",
                    noi_dung=f"Đã đặt {', '.join(service.ten for service in form.cleaned_data['dich_vu_dat_truoc_nhieu'])} ngày {lich.ngay_kham} lúc {lich.gio_kham:%H:%M} với bác sĩ {lich.bac_si.ho_ten}.",
                    lien_ket="/lich-kham-sap-toi/",
                )
                schedule_booking_confirmation(lich, request.user.email)
                return redirect("upcoming_appointments")
        messages.error(request, "Không thể đặt lịch. Vui lòng kiểm tra lại thông tin.")

    schedule_rows = [
        {"bac_si": doctor, "gio_lam_viecs": doctor.gio_lam_viecs.all().order_by("thu")}
        for doctor in doctors.prefetch_related("gio_lam_viecs")
    ]
    return render(request, "core/appointment.html", {
        "form": form,
        "benh_nhan": benh_nhan,
        "chi_nhanhs": branches,
        "selected_branch": selected_branch,
        "selected_service": selected_service,
        "selected_services": selected_services,
        "selected_doctor": selected_doctor,
        "services": services,
        "bac_sis": doctors,
        "bac_si_schedules": schedule_rows,
        "slots": slots,
        "slot_options": slot_options,
        "today": timezone.localdate(),
        "initial_branch_id": selected_branch.pk if selected_branch else "",
        "initial_service_id": selected_service.pk if selected_service else "",
        "initial_service_ids": service_ids if selected_branch else requested_service_ids,
        "initial_doctor_id": selected_doctor.pk if selected_doctor else "",
        "initial_date": selected_date.isoformat() if selected_date else "",
    })


@login_required
@require_GET
@never_cache
def appointment_options_api(request):
    """Return only public service, doctor, and free-slot options for a valid booking chain."""
    patient = _patient_for_user(request.user)
    if not patient:
        return JsonResponse({"message": "Tài khoản chưa có hồ sơ bệnh nhân."}, status=403)
    branch_raw = request.GET.get("chi_nhanh", "")
    if not branch_raw.isascii() or not branch_raw.isdigit():
        return JsonResponse({"message": "Chọn một chi nhánh đang hoạt động."}, status=400)
    branch = active_branch_queryset().filter(pk=int(branch_raw)).first()
    if not branch:
        return JsonResponse({"message": "Chi nhánh không khả dụng."}, status=400)

    service_rows = bookable_services_for_branch(branch.pk)
    data = {
        "services": [{
            "id": service.pk,
            "name": service.ten,
            "code": service.ma_dich_vu,
            "description": catalog_description(service.ma_dich_vu, service.mo_ta),
            "price": str(service.don_gia),
            "duration_minutes": service.thoi_luong_phut,
        } for service in service_rows],
        "doctors": [],
        "slots": [],
    }
    service_raws = request.GET.getlist("dich_vu_dat_truoc_nhieu") or request.GET.getlist("dich_vu_dat_truoc")
    if not service_raws:
        data["message"] = "Chi nhánh chưa có dịch vụ đặt trước." if not data["services"] else "Chọn dịch vụ để xem bác sĩ."
        return JsonResponse(data)
    if any(not value.isascii() or not value.isdigit() for value in service_raws):
        return JsonResponse({"message": "Dịch vụ không hợp lệ."}, status=400)
    service_ids = list(dict.fromkeys(int(value) for value in service_raws))
    services = list(service_rows.filter(pk__in=service_ids))
    if not services or len(services) != len(service_ids):
        return JsonResponse({"message": "Dịch vụ không khả dụng tại chi nhánh đã chọn."}, status=400)
    data["duration_minutes"] = sum(service.thoi_luong_phut for service in services)

    doctor_rows = eligible_doctors_for_services(branch.pk, service_ids)
    doctor_rows = doctor_rows.prefetch_related("gio_lam_viecs")
    data["doctors"] = [{
        "id": doctor.pk,
        "name": doctor.ho_ten,
        "specialty": doctor.chuyen_khoa,
        "schedules": [{
            "weekday": schedule.get_thu_display(),
            "off": schedule.nghi,
            "start": schedule.gio_bat_dau.strftime("%H:%M") if schedule.gio_bat_dau else "",
            "end": schedule.gio_ket_thuc.strftime("%H:%M") if schedule.gio_ket_thuc else "",
        } for schedule in doctor.gio_lam_viecs.all().order_by("thu")],
    } for doctor in doctor_rows]
    doctor_raw = request.GET.get("bac_si", "")
    if not doctor_raw:
        data["message"] = "Chưa có bác sĩ được phân công dịch vụ này." if not data["doctors"] else "Chọn bác sĩ, ngày và giờ khám."
        return JsonResponse(data)
    if not doctor_raw.isascii() or not doctor_raw.isdigit():
        return JsonResponse({"message": "Bác sĩ không hợp lệ."}, status=400)
    doctor = doctor_rows.filter(pk=int(doctor_raw)).first()
    if not doctor:
        return JsonResponse({"message": "Bác sĩ không khả dụng cho dịch vụ này."}, status=400)
    date_raw = request.GET.get("ngay_kham", "")
    if not date_raw:
        return JsonResponse(data)
    try:
        day = datetime.date.fromisoformat(date_raw)
    except ValueError:
        return JsonResponse({"message": "Ngày khám không hợp lệ."}, status=400)
    slot_options = booking_slot_options(
        doctor, day, patient_id=patient.pk, service_ids=service_ids,
    )
    data["slots"] = [slot.strftime("%H:%M") for slot, available in slot_options if available]
    data["slot_options"] = [{"time": slot.strftime("%H:%M"), "available": available}
                            for slot, available in slot_options]
    data["message"] = ("Tổng thời lượng dịch vụ vượt quá giới hạn một lượt khám." if data["duration_minutes"] > 480
                       else "Không còn khung giờ phù hợp trong ngày đã chọn." if not data["slots"]
                       else "Chọn khung giờ còn trống.")
    return JsonResponse(data)


@login_required
def upcoming_appointments(request):
    benh_nhan = _patient_for_user(request.user)

    if not benh_nhan:
        return render(request, "core/error.html", {
            "msg": "Bạn chưa có hồ sơ bệnh nhân"
        })

    today = timezone.localdate()
    all_upcoming = LichKham.objects.filter(
        benh_nhan=benh_nhan,
        ngay_kham__gte=today,
    ).order_by("ngay_kham", "gio_kham")

    lich_khams = all_upcoming.exclude(trang_thai="huy").exclude(trang_thai="xong")
    lich_huy = all_upcoming.filter(trang_thai="huy")

    now = timezone.localtime()
    window_end = now + datetime.timedelta(hours=24)
    tz = timezone.get_current_timezone()
    for lich in lich_khams.filter(trang_thai="cho"):
        lich_dt = datetime.datetime.combine(lich.ngay_kham, lich.gio_kham)
        lich_dt = timezone.make_aware(lich_dt, tz)
        if now <= lich_dt <= window_end:
            ThongBao.objects.get_or_create(
                nguoi_nhan=request.user,
                loai="nhac_nho",
                lien_ket=f"/lich-kham-sap-toi/?lich={lich.id}",
                defaults={
                    "tieu_de": "Nhắc lịch khám",
                    "noi_dung": (
                        f"Bạn có lịch khám vào ngày {lich.ngay_kham} lúc {lich.gio_kham} với bác sĩ {lich.bac_si.ho_ten}."
                    ),
                },
            )

    return render(request, "core/upcoming_appointments.html", {
        "benh_nhan": benh_nhan,
        "lich_khams": lich_khams,
        "lich_huy": lich_huy,
    })


# ==========================
# HỦY LỊCH KHÁM (BỆNH NHÂN)
# ==========================
@login_required
@require_POST
@transaction.atomic
def cancel_appointment(request, lich_id):

    benh_nhan = _patient_for_user(request.user)

    if not benh_nhan:
        return render(request, "core/error.html", {
            "msg": "Bạn chưa có hồ sơ bệnh nhân"
        })

    lich = get_object_or_404(LichKham.objects.select_for_update(), id=lich_id, benh_nhan=benh_nhan)

    if lich.trang_thai == "huy":
        messages.error(request, "Lịch khám đã bị hủy trước đó.")
        return redirect("upcoming_appointments")

    if lich.trang_thai in ("dang", "xong"):
        messages.error(request, "Không thể hủy lịch đang khám hoặc đã hoàn thành.")
        return redirect("upcoming_appointments")

    now = timezone.localtime()
    if lich.ngay_kham < now.date() or (
        lich.ngay_kham == now.date() and lich.gio_kham <= now.time()
    ):
        messages.error(request, "Không thể hủy lịch đã đến giờ khám.")
        return redirect("upcoming_appointments")

    lich.trang_thai = "huy"
    lich.save()
    ThongBao.objects.create(
        nguoi_nhan=request.user,
        loai="he_thong",
        tieu_de="Đã hủy lịch khám",
        noi_dung=(
            f"Hủy lịch khám vào ngày {lich.ngay_kham} lúc {lich.gio_kham} với bác sĩ {lich.bac_si.ho_ten}."
        ),
        lien_ket="/lich-kham-sap-toi/",
    )

    messages.success(request, "Đã hủy lịch khám thành công.")
    return redirect("upcoming_appointments")

# ==========================
# LỊCH SỬ KHÁM (BỆNH NHÂN)
# ==========================
@login_required
def medical_history(request):
    benh_nhan = _patient_for_user(request.user)

    if not benh_nhan:
        return render(request, "core/error.html", {
            "msg": "Bạn chưa có hồ sơ bệnh nhân"
        })

    lich_su = (
        LichSuKhamBenh.objects.filter(benh_nhan=benh_nhan).filter(
            Q(phieu_kham__isnull=True) | Q(phieu_kham__lich_kham__trang_thai='xong')
        )
        .select_related("bac_si", "chi_nhanh", "phieu_kham", "phieu_kham__hoa_don")
        .order_by("-ngay_kham", "-ngay_tao")
    )

    return render(request, "core/medical_history.html", {
        "benh_nhan": benh_nhan,
        "lich_su": lich_su,
    })


# ==========================
# CHI TIẾT PHIẾU KHÁM
# ==========================
@never_cache
@login_required
@require_GET
def private_exam_image(request, path):
    image = get_object_or_404(
        PhieuKhamHinhAnh.objects.select_related('phieu_kham__lich_kham__bac_si'),
        hinh_anh=path,
    )
    exam = image.phieu_kham
    doctor = getattr(request.user, 'bac_si', None)
    patient = _patient_for_user(request.user)
    if not request.user.is_active or not (
        (doctor and exam.lich_kham.bac_si_id == doctor.pk)
        or (patient and exam.benh_nhan_id == patient.pk)
    ):
        raise Http404('Không tìm thấy ảnh phiếu khám.')
    try:
        image.hinh_anh.open('rb')
    except FileNotFoundError:
        raise Http404('Không tìm thấy ảnh phiếu khám.') from None
    response = FileResponse(image.hinh_anh)
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return response


def deny_public_exam_image(request, path):
    raise Http404('Ảnh phiếu khám không được phục vụ trong media công khai.')


@login_required
@require_GET
def phieu_kham_detail(request, phieu_id):
    def correction_snapshot_sections(snapshot):
        snapshot = snapshot or {}
        sections = []
        labels = {
            'trieu_chung': 'Triệu chứng', 'chan_doan': 'Chẩn đoán',
            'huong_dieu_tri': 'Hướng điều trị',
            'thi_luc_phai_khong_kinh': 'Thị lực mắt phải, không kính',
            'thi_luc_phai_co_kinh': 'Thị lực mắt phải, có kính',
            'thi_luc_trai_khong_kinh': 'Thị lực mắt trái, không kính',
            'thi_luc_trai_co_kinh': 'Thị lực mắt trái, có kính',
            'nhan_ap_phai': 'Nhãn áp mắt phải, mmHg', 'nhan_ap_trai': 'Nhãn áp mắt trái, mmHg',
            'sph_phai': 'SPH mắt phải, D', 'cyl_phai': 'CYL mắt phải, D',
            'axis_phai': 'AXIS mắt phải, độ', 'sph_trai': 'SPH mắt trái, D',
            'cyl_trai': 'CYL mắt trái, D', 'axis_trai': 'AXIS mắt trái, độ',
            'ban_phan_truoc': 'Khám bán phần trước', 'day_mat': 'Khám đáy mắt',
            'ghi_nhan_khac': 'Ghi nhận lâm sàng khác', 'bac_si_xac_nhan': 'Bác sĩ xác nhận đơn kính',
            'ghi_chu': 'Ghi chú', 'ten_thuoc': 'Tên thuốc', 'lieu_dung': 'Liều dùng',
            'huong_dan': 'Hướng dẫn sử dụng', 'thoi_gian_dung': 'Thời gian dùng',
        }

        def display(value):
            if value is None or value == '':
                return 'Chưa ghi nhận'
            if value is True:
                return 'Có'
            if value is False:
                return 'Không'
            return str(value)

        def add_section(title, values):
            rows = [{'label': labels.get(key, key.replace('_', ' ').capitalize()), 'value': display(value)}
                    for key, value in values.items()]
            if rows:
                sections.append({'title': title, 'rows': rows})

        add_section('Thông tin khám', snapshot.get('clinical') or {})
        add_section('Kết quả khám mắt', snapshot.get('eye') or {})
        add_section('Đơn kính', snapshot.get('glasses_prescription') or {})

        medicines = snapshot.get('medicine_prescriptions') or []
        if medicines:
            rows = []
            for index, medicine in enumerate(medicines, start=1):
                summary = ' · '.join(
                    f"{labels.get(key, key)}: {display(medicine.get(key))}"
                    for key in ('ten_thuoc', 'lieu_dung', 'huong_dan', 'thoi_gian_dung', 'ghi_chu')
                    if medicine.get(key) not in (None, '')
                )
                rows.append({'label': f'Thuốc {index}', 'value': summary or 'Chưa ghi nhận thông tin'})
            sections.append({'title': 'Đơn thuốc', 'rows': rows})

        services = snapshot.get('performed_services') or []
        if services:
            rows = []
            for service in services:
                status = 'Đã hủy' if service.get('cancelled') else 'Đã xác nhận' if service.get('doctor_confirmed') else 'Chưa xác nhận'
                details = [f"Số lượng: {display(service.get('quantity'))}", f'Trạng thái: {status}']
                if service.get('booked_service'):
                    details.append('Dịch vụ đã đặt trước')
                if service.get('note'):
                    details.append(f"Ghi chú: {display(service.get('note'))}")
                rows.append({'label': display(service.get('service_name')), 'value': ' · '.join(details)})
            sections.append({'title': 'Dịch vụ thực hiện', 'rows': rows})

        images = snapshot.get('images') or []
        if images:
            rows = []
            for index, image in enumerate(images, start=1):
                filename = str(image.get('file') or '').replace('\\', '/').rsplit('/', 1)[-1]
                value = filename or 'Không có tên tệp'
                if image.get('description'):
                    value += f" · {image['description']}"
                rows.append({'label': f'Ảnh {index}', 'value': value})
            sections.append({'title': 'Ảnh hồ sơ', 'rows': rows})
        return sections

    def correction_history_for(exam):
        rows = YeuCauChinhSuaPhieuKham.objects.filter(
            phieu_kham=exam, trang_thai=YeuCauChinhSuaPhieuKham.TrangThai.DA_DUYET,
        ).select_related('nguoi_de_nghi', 'nguoi_duyet').order_by('ngay_tao', 'pk')
        return [{
            'row': row,
            'before_sections': correction_snapshot_sections(row.du_lieu_truoc),
            'after_sections': correction_snapshot_sections(row.du_lieu_sau or row.du_lieu_de_xuat),
        } for row in rows]

    try:
        bac_si = request.user.bac_si
    except BacSi.DoesNotExist:
        bac_si = None

    if bac_si:
        try:
            phieu = get_object_or_404(
                PhieuKham.objects.prefetch_related("hinh_anhs", "dich_vu_thuc_hien__dich_vu", "don_thuocs"),
                id=phieu_id,
                lich_kham__bac_si=bac_si,
            )
        except Http404:
            # Django's technical 404 page replaces handler404 while DEBUG=True.
            # Render the branded not-found page explicitly for out-of-scope exams.
            return render(request, "404.html", status=404)

        edit_logs = LogHeThong.objects.filter(
            model_name="PhieuKham",
            object_id=phieu.id,
            hanh_dong="update",
        ).select_related("nguoi_thuc_hien").order_by("-thoi_gian")

        return render(request, "core/exam_detail.html", {
            "benh_nhan": phieu.benh_nhan,
            "phieu": phieu,
            "edit_logs": edit_logs,
            "correction_history": correction_history_for(phieu),
            "exam_images": list(phieu.hinh_anhs.all()),
            "is_patient_view": False,
            "eye_exam": getattr(phieu, 'chi_tiet_mat', None),
            "glasses_prescription": getattr(phieu, 'don_kinh', None),
            "medicine_prescriptions": list(phieu.don_thuocs.all()),
            **exam_billing_context(phieu),
        })

    benh_nhan = _patient_for_user(request.user)

    if not benh_nhan:
        return render(request, "core/error.html", {
            "msg": "Bạn chưa có hồ sơ bệnh nhân"
        })

    try:
        phieu = get_object_or_404(
            PhieuKham.objects.prefetch_related("hinh_anhs", "dich_vu_thuc_hien__dich_vu", "don_thuocs"),
            id=phieu_id,
            benh_nhan=benh_nhan,
            lich_kham__trang_thai='xong',
        )
    except Http404:
        # Keep the same non-disclosing behavior for patients viewing other records.
        return render(request, "404.html", status=404)

    edit_logs = LogHeThong.objects.filter(
        model_name="PhieuKham",
        object_id=phieu.id,
        hanh_dong="update",
    ).select_related("nguoi_thuc_hien").order_by("-thoi_gian")

    return render(request, "core/exam_detail.html", {
        "benh_nhan": benh_nhan,
        "phieu": phieu,
        "edit_logs": edit_logs,
        "correction_history": correction_history_for(phieu),
        "exam_images": list(phieu.hinh_anhs.all()),
        "is_patient_view": True,
        "eye_exam": getattr(phieu, 'chi_tiet_mat', None),
        "glasses_prescription": getattr(phieu, 'don_kinh', None),
        "medicine_prescriptions": list(phieu.don_thuocs.all()),
        **exam_billing_context(phieu),
    })


# ==========================
# DASHBOARD BÁC SĨ
# ==========================
@login_required
def doctor_dashboard(request):

    try:
        bac_si = request.user.bac_si
    except BacSi.DoesNotExist:
        return redirect("home")


    lich_khams = LichKham.objects.filter(
        bac_si=bac_si
    ).order_by("ngay_kham", "gio_kham")


    return render(request, "core/doctor_dashboard.html", {
        "bac_si": bac_si,
        "lich_khams": lich_khams
    })


# ==========================
# KHÁM BỆNH (TẠO PHIẾU)
# ==========================
@login_required
@never_cache
def doctor_exam(request, lich_id):

    try:
        bac_si = request.user.bac_si
    except BacSi.DoesNotExist:
        return redirect("home")


    lich = get_object_or_404(
        LichKham,
        id=lich_id,
        bac_si=bac_si
    )

    if lich.trang_thai == "huy":
        messages.error(request, "Lịch đã hủy, không thể khám.")
        return redirect("bac_si_home")

    if hasattr(lich, "phieu_kham"):
        if lich.trang_thai == 'dang':
            return redirect('bac_si_phieu_kham_edit', phieu_id=lich.phieu_kham.pk)
        return redirect('phieu_kham_detail', phieu_id=lich.phieu_kham.pk)

    clinical_values = {name + "_value": request.POST.get(name, "") for name in ("trieu_chung", "chan_doan", "huong_dieu_tri")}
    eye_form = ExamEyeForm(request.POST or None, prefix='eye')
    glasses_form = GlassesPrescriptionForm(request.POST or None, prefix='glasses')
    medicine_submitted = 'medicine-TOTAL_FORMS' in request.POST
    medicine_formset = MedicineFormSet(request.POST if medicine_submitted else None, prefix='medicine')
    doctor_services = doctor_form_context(lich, None, request.POST if request.method == 'POST' else None)

    def render_exam():
        return render(request, "core/doctor_exam.html", {"lich": lich, **clinical_values,
            'eye_form': eye_form, 'glasses_form': glasses_form, 'medicine_formset': medicine_formset,
            **doctor_services,
            'exception_reason': request.POST.get('exception_reason', '')})

    if request.method == "POST" and lich.trang_thai == 'dang':
        action = request.POST.get('action', 'complete')
        trieu_chung = request.POST.get("trieu_chung", "").strip()
        chan_doan = request.POST.get("chan_doan", "").strip()
        huong_dieu_tri = request.POST.get("huong_dieu_tri", "").strip()
        uploaded_images = request.FILES.getlist("images")
        eye_valid = eye_form.is_valid()
        glasses_valid = glasses_form.is_valid()
        medicine_valid = not medicine_submitted or medicine_formset.is_valid()
        valid = eye_valid and glasses_valid and medicine_valid
        services_valid = (not doctor_services['doctor_services_submitted']
                          or doctor_services['services_formset'].is_valid())
        valid = valid and services_valid
        if action == 'complete' and not (trieu_chung and chan_doan and huong_dieu_tri):
            messages.error(request, "Để hoàn tất, vui lòng nhập triệu chứng, chẩn đoán và hướng điều trị.")
            valid = False
        if action != 'complete':
            messages.error(request, "Thao tác phiếu khám không hợp lệ.")
            valid = False
        if not valid:
            return render_exam()
        try:
            if valid:
                with transaction.atomic():
                    locked_lich = get_object_or_404(LichKham.objects.select_for_update(), id=lich_id, bac_si=bac_si)
                    if locked_lich.trang_thai != 'dang':
                        raise ValidationError("Chỉ có thể lưu hồ sơ khi lịch đang khám.")
                    if PhieuKham.objects.filter(lich_kham=locked_lich).exists():
                        raise ValidationError("Phiếu khám đã được tạo ở một phiên khác. Vui lòng tải lại.")
                    validate_exam_images(uploaded_images)
                    phieu = PhieuKham.objects.create(
                        benh_nhan=locked_lich.benh_nhan, lich_kham=locked_lich,
                        trieu_chung=trieu_chung, chan_doan=chan_doan, huong_dieu_tri=huong_dieu_tri,
                    )
                    if doctor_services['doctor_services_submitted']:
                        sync_doctor_services(
                            locked_lich.pk, bac_si, request.user, phieu,
                            doctor_services['services_formset'], doctor_services['services_revision'],
                            booked_performed=request.POST.getlist('booked_performed'),
                            remote_addr=request.META.get('REMOTE_ADDR', ''),
                        )
                    eye = eye_form.save(commit=False)
                    if eye_form.has_changed():
                        eye.phieu_kham = phieu
                        eye.save()
                    if glasses_form.has_changed():
                        if glasses_form.cleaned_data.get('bac_si_xac_nhan'):
                            glasses = glasses_form.save(commit=False)
                            glasses.phieu_kham = phieu
                            glasses.save()
                    if medicine_submitted:
                        for medicine in medicine_formset.save(commit=False):
                            medicine.phieu_kham = phieu
                            medicine.save()
                        for medicine in medicine_formset.deleted_objects:
                            medicine.delete()
                    _sync_exam_images(phieu, uploaded_images)
                    transition_exam(locked_lich, request.user, 'xong', request.POST.get('exception_reason', ''))
                    prepare_exam_invoice(phieu.pk, request.user, doctor_completion=True,
                                         remote_addr=request.META.get('REMOTE_ADDR', ''))
        except ValidationError as exc:
            lich.refresh_from_db()
            messages.error(request, ' '.join(exc.messages))
            return render_exam()

        messages.success(request, "Đã hoàn tất khám và chuẩn bị hóa đơn chờ thanh toán từ các dịch vụ đã xác nhận.")
        return redirect("bac_si_home")

    return render_exam()

# ==========================
# ĐĂNG KÝ
# ==========================
def register(request):

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        name = request.POST.get("name", "").strip()
        email = request.POST.get("email", "").strip()

        if not name or not username or not password:
            messages.error(request, "Vui lòng nhập đầy đủ họ tên, tên người dùng và mật khẩu.")
            return redirect("register")

        # Keep phone-number usernames used by existing accounts/clients.
        legacy_phone = bool(re.fullmatch(r"[0-9]{9,15}", username))
        if len(username) > 150 or not (legacy_phone or re.fullmatch(r"(?=.*[A-Za-z])(?=.*[0-9])[A-Za-z0-9]+", username)):
            messages.error(request, "Tên người dùng phải gồm chữ và số không dấu, hoặc số điện thoại.")
            return redirect("register")

        phone = request.POST.get("phone", "").strip() or (username if legacy_phone else "")
        try:
            validate_email(email)
            validate_password(password, user=User(username=username, email=email))
            if User.objects.filter(email__iexact=email).exists():
                raise ValidationError("Email đã được sử dụng.")
            with transaction.atomic():
                user = User(username=username, email=email)
                user.set_password(password)
                user.full_clean()
                user.save()
                patient = BenhNhan(user=user, ho_ten=name, so_dien_thoai=phone,
                                   ngay_sinh="2000-01-01", gioi_tinh="nam", dia_chi="Chưa cập nhật")
                patient.full_clean()
                patient.save()
        except ValidationError as exc:
            for error in exc.messages:
                messages.error(request, error)
            return redirect("register")
        except IntegrityError:
            messages.error(request, "Tài khoản hoặc số điện thoại đã được sử dụng.")
            return redirect("register")

        messages.success(request, "Đăng ký thành công")

        return redirect("login")


    return render(request, "core/register.html")


# ==========================
# ĐĂNG NHẬP
# ==========================
def user_login(request):
    next_url = request.POST.get('next', request.GET.get('next', '')).strip()
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = ''
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        if not username:
            messages.error(request, "Vui lòng nhập tên người dùng.")
            return render(request, "core/login.html", {'next': next_url})

        user = authenticate(
            request,
            username=username,
            password=password
        )

        if user:
            login(request, user)

            if next_url:
                return redirect(next_url)

            if hasattr(user, "le_tan_chi_nhanh") and not user.is_superuser:
                return redirect("reception_dashboard")

            if can_admin(user):
                return redirect("custom_admin_dashboard")

            if can_collect(user):
                return redirect("payment_list")
            # Nếu là bác sĩ
            if hasattr(user, "bac_si"):
                return redirect("bac_si_home")

            # Nếu là bệnh nhân
            return redirect("home")

        messages.error(request, "Sai tài khoản hoặc mật khẩu")

    return render(request, "core/login.html", {'next': next_url})


# ==========================
# ĐĂNG XUẤT
# ==========================
@login_required
def custom_admin_dashboard(request):
    if not can_admin(request.user):
        return redirect("home")

    raw_date_from = request.GET.get("date_from", "").strip()
    raw_date_to = request.GET.get("date_to", "").strip()
    date_from = parse_date(raw_date_from) if raw_date_from else None
    date_to = parse_date(raw_date_to) if raw_date_to else None
    filter_error = ""
    if raw_date_from and date_from is None:
        filter_error = "Ngày bắt đầu không hợp lệ; hãy dùng định dạng YYYY-MM-DD."
    if raw_date_to and date_to is None:
        filter_error = "Ngày kết thúc không hợp lệ; hãy dùng định dạng YYYY-MM-DD."
    if filter_error and (raw_date_from and date_from is None or raw_date_to and date_to is None):
        date_from = date_to = None
    if date_from and date_to and date_from > date_to:
        filter_error = "Ngày bắt đầu phải trước hoặc bằng ngày kết thúc."
        date_from = date_to = None

    branch_status = request.GET.get("branch_status", "active")
    if branch_status not in {"active", "history", "all"}:
        branch_status = "active"
        filter_error = filter_error or "Trạng thái chi nhánh không hợp lệ. Đang hiển thị các chi nhánh hoạt động."
    all_branches = ChiNhanh.objects.all()
    if branch_status == "active":
        all_branches = all_branches.filter(dang_hoat_dong=True)
    elif branch_status == "history":
        all_branches = all_branches.filter(dang_hoat_dong=False)
    all_branches = all_branches.order_by("-dang_hoat_dong", "ten", "pk")
    raw_branch = request.GET.get("branch", "").strip()
    selected_branch = None
    if raw_branch:
        if raw_branch.isdecimal():
            selected_branch = all_branches.filter(pk=int(raw_branch)).first()
        if selected_branch is None:
            filter_error = "Không tìm thấy chi nhánh đã chọn."
    branch_filter_invalid = bool(raw_branch and selected_branch is None)
    scoped_branches = (
        all_branches.none() if branch_filter_invalid
        else all_branches.filter(pk=selected_branch.pk) if selected_branch
        else all_branches
    )
    branch_rows = list(scoped_branches.values("id", "ten", "dang_hoat_dong"))
    branch_ids = [row["id"] for row in branch_rows]

    doctors_by_branch = dict(
        BacSi.objects.filter(chi_nhanh_id__in=branch_ids)
        .values("chi_nhanh_id").annotate(total=Count("pk"))
        .values_list("chi_nhanh_id", "total")
    )
    services_by_branch = dict(
        DichVuChiNhanh.objects.filter(
            chi_nhanh_id__in=branch_ids,
            chi_nhanh__dang_hoat_dong=True,
            dang_ap_dung=True,
            dich_vu__dang_ap_dung=True,
        ).values("chi_nhanh_id")
        .annotate(total=Count("dich_vu_id", distinct=True))
        .values_list("chi_nhanh_id", "total")
    )

    appointments = LichKham.objects.filter(bac_si__chi_nhanh_id__in=branch_ids)
    if date_from:
        appointments = appointments.filter(ngay_kham__gte=date_from)
    if date_to:
        appointments = appointments.filter(ngay_kham__lte=date_to)
    appointment_rows = list(appointments.values("bac_si__chi_nhanh_id").annotate(
        total=Count("pk"),
        waiting=Count("pk", filter=Q(trang_thai="cho") & ~Q(trang_thai_tiep_nhan="vang_mat")),
        examining=Count("pk", filter=Q(trang_thai="dang")),
        completed=Count("pk", filter=Q(trang_thai="xong")),
        cancelled=Count("pk", filter=Q(trang_thai="huy")),
        absent=Count("pk", filter=Q(trang_thai_tiep_nhan="vang_mat")),
        patients=Count("benh_nhan_id", distinct=True),
    ))
    appointment_by_branch = {row["bac_si__chi_nhanh_id"]: row for row in appointment_rows}

    successful_payments = GiaoDichThanhToan.objects.filter(
        chi_nhanh_id__in=branch_ids,
        trang_thai=GiaoDichThanhToan.TrangThai.THANH_CONG,
        hoa_don__trang_thai=HoaDon.TrangThai.DA_THANH_TOAN,
        hoa_don__so_tien__gt=0,
    )
    if date_from:
        successful_payments = successful_payments.filter(hoa_don__ngay_thanh_toan__date__gte=date_from)
    if date_to:
        successful_payments = successful_payments.filter(hoa_don__ngay_thanh_toan__date__lte=date_to)
    # Payment branch is captured when cash/VNPAY is collected. Do not infer a
    # historical branch from the doctor's current assignment. Deduplicate by
    # invoice and leave conflicting/missing snapshots unattributed.
    successful_payment_rows = successful_payments.values(
        "hoa_don_id", "chi_nhanh_id", "hoa_don__so_tien",
    ).distinct()
    payment_branches_by_invoice = {}
    invoice_amounts = {}
    for row in successful_payment_rows:
        payment_branches_by_invoice.setdefault(row["hoa_don_id"], set()).add(row["chi_nhanh_id"])
        invoice_amounts[row["hoa_don_id"]] = row["hoa_don__so_tien"]

    invoice_by_branch = {}
    for invoice_id, historical_branches in payment_branches_by_invoice.items():
        if len(historical_branches) != 1:
            continue
        branch_id = next(iter(historical_branches))
        if branch_id not in branch_ids:
            continue
        totals = invoice_by_branch.setdefault(branch_id, {"total": 0, "revenue": 0})
        totals["total"] += 1
        totals["revenue"] += invoice_amounts[invoice_id]

    # Zero-settlement invoices have no successful payment attempt. Their exam
    # history carries the branch snapshot; only unambiguous snapshots count.
    zero_paid_invoices = HoaDon.objects.filter(
        phieu_kham__isnull=False,
        trang_thai=HoaDon.TrangThai.DA_THANH_TOAN,
        so_tien=0,
        phieu_kham__lich_sus__chi_nhanh_id__in=branch_ids,
    )
    if date_from:
        zero_paid_invoices = zero_paid_invoices.filter(ngay_thanh_toan__date__gte=date_from)
    if date_to:
        zero_paid_invoices = zero_paid_invoices.filter(ngay_thanh_toan__date__lte=date_to)
    zero_invoice_rows = zero_paid_invoices.values(
        "pk", "phieu_kham__lich_sus__chi_nhanh_id",
    ).distinct()
    zero_invoice_branches = {}
    for row in zero_invoice_rows:
        zero_invoice_branches.setdefault(row["pk"], set()).add(row["phieu_kham__lich_sus__chi_nhanh_id"])
    for historical_branches in zero_invoice_branches.values():
        if len(historical_branches) != 1:
            continue
        branch_id = next(iter(historical_branches))
        if branch_id in branch_ids:
            invoice_by_branch.setdefault(branch_id, {"total": 0, "revenue": 0})["total"] += 1

    branch_stats = []
    for branch in branch_rows:
        branch_id = branch["id"]
        appt = appointment_by_branch.get(branch_id, {})
        invoices = invoice_by_branch.get(branch_id, {})
        branch_stats.append({
            **branch,
            "doctor_count": doctors_by_branch.get(branch_id, 0),
            "service_count": services_by_branch.get(branch_id, 0),
            "appointment_count": appt.get("total", 0),
            "completed_count": appt.get("completed", 0),
            "patient_count": appt.get("patients", 0),
            "paid_invoice_count": invoices.get("total", 0),
            "revenue": invoices.get("revenue") or 0,
        })

    appointment_totals = {
        key: sum(row.get(key, 0) for row in appointment_rows)
        for key in ("total", "waiting", "examining", "completed", "cancelled", "absent", "patients")
    }
    paid_invoice_count = sum(row["total"] for row in invoice_by_branch.values())
    total_revenue = sum((row["revenue"] for row in invoice_by_branch.values()), 0)
    latest_appointments = appointments.select_related(
        "benh_nhan", "bac_si", "bac_si__chi_nhanh"
    ).order_by("-ngay_tao", "-id")[:10]
    latest_branches = scoped_branches.order_by("-id")[:10]
    latest_doctors = BacSi.objects.filter(chi_nhanh_id__in=branch_ids).select_related(
        "chi_nhanh", "khoa"
    ).order_by("-id")[:10]
    admin_models = [
        {"key": key, "label": cfg["title"], "count": cfg["model"].objects.count()}
        for key, cfg in ADMIN_MODEL_CONFIG.items() if key != "bao-hiem-y-te"
    ]

    context = {
        "stats": {
            "chi_nhanh": sum(1 for row in branch_rows if row["dang_hoat_dong"]),
            "bac_si": sum(row["doctor_count"] for row in branch_stats),
            "dich_vu": sum(row["service_count"] for row in branch_stats),
            "benh_nhan": appointment_totals["patients"],
            "lich_kham": appointment_totals["total"],
            "phieu_kham": appointment_totals["completed"],
            "hoa_don_da_thu": paid_invoice_count,
            "doanh_thu": total_revenue,
        },
        "status_counts": {
            "cho": appointment_totals["waiting"],
            "dang": appointment_totals["examining"],
            "xong": appointment_totals["completed"],
            "huy": appointment_totals["cancelled"],
            "vang_mat": appointment_totals["absent"],
        },
        "branch_stats": branch_stats,
        "branches": all_branches,
        "branch_status": branch_status,
        "selected_branch": selected_branch,
        "date_from": raw_date_from,
        "date_to": raw_date_to,
        "filter_error": filter_error,
        "latest_appointments": latest_appointments,
        "latest_branches": latest_branches,
        "latest_doctors": latest_doctors,
        "admin_models": admin_models,
    }
    return render(request, "core/custom_admin_dashboard.html", context)


ADMIN_MODEL_CONFIG = {
    "noi-dung-gioi-thieu": {
        "title": "Nội dung giới thiệu",
        "model": NoiDungGioiThieu,
        "ordering": ("thu_tu",),
    },
    "chi-nhanh": {
        "title": "Chi nhánh",
        "model": ChiNhanh,
        "form_class": AdminChiNhanhForm,
        "list_display": ("ten", "dang_hoat_dong", "gio_mo", "gio_dong"),
        "search_fields": ("ten", "dia_chi", "phuong", "so_dien_thoai", "email"),
        "list_filter": ("dang_hoat_dong",),
        "ordering": ("-dang_hoat_dong", "ten"),
    },
    "gio-lam-viec-chi-nhanh": {
        "title": "Giờ làm việc chi nhánh",
        "model": GioLamViecChiNhanh,
        "form_class": AdminGioLamViecChiNhanhForm,
        "list_display": ("chi_nhanh", "thu", "gio_mo", "gio_dong", "nghi"),
        "list_filter": ("thu", "nghi", "chi_nhanh"),
        "ordering": ("chi_nhanh", "thu"),
    },
    "dich-vu-chi-nhanh": {
        "title": "Dịch vụ tại chi nhánh",
        "model": DichVuChiNhanh,
        "form_class": AdminDichVuChiNhanhForm,
        "list_display": ("chi_nhanh", "dich_vu", "dang_ap_dung"),
        "search_fields": ("chi_nhanh__ten", "dich_vu__ma_dich_vu", "dich_vu__ten"),
        "list_filter": ("chi_nhanh", "dang_ap_dung"),
        "ordering": ("chi_nhanh__ten", "dich_vu__ma_dich_vu"),
        "allow_delete": False,
    },
    "khoa": {
        "title": "Khoa",
        "model": Khoa,
        "form_class": AdminKhoaForm,
        "list_display": ("ten", "chi_nhanh"),
        "search_fields": ("ten", "chi_nhanh__ten"),
        "list_filter": ("chi_nhanh",),
        "ordering": ("ten",),
    },
    "bac-si": {
        "title": "Bác sĩ",
        "model": BacSi,
        "form_class": AdminBacSiForm,
        "list_display": ("ho_ten", "chuyen_khoa", "khoa", "chi_nhanh", "so_dien_thoai"),
        "search_fields": ("ho_ten", "chuyen_khoa", "so_dien_thoai", "khoa__ten", "chi_nhanh__ten"),
        "list_filter": ("chi_nhanh", "khoa", "chuyen_khoa"),
        "ordering": ("ho_ten",),
    },
    "bac-si-dich-vu": {
        "title": "Dịch vụ bác sĩ được thực hiện",
        "model": BacSiDichVu,
        "form_class": AdminBacSiDichVuForm,
        "list_display": ("bac_si", "dich_vu_chi_nhanh", "dang_ap_dung"),
        "search_fields": ("bac_si__ho_ten", "dich_vu_chi_nhanh__dich_vu__ma_dich_vu",
                          "dich_vu_chi_nhanh__dich_vu__ten", "dich_vu_chi_nhanh__chi_nhanh__ten"),
        "list_filter": ("dang_ap_dung", "dich_vu_chi_nhanh__chi_nhanh"),
        "ordering": ("bac_si__ho_ten", "dich_vu_chi_nhanh__dich_vu__ma_dich_vu"),
        "allow_delete": False,
    },
    "tai-khoan": {
        "title": "Tài khoản",
        "model": User,
        "form_class": AdminUserForm,
        "list_display": ("username", "email", "first_name", "last_name", "is_staff", "is_superuser", "is_active"),
        "search_fields": ("username", "email", "first_name", "last_name"),
        "list_filter": ("is_staff", "is_superuser", "is_active"),
        "ordering": ("-date_joined",),
    },
    "gio-lam-viec-bac-si": {
        "title": "Giờ làm việc bác sĩ",
        "model": GioLamViecBacSi,
        "form_class": AdminGioLamViecBacSiForm,
        "list_display": ("bac_si", "thu", "gio_bat_dau", "gio_ket_thuc", "nghi"),
        "list_filter": ("thu", "nghi", "bac_si"),
        "ordering": ("bac_si", "thu"),
    },
    "benh-nhan": {
        "title": "Bệnh nhân",
        "model": BenhNhan,
        "form_class": AdminBenhNhanForm,
        "list_display": ("ho_ten", "ngay_sinh", "gioi_tinh", "so_dien_thoai"),
        "search_fields": ("ho_ten", "so_dien_thoai", "dia_chi"),
        "list_filter": ("gioi_tinh",),
        "ordering": ("ho_ten",),
    },
    "lich-kham": {
        "title": "Lịch khám",
        "model": LichKham,
        "list_display": ("benh_nhan", "bac_si", "ngay_kham", "gio_kham", "trang_thai"),
        "search_fields": ("benh_nhan__ho_ten", "bac_si__ho_ten", "ghi_chu"),
        "list_filter": ("trang_thai", "ngay_kham", "bac_si", "benh_nhan"),
        "ordering": ("-ngay_kham", "-gio_kham"),
    },
    "phieu-kham": {
        "title": "Phiếu khám",
        "model": PhieuKham,
        "list_display": ("benh_nhan", "lich_kham", "ngay_lap"),
        "search_fields": ("benh_nhan__ho_ten", "chan_doan", "trieu_chung"),
        "list_filter": ("ngay_lap",),
        "ordering": ("-ngay_lap",),
    },
    "phieu-kham-hinh-anh": {
        "title": "Hình ảnh phiếu khám",
        "model": PhieuKhamHinhAnh,
        "list_display": ("phieu_kham", "thu_tu", "ngay_tao"),
        "search_fields": ("phieu_kham__benh_nhan__ho_ten", "mo_ta"),
        "list_filter": ("ngay_tao",),
        "ordering": ("-ngay_tao",),
    },
    "lich-su-kham-benh": {
        "title": "Lịch sử khám bệnh",
        "model": LichSuKhamBenh,
        "list_display": ("benh_nhan", "bac_si", "chi_nhanh", "ngay_kham", "ngay_tao"),
        "search_fields": ("benh_nhan__ho_ten", "bac_si__ho_ten", "chan_doan", "trieu_chung"),
        "list_filter": ("chi_nhanh", "bac_si", "ngay_kham"),
        "ordering": ("-ngay_kham", "-ngay_tao"),
    },
    "log-lich-kham": {
        "read_only": True,
        "title": "Log lịch khám",
        "model": LogLichKham,
        "list_display": ("lich_kham", "hanh_dong", "trang_thai_cu", "trang_thai_moi", "nguoi_thuc_hien", "thoi_gian"),
        "search_fields": ("mo_ta", "lich_kham__benh_nhan__ho_ten", "lich_kham__bac_si__ho_ten"),
        "list_filter": ("hanh_dong", "thoi_gian"),
        "ordering": ("-thoi_gian",),
    },
    "log-he-thong": {
        "read_only": True,
        "title": "Log hệ thống",
        "model": LogHeThong,
        "list_display": ("model_name", "object_id", "hanh_dong", "nguoi_thuc_hien", "thoi_gian"),
        "search_fields": ("model_name", "object_id", "ghi_chu"),
        "list_filter": ("model_name", "hanh_dong", "thoi_gian"),
        "ordering": ("-thoi_gian",),
    },
    "thong-bao": {
        "title": "Thông báo",
        "model": ThongBao,
        "list_display": ("tieu_de", "loai", "nguoi_nhan", "da_doc", "thoi_gian"),
        "search_fields": ("tieu_de", "noi_dung", "nguoi_nhan__username"),
        "list_filter": ("loai", "da_doc", "thoi_gian"),
        "ordering": ("-thoi_gian",),
    },
}


def _admin_access_or_redirect(request):
    if not can_admin(request.user):
        return redirect("home")
    return None


def _require_account_superuser(request, target=None, *, deleting=False):
    """The custom account directory is reserved for superusers only."""
    if not request.user.is_superuser:
        raise PermissionDenied('Chỉ superuser được quản trị tài khoản.')
    if target and target.is_superuser:
        raise PermissionDenied('Không được sửa hoặc xóa tài khoản superuser qua màn hình này.')
    if deleting and target and target.pk == request.user.pk:
        raise PermissionDenied('Không được tự xóa tài khoản đang đăng nhập.')


def _delete_impact(model_key, obj):
    """Counts records that must be preserved before a destructive admin action."""
    if model_key == 'chi-nhanh':
        return [
            ('Khoa', Khoa.objects.filter(chi_nhanh=obj).count()),
            ('Bác sĩ', BacSi.objects.filter(chi_nhanh=obj).count()),
            ('Lịch khám', LichKham.objects.filter(bac_si__chi_nhanh=obj).count()),
            ('Phiếu khám', PhieuKham.objects.filter(lich_kham__bac_si__chi_nhanh=obj).count()),
            ('Ảnh chi nhánh', ChiNhanhHinhAnh.objects.filter(chi_nhanh=obj).count()),
        ]
    if model_key == 'khoa':
        return [
            ('Bác sĩ', BacSi.objects.filter(khoa=obj).count()),
            ('Lịch khám', LichKham.objects.filter(bac_si__khoa=obj).count()),
            ('Phiếu khám', PhieuKham.objects.filter(lich_kham__bac_si__khoa=obj).count()),
        ]
    if model_key == 'bac-si':
        return [
            ('Lịch khám', LichKham.objects.filter(bac_si=obj).count()),
            ('Phiếu khám', PhieuKham.objects.filter(lich_kham__bac_si=obj).count()),
            ('Lịch làm việc', GioLamViecBacSi.objects.filter(bac_si=obj).count()),
        ]
    if model_key == 'tai-khoan':
        doctor = BacSi.objects.filter(user=obj)
        return [
            ('Hồ sơ bác sĩ', doctor.count()),
            ('Lịch khám', LichKham.objects.filter(bac_si__user=obj).count()),
            ('Phiếu khám', PhieuKham.objects.filter(lich_kham__bac_si__user=obj).count()),
        ]
    if model_key == 'lich-kham':
        return [('Phiếu khám', PhieuKham.objects.filter(lich_kham=obj).count()), ('Nhật ký lịch khám', LogLichKham.objects.filter(lich_kham=obj).count())]
    if model_key == 'phieu-kham':
        return [('Dịch vụ thực hiện', DichVuThucHien.objects.filter(phieu_kham=obj).count()), ('Ảnh phiếu khám', PhieuKhamHinhAnh.objects.filter(phieu_kham=obj).count())]
    return []


def _get_admin_config(model_key):
    config = ADMIN_MODEL_CONFIG.get(model_key)
    if not config:
        raise Http404("Model không tồn tại")
    return config


def _get_admin_form_class(config):
    form_class = config.get("form_class")
    if form_class:
        return form_class
    return modelform_factory(config["model"], fields="__all__")


def _format_admin_value(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Có" if value else "Không"
    if isinstance(value, datetime.datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.strftime("%d/%m/%Y %H:%M")
    if isinstance(value, datetime.date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, datetime.time):
        return value.strftime("%H:%M")
    return str(value)


def _resolve_path_value(obj, path):
    current = obj
    for part in path.split("__"):
        current = getattr(current, part, None)
        if current is None:
            return None
    return current


ADMIN_FIELD_LABELS = {
    "id": "ID",
    "ten": "Tên",
    "dia_chi": "Địa chỉ",
    "phuong": "Phường",
    "gio_mo": "Giờ mở",
    "gio_dong": "Giờ đóng",
    "chi_nhanh": "Chi nhánh",
    "khoa": "Khoa",
    "bac_si": "Bác sĩ",
    "benh_nhan": "Bệnh nhân",
    "chuyen_khoa": "Chuyên khoa",
    "so_dien_thoai": "Số điện thoại",
    "thu": "Thứ",
    "gio_bat_dau": "Giờ bắt đầu",
    "gio_ket_thuc": "Giờ kết thúc",
    "nghi": "Nghỉ",
    "ngay_sinh": "Ngày sinh",
    "gioi_tinh": "Giới tính",
    "ngay_kham": "Ngày khám",
    "gio_kham": "Giờ khám",
    "trang_thai": "Trạng thái",
    "lich_kham": "Lịch khám",
    "phieu_kham": "Phiếu khám",
    "hinh_anh": "Hình ảnh",
    "mo_ta": "Mô tả",
    "thu_tu": "Thứ tự",
    "ngay_lap": "Ngày lập",
    "ngay_tao": "Ngày tạo",
    "hanh_dong": "Hành động",
    "trang_thai_cu": "Trạng thái cũ",
    "trang_thai_moi": "Trạng thái mới",
    "nguoi_thuc_hien": "Người thực hiện",
    "thoi_gian": "Thời gian",
    "model_name": "Tên model",
    "object_id": "ID đối tượng",
    "ghi_chu": "Ghi chú",
    "tieu_de": "Tiêu đề",
    "loai": "Loại",
    "nguoi_nhan": "Người nhận",
    "da_doc": "Đã đọc",
    "user": "Tài khoản",
    "ho_ten": "Họ tên",
    "username": "Tên đăng nhập",
    "email": "Email",
    "first_name": "Tên",
    "last_name": "Họ",
    "is_staff": "Nhân viên",
    "is_superuser": "Quản trị cao nhất",
    "is_active": "Đang hoạt động",
    "date_joined": "Ngày tạo",
    "password": "Mật khẩu",
    "tieu_de_trang": "Tiêu đề trang",
    "nut_lien_he": "Nút liên hệ",
    "nut_kham_pha": "Nút khám phá",
    "tieu_de_gioi_thieu": "Tiêu đề giới thiệu",
    "noi_dung_gioi_thieu": "Nội dung giới thiệu",
    "tieu_de_van_de": "Tiêu đề vấn đề & mục tiêu",
    "noi_dung_van_de": "Nội dung vấn đề & mục tiêu",
    "tieu_de_chuc_nang": "Tiêu đề chức năng chính",
    "ds_chuc_nang": "Danh sách chức năng",
    "tieu_de_gis": "Tiêu đề thành phần GIS",
    "ds_thanh_phan_gis": "Danh sách thành phần GIS",
    "tieu_de_cong_nghe": "Tiêu đề công nghệ",
    "ds_cong_nghe": "Danh sách công nghệ",
    "tieu_de_cta": "Tiêu đề CTA",
    "mo_ta_cta": "Mô tả CTA",
    "thu_tu": "Thứ tự",
}


def _label_from_path(path):
    parts = path.split("__")
    labels = [ADMIN_FIELD_LABELS.get(part, part.replace("_", " ").capitalize()) for part in parts]
    return " / ".join(labels)


def _list_column_label(model, field_name):
    if "__" in field_name:
        return _label_from_path(field_name)
    try:
        if field_name in ADMIN_FIELD_LABELS:
            return ADMIN_FIELD_LABELS[field_name]
        return str(model._meta.get_field(field_name).verbose_name).replace("_", " ").capitalize()
    except Exception:
        return ADMIN_FIELD_LABELS.get(field_name, field_name.replace("_", " ").capitalize())


def _list_column_value(obj, field_name):
    if "__" not in field_name:
        display_fn = f"get_{field_name}_display"
        if hasattr(obj, display_fn):
            try:
                return _format_admin_value(getattr(obj, display_fn)())
            except Exception:
                pass
    return _format_admin_value(_resolve_path_value(obj, field_name))


def _apply_admin_filters(queryset, model, list_filter, params):
    for filter_name in list_filter:
        raw_value = params.get(filter_name, "").strip()
        if not raw_value:
            continue
        try:
            field = model._meta.get_field(filter_name)
        except Exception:
            continue
        try:
            if isinstance(field, db_models.BooleanField):
                value = raw_value.lower() in {"1", "true", "yes", "co"}
                queryset = queryset.filter(**{filter_name: value})
            elif field.is_relation:
                queryset = queryset.filter(**{f"{filter_name}_id": raw_value})
            elif isinstance(field, db_models.DateTimeField):
                queryset = queryset.filter(**{f"{filter_name}__date": raw_value})
            else:
                queryset = queryset.filter(**{filter_name: raw_value})
        except Exception:
            continue
    return queryset


def _build_filter_meta(model, list_filter, params):
    filter_meta = []
    for filter_name in list_filter:
        try:
            field = model._meta.get_field(filter_name)
        except Exception:
            continue
        selected = params.get(filter_name, "").strip()
        item = {
            "name": filter_name,
            "label": _list_column_label(model, filter_name),
            "selected": selected,
            "type": "text",
            "options": [],
        }
        if isinstance(field, db_models.BooleanField):
            item["type"] = "select"
            item["options"] = [
                {"value": "1", "label": "Có"},
                {"value": "0", "label": "Không"},
            ]
        elif field.choices:
            item["type"] = "select"
            item["options"] = [{"value": str(v), "label": str(l)} for v, l in field.choices]
        elif field.is_relation:
            item["type"] = "select"
            rel_qs = field.related_model.objects.all().order_by("id")[:300]
            item["options"] = [{"value": str(obj.pk), "label": str(obj)} for obj in rel_qs]
        elif isinstance(field, (db_models.DateField, db_models.DateTimeField)):
            item["type"] = "date"
        filter_meta.append(item)
    return filter_meta


ALLOWED_BRANCH_IMAGE_EXTS = {"jpg", "jpeg", "png", "webp"}
MAX_BRANCH_IMAGE_SIZE_BYTES = 5 * 1024 * 1024


def _parse_int_set(values):
    result = set()
    for value in values:
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _validate_clinic_images(uploaded_images):
    errors = []
    for uploaded in uploaded_images:
        name = uploaded.name or "tep_khong_ten"
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ALLOWED_BRANCH_IMAGE_EXTS:
            errors.append(f"Ảnh '{name}' không đúng định dạng (jpg, jpeg, png, webp).")
            continue
        if uploaded.size and uploaded.size > MAX_BRANCH_IMAGE_SIZE_BYTES:
            errors.append(f"Ảnh '{name}' vượt quá 5MB.")
            continue
        content_type = (getattr(uploaded, "content_type", "") or "").lower()
        if content_type and not content_type.startswith("image/"):
            errors.append(f"Tệp '{name}' không phải ảnh hợp lệ.")
    return errors


def _validate_exam_images(uploaded_images, *, existing_count=0):
    try:
        validate_exam_images(uploaded_images, existing_count=existing_count)
    except ValidationError as exc:
        return exc.messages
    return []


def _sync_clinic_images(clinic, uploaded_images, delete_ids=None, cover_image_id=None):
    delete_ids = delete_ids or set()

    if delete_ids:
        ChiNhanhHinhAnh.objects.filter(chi_nhanh=clinic, id__in=delete_ids).delete()

    if uploaded_images:
        current_max = (
            ChiNhanhHinhAnh.objects.filter(chi_nhanh=clinic)
            .aggregate(value=Max("thu_tu"))
            .get("value")
            or 0
        )
        new_items = []
        for idx, uploaded in enumerate(uploaded_images, start=1):
            new_items.append(
                ChiNhanhHinhAnh(
                    chi_nhanh=clinic,
                    hinh_anh=uploaded,
                    thu_tu=current_max + idx,
                    la_anh_dai_dien=False,
                )
            )
        ChiNhanhHinhAnh.objects.bulk_create(new_items)

    images = list(
        ChiNhanhHinhAnh.objects.filter(chi_nhanh=clinic).order_by("thu_tu", "id")
    )
    if not images:
        return

    valid_ids = {img.id for img in images}
    target_cover_id = cover_image_id if cover_image_id in valid_ids else None

    if target_cover_id is None:
        current_cover = next((img for img in images if img.la_anh_dai_dien), None)
        if current_cover:
            target_cover_id = current_cover.id
        else:
            target_cover_id = images[0].id

    ChiNhanhHinhAnh.objects.filter(chi_nhanh=clinic).update(la_anh_dai_dien=False)
    ChiNhanhHinhAnh.objects.filter(chi_nhanh=clinic, id=target_cover_id).update(la_anh_dai_dien=True)


@transaction.atomic
def _sync_exam_images(phieu, uploaded_images, delete_ids=None):
    delete_ids = delete_ids or set()
    PhieuKham.objects.select_for_update().get(pk=phieu.pk)
    kept = phieu.hinh_anhs.exclude(pk__in=delete_ids).count()
    validate_exam_images(uploaded_images, existing_count=kept)

    if delete_ids:
        PhieuKhamHinhAnh.objects.filter(phieu_kham=phieu, id__in=delete_ids).delete()

    if not uploaded_images:
        return

    current_max = (
        PhieuKhamHinhAnh.objects.filter(phieu_kham=phieu)
        .aggregate(value=Max("thu_tu"))
        .get("value")
        or 0
    )

    for idx, uploaded in enumerate(uploaded_images, start=1):
        item = PhieuKhamHinhAnh(
            phieu_kham=phieu,
            hinh_anh=uploaded,
            thu_tu=current_max + idx,
        )
        item.full_clean()
        item.save()


BRANCH_WEEKDAY_CHOICES = [
    (1, "Thứ 2"),
    (2, "Thứ 3"),
    (3, "Thứ 4"),
    (4, "Thứ 5"),
    (5, "Thứ 6"),
    (6, "Thứ 7"),
    (0, "Chủ nhật"),
]


def _parse_hhmm(value):
    if not value:
        return None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    return None


def _build_branch_schedule_rows(request, clinic=None):
    existing = {}
    if clinic and clinic.pk:
        existing = {item.thu: item for item in clinic.gio_lam_viecs.all()}

    fallback_open = clinic.gio_mo if clinic and clinic.gio_mo else datetime.time(7, 0)
    fallback_close = clinic.gio_dong if clinic and clinic.gio_dong else datetime.time(17, 0)

    rows = []
    errors = []
    is_post = request.method == "POST"

    for thu, label in BRANCH_WEEKDAY_CHOICES:
        current = existing.get(thu)
        default_open = current.gio_mo if current else fallback_open
        default_close = current.gio_dong if current else fallback_close
        default_nghi = current.nghi if current else False

        if is_post:
            gio_mo_raw = request.POST.get(f"schedule_{thu}_gio_mo", "").strip() or default_open.strftime("%H:%M")
            gio_dong_raw = request.POST.get(f"schedule_{thu}_gio_dong", "").strip() or default_close.strftime("%H:%M")
            nghi = request.POST.get(f"schedule_{thu}_nghi") in {"1", "on", "true", "True"}
        else:
            gio_mo_raw = default_open.strftime("%H:%M")
            gio_dong_raw = default_close.strftime("%H:%M")
            nghi = default_nghi

        gio_mo = _parse_hhmm(gio_mo_raw)
        gio_dong = _parse_hhmm(gio_dong_raw)

        if not gio_mo or not gio_dong:
            errors.append(f"{label}: Giờ mở/giờ đóng không hợp lệ.")
        elif not nghi and gio_mo >= gio_dong:
            errors.append(f"{label}: Giờ đóng phải sau giờ mở.")

        rows.append({
            "thu": thu,
            "label": label,
            "gio_mo_raw": gio_mo_raw,
            "gio_dong_raw": gio_dong_raw,
            "nghi": nghi,
            "gio_mo": gio_mo,
            "gio_dong": gio_dong,
        })

    return rows, errors


def _save_branch_schedule_rows(clinic, rows):
    for row in rows:
        GioLamViecChiNhanh.objects.update_or_create(
            chi_nhanh=clinic,
            thu=row["thu"],
            defaults={
                "gio_mo": row["gio_mo"],
                "gio_dong": row["gio_dong"],
                "nghi": row["nghi"],
            },
        )


@login_required
def custom_admin_model_list(request, model_key):
    denied = _admin_access_or_redirect(request)
    if denied:
        return denied

    config = _get_admin_config(model_key)
    if model_key == 'tai-khoan':
        _require_account_superuser(request)
    model = config["model"]
    queryset = model.objects.all()
    branch_scope = request.GET.get('branch_scope', 'active') if model_key == 'chi-nhanh' else ''
    if model_key == 'chi-nhanh':
        if branch_scope not in {'active', 'history', 'all'}:
            branch_scope = 'active'
        if branch_scope == 'active':
            queryset = queryset.filter(dang_hoat_dong=True)
        elif branch_scope == 'history':
            queryset = queryset.filter(dang_hoat_dong=False)

    keyword = request.GET.get("q", "").strip()
    search_fields = config.get("search_fields", ())
    if keyword and search_fields:
        search_q = Q()
        for field_name in search_fields:
            search_q |= Q(**{f"{field_name}__icontains": keyword})
        queryset = queryset.filter(search_q)

    list_filter = config.get("list_filter", ())
    if model_key == 'chi-nhanh':
        list_filter = tuple(field for field in list_filter if field != 'dang_hoat_dong')
    queryset = _apply_admin_filters(queryset, model, list_filter, request.GET)

    ordering = config.get("ordering", ("-id",))
    queryset = queryset.order_by(*ordering)

    page_obj = Paginator(queryset, 25).get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)
    query_string = query_params.urlencode()
    list_display = config.get("list_display", ("id",))
    columns = [{"name": c, "label": _list_column_label(model, c)} for c in list_display]
    rows = [
        {
            "id": obj.pk,
            "values": [_list_column_value(obj, col) for col in list_display],
            "can_manage": not config.get('read_only') and not (model_key == 'tai-khoan' and obj.is_superuser),
            "can_delete": (
                not config.get('read_only')
                and config.get('allow_delete', True)
                and not (model_key == 'tai-khoan' and obj.is_superuser)
            ),
        }
        for obj in page_obj.object_list
    ]
    if model_key == 'chi-nhanh':
        rows = [
            {
                'id': obj.pk,
                'ten': obj.ten,
                'dia_chi': obj.dia_chi,
                'phuong': obj.phuong,
                'so_dien_thoai': obj.so_dien_thoai,
                'dang_hoat_dong': obj.dang_hoat_dong,
                'can_manage': True,
                'can_delete': config.get('allow_delete', True),
            }
            for obj in page_obj.object_list
        ]

    context = {
        "title": f"Quản trị {config['title']}",
        "model_key": model_key,
        "model_label": config["title"],
        "branch_scope": branch_scope,
        "read_only": config.get('read_only', False),
        "q": keyword,
        "columns": columns,
        "rows": rows,
        "filter_meta": _build_filter_meta(model, list_filter, request.GET),
        "page_obj": page_obj,
        "query_string": query_string,
        "admin_models": [
            {"key": key, "label": cfg["title"]}
            for key, cfg in ADMIN_MODEL_CONFIG.items() if key != "bao-hiem-y-te"
        ],
    }
    return render(request, "core/custom_admin_model_list.html", context)


@login_required
def custom_admin_model_create(request, model_key):
    denied = _admin_access_or_redirect(request)
    if denied:
        return denied
    config = _get_admin_config(model_key)
    if config.get('read_only'):
        raise PermissionDenied('Nhật ký kiểm toán chỉ được xem.')
    if model_key == 'tai-khoan':
        _require_account_superuser(request)
    form_class = _get_admin_form_class(config)
    is_clinic = model_key == "chi-nhanh"
    is_exam_record = model_key == "phieu-kham"

    form_data = request.POST if request.method == "POST" else (
        request.GET if model_key == 'bac-si-dich-vu' and request.GET else None
    )
    form = form_class(form_data, request.FILES or None)
    schedule_rows = []
    schedule_errors = []
    uploaded_images = []
    upload_errors = []
    exam_uploaded_images = []
    exam_upload_errors = []
    if is_clinic:
        schedule_rows, schedule_errors = _build_branch_schedule_rows(request, None)
        if request.method == "POST":
            uploaded_images = request.FILES.getlist("clinic_images")
            upload_errors = _validate_clinic_images(uploaded_images)
    elif is_exam_record and request.method == "POST":
        exam_uploaded_images = request.FILES.getlist("exam_images")
        exam_upload_errors = _validate_exam_images(exam_uploaded_images)

    if request.method == "POST":
        for err in schedule_errors:
            form.add_error(None, err)
        for err in upload_errors:
            form.add_error(None, err)
        for err in exam_upload_errors:
            form.add_error(None, err)

        if form.is_valid():
            try:
                with transaction.atomic():
                    if is_exam_record:
                        form.instance._audit_user = request.user
                    obj = form.save()
                    if is_clinic:
                        _save_branch_schedule_rows(obj, schedule_rows)
                        _sync_clinic_images(obj, uploaded_images)
                    elif is_exam_record:
                        _sync_exam_images(obj, exam_uploaded_images)
                messages.success(request, f"Đã tạo {config['title'].lower()}.")
                return redirect("custom_admin_model_list", model_key=model_key)
            except ValidationError as exc:
                form.add_error(None, exc)

    return render(request, "core/custom_admin_form.html", {
        "title": f"Tạo {config['title']}",
        "model_key": model_key,
        "form": form,
        "back_href": f"/quan-tri/du-lieu/{model_key}/",
        "show_map_picker": is_clinic,
        "show_clinic_layout": is_clinic,
        "show_branch_schedule": is_clinic,
        "show_clinic_image_manager": is_clinic,
        "show_exam_image_manager": is_exam_record,
        "clinic_images": [],
        "exam_images": [],
        "selected_delete_ids": set(),
        "selected_delete_exam_ids": set(),
        "selected_cover_id": "",
        "schedule_rows": schedule_rows,
        "google_maps_api_key": settings.GOOGLE_MAPS_API_KEY,
        "show_assignment_branch_filter": model_key == 'bac-si-dich-vu' and request.method == 'GET',
        "assignment_branches": active_branch_queryset().order_by('ten'),
        "selected_assignment_branch": form.data.get('chi_nhanh', '') if form.is_bound else '',
    })


@login_required
def custom_admin_model_edit(request, model_key, pk):
    denied = _admin_access_or_redirect(request)
    if denied:
        return denied
    config = _get_admin_config(model_key)
    if config.get('read_only'):
        raise PermissionDenied('Nhật ký kiểm toán chỉ được xem.')
    model = config["model"]
    obj = get_object_or_404(model, pk=pk)
    if model_key == 'tai-khoan':
        _require_account_superuser(request, obj)
    form_class = _get_admin_form_class(config)
    is_clinic = model_key == "chi-nhanh"
    is_exam_record = model_key == "phieu-kham"

    form_data = request.POST if request.method == "POST" else (
        request.GET if model_key == 'bac-si-dich-vu' and request.GET else None
    )
    form = form_class(form_data, request.FILES or None, instance=obj)
    schedule_rows = []
    schedule_errors = []
    uploaded_images = []
    upload_errors = []
    selected_delete_ids = set()
    selected_cover_id = ""
    exam_uploaded_images = []
    exam_upload_errors = []
    selected_delete_exam_ids = set()
    if is_clinic:
        schedule_rows, schedule_errors = _build_branch_schedule_rows(request, obj)
        if request.method == "POST":
            uploaded_images = request.FILES.getlist("clinic_images")
            upload_errors = _validate_clinic_images(uploaded_images)
            selected_delete_ids = _parse_int_set(request.POST.getlist("delete_image_ids"))
            selected_cover_id_raw = (request.POST.get("cover_image_id") or "").strip()
            if selected_cover_id_raw:
                try:
                    selected_cover_id = int(selected_cover_id_raw)
                except ValueError:
                    selected_cover_id = ""
                    form.add_error(None, "Ảnh đại diện không hợp lệ.")
                if selected_cover_id and selected_cover_id in selected_delete_ids:
                    form.add_error(None, "Không thể chọn ảnh vừa đánh dấu xóa làm ảnh đại diện.")
    elif is_exam_record and request.method == "POST":
        exam_uploaded_images = request.FILES.getlist("exam_images")
        exam_upload_errors = _validate_exam_images(exam_uploaded_images)
        selected_delete_exam_ids = _parse_int_set(request.POST.getlist("delete_exam_image_ids"))

    if request.method == "POST":
        for err in schedule_errors:
            form.add_error(None, err)
        for err in upload_errors:
            form.add_error(None, err)
        for err in exam_upload_errors:
            form.add_error(None, err)

        if form.is_valid():
            try:
                with transaction.atomic():
                    if is_exam_record:
                        form.instance._audit_user = request.user
                    updated_obj = form.save()
                    if is_clinic:
                        _save_branch_schedule_rows(updated_obj, schedule_rows)
                        _sync_clinic_images(
                            updated_obj,
                            uploaded_images,
                            delete_ids=selected_delete_ids,
                            cover_image_id=selected_cover_id if selected_cover_id else None,
                        )
                    elif is_exam_record:
                        _sync_exam_images(
                            updated_obj,
                            exam_uploaded_images,
                            delete_ids=selected_delete_exam_ids,
                        )
                if isinstance(updated_obj, User) and request.user.pk == updated_obj.pk and form.cleaned_data.get("password"):
                    # Keep current admin session alive when changing own password.
                    update_session_auth_hash(request, updated_obj)
                messages.success(request, f"Đã cập nhật {config['title'].lower()}.")
                return redirect("custom_admin_model_list", model_key=model_key)
            except ValidationError as exc:
                form.add_error(None, exc)

    return render(request, "core/custom_admin_form.html", {
        "title": f"Sửa {config['title']}",
        "model_key": model_key,
        "form": form,
        "object_name": str(obj),
        "back_href": f"/quan-tri/du-lieu/{model_key}/",
        "show_map_picker": is_clinic,
        "show_clinic_layout": is_clinic,
        "show_branch_schedule": is_clinic,
        "show_clinic_image_manager": is_clinic,
        "show_exam_image_manager": is_exam_record,
        "clinic_images": list(obj.hinh_anhs.all()) if is_clinic else [],
        "exam_images": list(obj.hinh_anhs.all()) if is_exam_record else [],
        "selected_delete_ids": selected_delete_ids,
        "selected_delete_exam_ids": selected_delete_exam_ids,
        "selected_cover_id": str(selected_cover_id) if selected_cover_id else "",
        "schedule_rows": schedule_rows,
        "google_maps_api_key": settings.GOOGLE_MAPS_API_KEY,
        "show_assignment_branch_filter": model_key == 'bac-si-dich-vu' and request.method == 'GET' and not request.GET,
        "assignment_branches": active_branch_queryset().order_by('ten'),
        "selected_assignment_branch": form.data.get('chi_nhanh', '') if form.is_bound else '',
        "future_branch_appointments": (
            LichKham.objects.filter(bac_si__chi_nhanh=obj, ngay_kham__gte=timezone.localdate())
            .exclude(trang_thai='huy').count() if is_clinic else 0
        ),
        "future_relation_appointments": (
            LichKham.objects.filter(
                ngay_kham__gte=timezone.localdate(),
                dich_vu_dat_truoc_id=(obj.dich_vu_id if model_key == 'dich-vu-chi-nhanh' else obj.dich_vu_chi_nhanh.dich_vu_id),
                **({'bac_si__chi_nhanh_id': obj.chi_nhanh_id} if model_key == 'dich-vu-chi-nhanh' else {'bac_si_id': obj.bac_si_id}),
            ).exclude(trang_thai='huy').count()
            if model_key in ('dich-vu-chi-nhanh', 'bac-si-dich-vu') else 0
        ),
    })


@login_required
def custom_admin_model_delete(request, model_key, pk):
    denied = _admin_access_or_redirect(request)
    if denied:
        return denied
    config = _get_admin_config(model_key)
    if config.get('read_only'):
        raise PermissionDenied('Nhật ký kiểm toán chỉ được xem.')
    if not config.get('allow_delete', True):
        raise PermissionDenied('Quan hệ cần được lưu lịch sử; hãy ngừng áp dụng thay vì xóa.')
    obj = get_object_or_404(config["model"], pk=pk)
    if model_key == 'tai-khoan':
        _require_account_superuser(request, obj, deleting=True)
    try:
        impact = [(label, count) for label, count in _delete_impact(model_key, obj) if count]
        if request.method != "POST":
            return render(request, "core/custom_admin_delete_confirm.html", {
                "title": f"Xóa {config['title'].lower()}", "model_key": model_key,
                "object_name": str(obj), "impact": impact,
                "can_delete": not impact,
                "back_href": f"/quan-tri/du-lieu/{model_key}/",
            })
        obj.delete()
        messages.success(request, f"Đã xóa {config['title'].lower()}.")
    except ProtectedError:
        messages.error(request, "Không thể xóa vì bản ghi đang có dữ liệu liên quan cần được lưu giữ.")
    except Exception as exc:
        messages.error(request, "Không thể xóa bản ghi này.")
    return redirect("custom_admin_model_list", model_key=model_key)


@login_required
def custom_admin_clinics(request):
    return custom_admin_model_list(request, "chi-nhanh")


@login_required
def custom_admin_clinic_create(request):
    return custom_admin_model_create(request, "chi-nhanh")


@login_required
def custom_admin_clinic_edit(request, pk):
    return custom_admin_model_edit(request, "chi-nhanh", pk)


@login_required
def custom_admin_clinic_delete(request, pk):
    return custom_admin_model_delete(request, "chi-nhanh", pk)


@login_required
def custom_admin_departments(request):
    return custom_admin_model_list(request, "khoa")


@login_required
def custom_admin_department_create(request):
    return custom_admin_model_create(request, "khoa")


@login_required
def custom_admin_department_edit(request, pk):
    return custom_admin_model_edit(request, "khoa", pk)


@login_required
def custom_admin_department_delete(request, pk):
    return custom_admin_model_delete(request, "khoa", pk)


@login_required
def custom_admin_doctors(request):
    return custom_admin_model_list(request, "bac-si")


@login_required
def custom_admin_doctor_create(request):
    return custom_admin_model_create(request, "bac-si")


@login_required
def custom_admin_doctor_edit(request, pk):
    return custom_admin_model_edit(request, "bac-si", pk)


@login_required
def custom_admin_doctor_delete(request, pk):
    return custom_admin_model_delete(request, "bac-si", pk)


@login_required
def custom_admin_departments_api(request):
    if not can_admin(request.user):
        return JsonResponse({"results": []}, status=403)

    chi_nhanh_id = (request.GET.get("chi_nhanh_id") or "").strip()
    if not chi_nhanh_id.isdigit():
        return JsonResponse({"results": []})

    departments = (
        Khoa.objects.filter(chi_nhanh_id=int(chi_nhanh_id))
        .order_by("ten")
        .values("id", "ten")
    )
    return JsonResponse({"results": list(departments)})


@require_POST
def user_logout(request):

    logout(request)

    return redirect("login")

# ==========================
# QUÊN MẬT KHẨU (OTP EMAIL)
# ==========================
FORGOT_PASSWORD_SESSION_KEY = "forgot_password_flow"
FORGOT_PASSWORD_OTP_EXPIRE_MINUTES = 10


def _clear_forgot_password_flow(request):
    request.session.pop(FORGOT_PASSWORD_SESSION_KEY, None)


def _mask_email(email):
    if not email or "@" not in email:
        return email
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "*" * (len(local) - 1)
    else:
        masked_local = local[:2] + "*" * (len(local) - 2)
    return f"{masked_local}@{domain}"


def _get_valid_forgot_password_flow(request):
    state = request.session.get(FORGOT_PASSWORD_SESSION_KEY)
    if not state:
        return None

    expires_at_raw = state.get("expires_at")
    if not expires_at_raw:
        _clear_forgot_password_flow(request)
        return None

    try:
        expires_at = datetime.datetime.fromisoformat(expires_at_raw)
    except (TypeError, ValueError):
        _clear_forgot_password_flow(request)
        return None

    if timezone.is_naive(expires_at):
        expires_at = timezone.make_aware(expires_at, timezone.get_current_timezone())

    if timezone.now() > expires_at:
        _clear_forgot_password_flow(request)
        return None

    return state


def _translate_password_validation_message(message):
    mapping = {
        "This password is too short. It must contain at least 8 characters.": "Mật khẩu quá ngắn. Mật khẩu phải có ít nhất 8 ký tự.",
        "This password is too common.": "Mật khẩu quá phổ biến, vui lòng chọn mật khẩu khác an toàn hơn.",
        "This password is entirely numeric.": "Mật khẩu không được chỉ gồm chữ số.",
        "The password is too similar to the username.": "Mật khẩu quá giống với tên người dùng.",
    }
    return mapping.get(message, message)


def forgot_password(request):
    if request.method == "POST":
        step = (request.POST.get("step") or "").strip()

        if step == "restart":
            _clear_forgot_password_flow(request)
            messages.success(request, "Mời bạn nhập lại tên người dùng.")
            return redirect("forgot_password")

        if step == "username":
            username = request.POST.get("username", "").strip()
            if not username:
                messages.error(request, "Vui lòng nhập tên người dùng.")
                return redirect("forgot_password")

            user = User.objects.filter(username=username).first()
            if not user:
                messages.error(request, "Tên người dùng không tồn tại.")
                return redirect("forgot_password")

            email = (user.email or "").strip()
            if not email:
                messages.error(
                    request,
                    "Tài khoản này chưa có email, không thể gửi mã xác thực.",
                )
                return redirect("forgot_password")

            otp_code = f"{secrets.randbelow(1000000):06d}"
            expires_at = timezone.now() + datetime.timedelta(
                minutes=FORGOT_PASSWORD_OTP_EXPIRE_MINUTES
            )

            subject = "[EyeViora] Mã OTP đặt lại mật khẩu"
            message = (
                f"Xin chào {user.username},\n\n"
                f"Mã OTP đặt lại mật khẩu của bạn là: {otp_code}\n"
                f"Mã có hiệu lực trong {FORGOT_PASSWORD_OTP_EXPIRE_MINUTES} phút.\n\n"
                "Nếu bạn không yêu cầu đổi mật khẩu, vui lòng bỏ qua email này."
            )

            try:
                send_mail(
                    subject=subject,
                    message=message,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[email],
                    fail_silently=False,
                )
            except Exception:
                _clear_forgot_password_flow(request)
                messages.error(
                    request,
                    "Không gửi được mã OTP lúc này. Vui lòng thử lại sau.",
                )
                return redirect("forgot_password")

            request.session[FORGOT_PASSWORD_SESSION_KEY] = {
                "user_id": user.id,
                "username": user.username,
                "email": email,
                "otp_code": otp_code,
                "verified": False,
                "expires_at": expires_at.isoformat(),
            }
            messages.success(request, "Đã gửi mã OTP 6 số đến email của bạn.")
            return redirect("forgot_password")

        if step == "otp":
            state = _get_valid_forgot_password_flow(request)
            if not state:
                messages.error(
                    request,
                    "Mã OTP đã hết hạn hoặc chưa được tạo. Vui lòng thực hiện lại.",
                )
                return redirect("forgot_password")

            otp_input = request.POST.get("otp", "").strip()
            if not (otp_input.isdigit() and len(otp_input) == 6):
                messages.error(request, "Vui lòng nhập đúng mã OTP gồm 6 chữ số.")
                return redirect("forgot_password")

            if not secrets.compare_digest(otp_input, str(state.get("otp_code", ""))):
                state["attempts"] = state.get("attempts", 0) + 1
                if state["attempts"] >= 5:
                    _clear_forgot_password_flow(request)
                else:
                    request.session[FORGOT_PASSWORD_SESSION_KEY] = state
                messages.error(request, "Mã OTP không đúng. Sau 5 lần sai, vui lòng yêu cầu mã mới.")
                return redirect("forgot_password")

            state["verified"] = True
            request.session[FORGOT_PASSWORD_SESSION_KEY] = state
            messages.success(request, "Xác thực OTP thành công. Mời bạn nhập mật khẩu mới.")
            return redirect("forgot_password")

        if step == "reset":
            state = _get_valid_forgot_password_flow(request)
            if not state:
                messages.error(
                    request,
                    "Phiên đổi mật khẩu đã hết hạn. Vui lòng thực hiện lại.",
                )
                return redirect("forgot_password")

            if not state.get("verified"):
                messages.error(request, "Vui lòng xác thực OTP trước khi đổi mật khẩu.")
                return redirect("forgot_password")

            new_password = request.POST.get("new_password", "").strip()
            confirm_password = request.POST.get("confirm_password", "").strip()

            if not new_password or not confirm_password:
                messages.error(request, "Vui lòng nhập đầy đủ mật khẩu mới.")
                return redirect("forgot_password")

            if new_password != confirm_password:
                messages.error(request, "Mật khẩu xác nhận không khớp.")
                return redirect("forgot_password")

            user = User.objects.filter(pk=state.get("user_id"), email=state.get("email"), is_active=True).first()
            if not user:
                _clear_forgot_password_flow(request)
                messages.error(request, "Tài khoản đã thay đổi. Vui lòng thực hiện lại.")
                return redirect("forgot_password")
            try:
                validate_password(new_password, user=user)
            except ValidationError as exc:
                for error in exc.messages:
                    messages.error(request, _translate_password_validation_message(error))
                return redirect("forgot_password")

            user.set_password(new_password)
            user.save(update_fields=["password"])
            _clear_forgot_password_flow(request)

            messages.success(request, "Đổi mật khẩu thành công vui lòng đăng nhập")
            return redirect("login")

        messages.error(request, "Yêu cầu không hợp lệ. Vui lòng thử lại.")
        return redirect("forgot_password")

    state = _get_valid_forgot_password_flow(request)
    if not state:
        step = "username"
    elif state.get("verified"):
        step = "reset"
    else:
        step = "otp"

    context = {
        "step": step,
        "flow_username": state.get("username") if state else "",
        "flow_email_masked": _mask_email(state.get("email")) if state else "",
        "otp_expire_minutes": FORGOT_PASSWORD_OTP_EXPIRE_MINUTES,
    }
    return render(request, "core/forgot_password.html", context)

# ==========================
# LIEN HE / GOP Y
# ==========================
def contact_feedback(request):
    initial = {}
    if request.user.is_authenticated:
        full_name = request.user.get_full_name().strip()
        initial["ho_ten"] = full_name or request.user.username
        if request.user.email:
            initial["email"] = request.user.email

    if request.method == "POST":
        form = ContactFeedbackForm(request.POST)
        if form.is_valid():
            cleaned = form.cleaned_data
            recipient = getattr(settings, "CONTACT_RECEIVER_EMAIL", "") or settings.DEFAULT_FROM_EMAIL
            subject = f"[GÓP Ý] {cleaned['chu_de']}"
            message = (
                f"Họ tên: {cleaned['ho_ten']}\n"
                f"Email: {cleaned['email']}\n"
                f"Thời gian: {timezone.localtime().strftime('%d/%m/%Y %H:%M:%S')}\n\n"
                f"Nội dung:\n{cleaned['noi_dung']}"
            )
            try:
                send_mail(
                    subject=subject,
                    message=message,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[recipient],
                    fail_silently=False,
                )
            except Exception:
                messages.error(
                    request,
                    "Không gửi được góp ý lúc này. Vui lòng thử lại sau.",
                )
            else:
                messages.success(
                    request,
                    "Đã gửi góp ý thành công. Cảm ơn bạn!",
                )
                return redirect("contact_feedback")
        else:
            messages.error(request, "Vui lòng kiểm tra lại thông tin góp ý.")
    else:
        form = ContactFeedbackForm(initial=initial)

    return render(request, "core/contact_feedback.html", {"form": form})

# ==========================
# TRANG CHỦ BÁC SĨ
# ==========================
@login_required
def bac_si_home(request):

    try:
        bac_si = request.user.bac_si
    except BacSi.DoesNotExist:
        return redirect("home")

    status_filter = request.GET.get("status", "").strip()
    range_filter = request.GET.get("range", "").strip()
    shift_filter = request.GET.get("shift", "").strip()

    base_qs = bac_si.lich_khams.all()

    lich_khams = base_qs

    today = timezone.localdate()
    stats_qs = base_qs.filter(ngay_kham__gte=today)
    stats = {
        "tong": stats_qs.count(),
        "cho": stats_qs.filter(trang_thai="cho").count(),
        "dang": stats_qs.filter(trang_thai="dang").count(),
        "xong": stats_qs.filter(trang_thai="xong").count(),
        "huy": stats_qs.filter(trang_thai="huy").count(),
    }
    if status_filter in {"cho", "dang", "xong", "huy"}:
        lich_khams = lich_khams.filter(trang_thai=status_filter)
    else:
        status_filter = ""

    if range_filter in {"week", "month"}:
        if range_filter == "week":
            end_date = today + datetime.timedelta(days=7)
        else:
            end_date = today + datetime.timedelta(days=30)
        lich_khams = lich_khams.filter(ngay_kham__gte=today, ngay_kham__lte=end_date)
    else:
        range_filter = ""

    if shift_filter in {"sang", "chieu"}:
        noon = datetime.time(12, 0)
        if shift_filter == "sang":
            lich_khams = lich_khams.filter(gio_kham__lt=noon)
        else:
            lich_khams = lich_khams.filter(gio_kham__gte=noon)
    else:
        shift_filter = ""

    lich_khams = lich_khams.select_related(
        "benh_nhan", "bac_si__chi_nhanh", "bac_si__khoa"
    ).prefetch_related("dich_vu_dat_truoc_nhieu").order_by("-ngay_kham", "-gio_kham")
    phieu_map = {
        phieu.lich_kham_id: phieu
        for phieu in PhieuKham.objects.filter(lich_kham__in=lich_khams)
    }
    for lich in lich_khams:
        lich.phieu_kham_obj = phieu_map.get(lich.id)
        lich.ca_label = "Ca sáng" if lich.gio_kham < datetime.time(12, 0) else "Ca chiều"

    return render(request, "core/doctor_home.html", {
        "bac_si": bac_si,
        "lich_khams": lich_khams,
        "status_filter": status_filter,
        "range_filter": range_filter,
        "shift_filter": shift_filter,
        "stats": stats,
    })


# ==========================
# HỒ SƠ BỆNH NHÂN (BÁC SĨ)
# ==========================
@login_required
def bac_si_patient_detail(request, benh_nhan_id):

    try:
        bac_si = request.user.bac_si
    except BacSi.DoesNotExist:
        return redirect("home")

    benh_nhan = get_object_or_404(BenhNhan, id=benh_nhan_id)

    co_lich = LichKham.objects.filter(
        bac_si=bac_si,
        benh_nhan=benh_nhan,
        trang_thai__in=['cho', 'dang', 'xong'],
    ).exists()
    if not co_lich:
        messages.error(request, "Bác sĩ không có lịch khám với bệnh nhân này.")
        return redirect("bac_si_home")

    lich_su = LichSuKhamBenh.objects.filter(
        benh_nhan=benh_nhan,
        bac_si=bac_si,
        chi_nhanh=bac_si.chi_nhanh,
        phieu_kham__lich_kham__bac_si=bac_si,
        phieu_kham__benh_nhan=benh_nhan,
        phieu_kham__lich_kham__trang_thai='xong',
    ).select_related("bac_si", "chi_nhanh", "phieu_kham").order_by("-ngay_kham", "-ngay_tao")

    return render(request, "core/doctor_patient_detail.html", {
        "bac_si": bac_si,
        "benh_nhan": benh_nhan,
        "lich_su": lich_su,
    })


# ==========================
# BẮT ĐẦU KHÁM (BÁC SĨ)
# ==========================
@login_required
@require_POST
def start_exam(request, lich_id):
    try:
        bac_si = request.user.bac_si
    except BacSi.DoesNotExist:
        return redirect("home")

    try:
        with transaction.atomic():
            lich = get_object_or_404(LichKham.objects.select_for_update(), pk=lich_id, bac_si=bac_si)
            transition_exam(lich, request.user, 'dang', request.POST.get('exception_reason', ''))
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return redirect('doctor_exam', lich_id=lich_id)
    messages.success(request, 'Đã chuyển trạng thái sang đang khám.')
    return redirect('doctor_exam', lich_id=lich_id)


# ==========================
# THÔNG BÁO BÁC SĨ
# ==========================
@login_required
def bac_si_notifications(request):
    try:
        bac_si = request.user.bac_si
    except BacSi.DoesNotExist:
        return redirect("home")

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "mark_all":
            ThongBao.objects.filter(
                nguoi_nhan=request.user,
                da_doc=False,
            ).update(da_doc=True)
            messages.success(request, "Đã đánh dấu tất cả thông báo là đã đọc.")
            return redirect("bac_si_notifications")
        if action == "mark_one":
            tb_id = request.POST.get("tb_id")
            if tb_id:
                ThongBao.objects.filter(
                    nguoi_nhan=request.user,
                    id=tb_id,
                ).update(da_doc=True)
            return redirect("bac_si_notifications")

    thong_baos = list(ThongBao.objects.filter(
        nguoi_nhan=request.user
    ).order_by("-thoi_gian"))
    for notification in thong_baos:
        notification.safe_link = _doctor_notification_link(notification.lien_ket, bac_si)

    return render(request, "core/doctor_notifications.html", {
        "thong_baos": thong_baos,
    })


def _doctor_notification_link(link, doctor):
    # Old notifications used a non-existent /lich-kham/<id>/ route.
    legacy = re.fullmatch(r'/lich-kham/(\d+)/', link or '')
    if legacy:
        link = reverse('doctor_exam', args=[int(legacy.group(1))])
    if not link or not link.startswith('/') or link.startswith('//'):
        return ''
    try:
        match = resolve(link)
    except Resolver404:
        return ''
    if match.url_name == 'doctor_exam':
        appointment = LichKham.objects.filter(pk=match.kwargs['lich_id'], bac_si=doctor).first()
        if not appointment:
            return ''
        exam = PhieuKham.objects.filter(lich_kham=appointment).first()
        if exam:
            return reverse('phieu_kham_detail', args=[exam.pk])
        return link if appointment.trang_thai in ('cho', 'dang') else ''
    if match.url_name == 'phieu_kham_detail':
        return link if PhieuKham.objects.filter(pk=match.kwargs['phieu_id'], lich_kham__bac_si=doctor).exists() else ''
    return ''


def _patient_notification_link(link, patient):
    """Return an authorized, internal destination for a patient notification."""
    parts = urlsplit(link or '')
    if (
        not parts.path.startswith('/')
        or parts.path.startswith('//')
        or parts.scheme
        or parts.netloc
    ):
        return ''
    try:
        match = resolve(parts.path)
    except Resolver404:
        return ''

    if match.url_name == 'upcoming_appointments':
        appointment_ids = parse_qs(parts.query).get('lich', [])
        if not appointment_ids:
            return reverse('upcoming_appointments')
        if len(appointment_ids) != 1 or not appointment_ids[0].isdigit():
            return ''
        appointment = LichKham.objects.filter(
            pk=int(appointment_ids[0]), benh_nhan=patient
        ).first()
        if not appointment:
            return ''
        return f"{reverse('upcoming_appointments')}?lich={appointment.pk}"

    if match.url_name == 'phieu_kham_detail':
        exam = PhieuKham.objects.filter(
            pk=match.kwargs['phieu_id'], benh_nhan=patient
        ).first()
        return reverse('phieu_kham_detail', args=[exam.pk]) if exam else ''

    if match.url_name == 'medical_history':
        return reverse('medical_history')
    return ''
 
# ==========================
# LỊCH LÀM VIỆC BÁC SĨ
# ==========================
@login_required
def bac_si_schedule(request):
    try:
        bac_si = request.user.bac_si
    except BacSi.DoesNotExist:
        return redirect("home")

    thu_labels = [
        (0, "Chủ nhật"),
        (1, "Thứ 2"),
        (2, "Thứ 3"),
        (3, "Thứ 4"),
        (4, "Thứ 5"),
        (5, "Thứ 6"),
        (6, "Thứ 7"),
    ]

    if request.method == "POST":
        schedule_updates = []
        for thu, _ in thu_labels:
            nghi = request.POST.get(f"nghi_{thu}") == "on"
            gio_bat_dau = request.POST.get(f"gio_bat_dau_{thu}", "").strip()
            gio_ket_thuc = request.POST.get(f"gio_ket_thuc_{thu}", "").strip()

            if nghi:
                schedule_updates.append((thu, {'nghi': True, 'gio_bat_dau': datetime.time(0),
                                               'gio_ket_thuc': datetime.time(0)}))
                continue

            if not gio_bat_dau or not gio_ket_thuc:
                messages.error(request, "Vui lòng nhập đầy đủ giờ làm việc.")
                return redirect("bac_si_schedule")

            try:
                start_time = datetime.datetime.strptime(gio_bat_dau, '%H:%M').time()
                end_time = datetime.datetime.strptime(gio_ket_thuc, '%H:%M').time()
            except ValueError:
                messages.error(request, "Định dạng giờ không hợp lệ.")
                return redirect("bac_si_schedule")

            if start_time >= end_time:
                messages.error(request, "Giờ bắt đầu phải nhỏ hơn giờ kết thúc.")
                return redirect("bac_si_schedule")

            schedule_updates.append((thu, {'nghi': False, 'gio_bat_dau': start_time, 'gio_ket_thuc': end_time}))

        with transaction.atomic():
            BacSi.objects.select_for_update().get(pk=bac_si.pk)
            for thu, values in schedule_updates:
                GioLamViecBacSi.objects.update_or_create(bac_si=bac_si, thu=thu, defaults=values)

        messages.success(request, "Đã cập nhật lịch làm việc.")
        return redirect("bac_si_schedule")

    schedules = {
        glv.thu: glv
        for glv in GioLamViecBacSi.objects.filter(bac_si=bac_si)
    }
    schedule_items = []
    for thu, label in thu_labels:
        glv = schedules.get(thu)
        schedule_items.append({
            "thu": thu,
            "label": label,
            "nghi": glv.nghi if glv else False,
            "gio_bat_dau": glv.gio_bat_dau.strftime("%H:%M") if glv and glv.gio_bat_dau else "",
            "gio_ket_thuc": glv.gio_ket_thuc.strftime("%H:%M") if glv and glv.gio_ket_thuc else "",
        })

    return render(request, "core/doctor_schedule.html", {
        "bac_si": bac_si,
        "schedule_items": schedule_items,
    })


# ==========================
# SỬA PHIẾU KHÁM (BÁC SĨ)
# ==========================
@login_required
def bac_si_phieu_kham_edit(request, phieu_id):
    try:
        bac_si = request.user.bac_si
    except BacSi.DoesNotExist:
        return redirect("home")

    phieu = get_object_or_404(
        PhieuKham.objects.prefetch_related("hinh_anhs", "don_thuocs"),
        id=phieu_id, lich_kham__bac_si=bac_si,
    )
    if phieu.lich_kham.trang_thai != 'dang':
        messages.error(request, 'Phiếu khám đã hoàn tất hoặc lịch không còn đang khám; không thể chỉnh sửa.')
        return redirect('phieu_kham_detail', phieu_id=phieu.pk)

    try:
        eye_instance = phieu.chi_tiet_mat
    except ChiTietKhamMat.DoesNotExist:
        eye_instance = None
    try:
        glasses_instance = phieu.don_kinh
    except DonKinh.DoesNotExist:
        glasses_instance = None
    eye_form = ExamEyeForm(request.POST or None, instance=eye_instance, prefix='eye')
    glasses_form = GlassesPrescriptionForm(request.POST or None, instance=glasses_instance, prefix='glasses')
    medicine_submitted = 'medicine-TOTAL_FORMS' in request.POST
    medicine_formset = MedicineFormSet(request.POST if medicine_submitted else None, instance=phieu, prefix='medicine')
    doctor_services = doctor_form_context(phieu.lich_kham, phieu, request.POST if request.method == 'POST' else None)
    clinical_values = {name + "_value": request.POST.get(name, "") if request.method == "POST" else getattr(phieu, name)
                       for name in ("trieu_chung", "chan_doan", "huong_dieu_tri")}

    def render_exam_edit():
        return render(request, "core/doctor_exam_edit.html", {
            "phieu": phieu, "existing_images": list(phieu.hinh_anhs.all()), **clinical_values,
            'eye_form': eye_form, 'glasses_form': glasses_form, 'medicine_formset': medicine_formset,
            **doctor_services,
        })

    if request.method == "POST":
        action = request.POST.get('action', 'complete')
        trieu_chung = request.POST.get("trieu_chung", "").strip()
        chan_doan = request.POST.get("chan_doan", "").strip()
        huong_dieu_tri = request.POST.get("huong_dieu_tri", "").strip()
        remove_image_ids = _parse_int_set(request.POST.getlist("remove_image_ids"))
        new_images = request.FILES.getlist("new_images")
        valid = eye_form.is_valid() and glasses_form.is_valid() and (not medicine_submitted or medicine_formset.is_valid())
        services_valid = (not doctor_services['doctor_services_submitted']
                          or doctor_services['services_formset'].is_valid())
        valid = valid and services_valid
        if action == 'complete' and not (trieu_chung and chan_doan and huong_dieu_tri):
            messages.error(request, 'Để hoàn tất, vui lòng nhập triệu chứng, chẩn đoán và hướng điều trị.')
            valid = False
        if action != 'complete':
            messages.error(request, 'Thao tác phiếu khám không hợp lệ.')
            valid = False
        if valid:
            try:
                with transaction.atomic():
                    locked_lich = get_object_or_404(LichKham.objects.select_for_update(), pk=phieu.lich_kham_id, bac_si=bac_si)
                    phieu = get_object_or_404(PhieuKham.objects.select_for_update(), pk=phieu_id, lich_kham__bac_si=bac_si)
                    if locked_lich.trang_thai != 'dang':
                        raise ValidationError('Hồ sơ đã khóa hoặc lịch không còn đang khám.')
                    if HoaDon.objects.filter(lich_kham=locked_lich).exists():
                        raise ValidationError('Đã có hóa đơn; hồ sơ đang bị khóa để bảo toàn chi tiết và thanh toán.')
                    kept = phieu.hinh_anhs.exclude(pk__in=remove_image_ids).count()
                    validate_exam_images(new_images, existing_count=kept)
                    phieu.trieu_chung, phieu.chan_doan, phieu.huong_dieu_tri = trieu_chung, chan_doan, huong_dieu_tri
                    phieu._audit_user = request.user
                    phieu.save()
                    if doctor_services['doctor_services_submitted']:
                        sync_doctor_services(
                            locked_lich.pk, bac_si, request.user, phieu,
                            doctor_services['services_formset'], doctor_services['services_revision'],
                            booked_performed=request.POST.getlist('booked_performed'),
                            remote_addr=request.META.get('REMOTE_ADDR', ''),
                        )
                    eye = eye_form.save(commit=False)
                    if eye_form.has_changed() or eye_instance:
                        eye.phieu_kham = phieu
                        eye.save()
                    if glasses_form.has_changed():
                        if glasses_form.cleaned_data.get('bac_si_xac_nhan'):
                            glasses = glasses_form.save(commit=False)
                            glasses.phieu_kham = phieu
                            glasses.save()
                        elif glasses_instance and not any(glasses_form.cleaned_data.get(name) for name in (
                            'sph_phai', 'cyl_phai', 'axis_phai', 'sph_trai', 'cyl_trai', 'axis_trai', 'ghi_chu'
                        )):
                            glasses_instance.delete()
                    if medicine_submitted:
                        for medicine in medicine_formset.save(commit=False):
                            medicine.phieu_kham = phieu
                            medicine.save()
                        for medicine in medicine_formset.deleted_objects:
                            medicine.delete()
                    _sync_exam_images(phieu, new_images, remove_image_ids)
                    transition_exam(locked_lich, request.user, 'xong', request.POST.get('exception_reason', ''))
                    prepare_exam_invoice(phieu.pk, request.user, doctor_completion=True,
                                         remote_addr=request.META.get('REMOTE_ADDR', ''))
            except ValidationError as exc:
                phieu.refresh_from_db()
                messages.error(request, ' '.join(exc.messages))
                return render_exam_edit()
            messages.success(request, 'Đã hoàn tất khám; hồ sơ đã được công bố cho bệnh nhân.')
            return redirect('bac_si_home')

    return render_exam_edit()


@login_required
def profile(request):

    benh_nhan = _patient_for_user(request.user)

    if not benh_nhan:
        return render(request, "core/error.html", {
            "msg": "Không tìm thấy hồ sơ"
        })

    if request.method == "POST":
        action = request.POST.get("action", "update_profile")
        if action == "change_password":
            current_password = request.POST.get("current_password", "")
            new_password = request.POST.get("new_password", "")
            confirm_password = request.POST.get("confirm_password", "")

            if not current_password or not new_password or not confirm_password:
                messages.error(request, "Vui lòng nhập đầy đủ thông tin đổi mật khẩu.")
                return redirect("profile")

            if not request.user.check_password(current_password):
                messages.error(request, "Mật khẩu hiện tại không đúng.")
                return redirect("profile")

            if new_password != confirm_password:
                messages.error(request, "Mật khẩu xác nhận không khớp.")
                return redirect("profile")

            try:
                validate_password(new_password, user=request.user)
            except ValidationError as exc:
                for err in exc.messages:
                    messages.error(request, err)
                return redirect("profile")

            request.user.set_password(new_password)
            request.user.save()
            update_session_auth_hash(request, request.user)
            messages.success(request, "Đổi mật khẩu thành công.")
            return redirect("profile")

        try:
            with transaction.atomic():
                benh_nhan = BenhNhan.objects.select_for_update().get(pk=benh_nhan.pk)
                for field in ("ho_ten", "ngay_sinh", "gioi_tinh", "dia_chi", "so_dien_thoai"):
                    setattr(benh_nhan, field, request.POST.get(field, "").strip())
                # Link legacy profiles on successful update; phone changes do not rename login.
                benh_nhan.user = request.user
                benh_nhan.full_clean()
                benh_nhan.save()
        except ValidationError as exc:
            for error in exc.messages:
                messages.error(request, error)
            return render(request, "core/profile.html", {
                "bn": BenhNhan.objects.get(pk=benh_nhan.pk),
                "profile_values": request.POST,
            })
        except IntegrityError:
            messages.error(request, "Số điện thoại đã được sử dụng.")
            return render(request, "core/profile.html", {
                "bn": BenhNhan.objects.get(pk=benh_nhan.pk),
                "profile_values": request.POST,
            })

        messages.success(request, "Cập nhật thành công")

        return redirect("profile")

    return render(request, "core/profile.html", {
        "bn": benh_nhan
    })


# ==========================
# THÔNG BÁO (BỆNH NHÂN)
# ==========================
@login_required
def notifications(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "mark_all":
            ThongBao.objects.filter(
                nguoi_nhan=request.user,
                da_doc=False,
            ).update(da_doc=True)
            messages.success(request, "Đã đánh dấu tất cả thông báo là đã đọc.")
            return redirect("notifications")
        if action == "mark_one":
            tb_id = request.POST.get("tb_id")
            if tb_id:
                ThongBao.objects.filter(
                    nguoi_nhan=request.user,
                    id=tb_id,
                ).update(da_doc=True)
            return redirect("notifications")

    patient = _patient_for_user(request.user)
    thong_baos = list(ThongBao.objects.filter(
        nguoi_nhan=request.user
    ).order_by("-thoi_gian"))
    for notification in thong_baos:
        notification.safe_link = (
            _patient_notification_link(notification.lien_ket, patient)
            if patient else ''
        )

    return render(request, "core/notifications.html", {
        "thong_baos": thong_baos,
    })


def custom_404(request, exception):
    return render(request, "404.html", status=404)


def custom_404_debug(request):
    return render(request, "404.html", status=404)



