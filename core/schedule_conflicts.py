"""Shared interval checks for doctor absences and the current 30-minute appointment slot."""
import datetime

from django.utils import timezone

from .models import BacSiNghiDotXuat


LEGACY_APPOINTMENT_MINUTES = 30


def appointment_interval(appointment):
    start = timezone.make_aware(
        datetime.datetime.combine(appointment.ngay_kham, appointment.gio_kham),
        timezone.get_current_timezone(),
    )
    duration = getattr(appointment, 'thoi_luong_du_kien', None) or LEGACY_APPOINTMENT_MINUTES
    return start, start + datetime.timedelta(minutes=duration)


def interval_overlaps(start_a, end_a, start_b, end_b):
    return start_a < end_b and end_a > start_b


def doctor_absences_for_day(doctor_id, day, *, lock=False):
    tz = timezone.get_current_timezone()
    day_start = timezone.make_aware(datetime.datetime.combine(day, datetime.time.min), tz)
    day_end = day_start + datetime.timedelta(days=1)
    rows = BacSiNghiDotXuat.objects.filter(
        bac_si_id=doctor_id,
        dang_ap_dung=True,
        bat_dau__lt=day_end,
        ket_thuc__gt=day_start,
    ).order_by('pk')
    if lock:
        rows = rows.select_for_update()
    return list(rows)


def doctor_is_absent_for_appointment(doctor_id, appointment, *, lock=False):
    start, end = appointment_interval(appointment)
    return any(
        interval_overlaps(start, end, absence.bat_dau, absence.ket_thuc)
        for absence in doctor_absences_for_day(doctor_id, appointment.ngay_kham, lock=lock)
    )


def affected_appointments(absence, *, branch_id=None):
    """Return non-cancelled appointments whose current estimated interval intersects leave."""
    local_start = timezone.localtime(absence.bat_dau)
    local_end = timezone.localtime(absence.ket_thuc)
    filters = {
        'bac_si_id': absence.bac_si_id,
        'ngay_kham__gte': local_start.date() - datetime.timedelta(days=1),
        'ngay_kham__lte': local_end.date() + datetime.timedelta(days=1),
    }
    if branch_id is not None:
        filters['bac_si__chi_nhanh_id'] = branch_id
    rows = absence.bac_si.lich_khams.filter(**filters).exclude(trang_thai='huy').select_related(
        'benh_nhan', 'bac_si', 'bac_si__chi_nhanh', 'hoa_don'
    ).prefetch_related('dich_vu_dat_truoc_nhieu').order_by('ngay_kham', 'gio_kham', 'pk')
    return [row for row in rows if interval_overlaps(*appointment_interval(row), absence.bat_dau, absence.ket_thuc)]
