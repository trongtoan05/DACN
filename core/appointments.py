"""Locked appointment writes and explicit clinical transition policy."""
import datetime

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from .models import (
    BacSi,
    BacSiDichVu,
    BenhNhan,
    ChiNhanh,
    DanhMucDichVu,
    DichVuChiNhanh,
    GioLamViecBacSi,
    GioLamViecChiNhanh,
    LichKham,
    LogHeThong,
)
from .branches import is_official_branch
from .schedule_conflicts import appointment_interval, doctor_absences_for_day, interval_overlaps


def bookable_services_for_branch(branch_id):
    if not is_official_branch(branch_id):
        return DanhMucDichVu.objects.none()
    return DanhMucDichVu.objects.filter(
        dang_ap_dung=True,
        cho_phep_dat_lich=True,
        chi_nhanh_ap_dung__chi_nhanh_id=branch_id,
        chi_nhanh_ap_dung__dang_ap_dung=True,
        chi_nhanh_ap_dung__chi_nhanh__dang_hoat_dong=True,
    ).distinct().order_by('ten', 'ma_dich_vu')


def eligible_doctors_for_service(branch_id, service_id):
    if not is_official_branch(branch_id):
        return BacSi.objects.none()
    return BacSi.objects.filter(
        chi_nhanh_id=branch_id,
        dich_vu_duoc_phep__dang_ap_dung=True,
        dich_vu_duoc_phep__dich_vu_chi_nhanh__chi_nhanh_id=branch_id,
        dich_vu_duoc_phep__dich_vu_chi_nhanh__dang_ap_dung=True,
        dich_vu_duoc_phep__dich_vu_chi_nhanh__dich_vu_id=service_id,
        dich_vu_duoc_phep__dich_vu_chi_nhanh__dich_vu__dang_ap_dung=True,
        dich_vu_duoc_phep__dich_vu_chi_nhanh__dich_vu__cho_phep_dat_lich=True,
        chi_nhanh__dang_hoat_dong=True,
    ).filter(Q(user__isnull=True) | Q(user__is_active=True)).distinct().order_by('ho_ten')


def eligible_doctors_for_services(branch_id, service_ids):
    if not is_official_branch(branch_id):
        return BacSi.objects.none()
    service_ids = list(dict.fromkeys(int(value) for value in service_ids))
    if not service_ids:
        return BacSi.objects.none()
    return BacSi.objects.filter(
        chi_nhanh_id=branch_id,
        dich_vu_duoc_phep__dang_ap_dung=True,
        dich_vu_duoc_phep__dich_vu_chi_nhanh__chi_nhanh_id=branch_id,
        dich_vu_duoc_phep__dich_vu_chi_nhanh__dang_ap_dung=True,
        dich_vu_duoc_phep__dich_vu_chi_nhanh__dich_vu_id__in=service_ids,
        dich_vu_duoc_phep__dich_vu_chi_nhanh__dich_vu__dang_ap_dung=True,
        dich_vu_duoc_phep__dich_vu_chi_nhanh__dich_vu__cho_phep_dat_lich=True,
        chi_nhanh__dang_hoat_dong=True,
    ).filter(Q(user__isnull=True) | Q(user__is_active=True)).annotate(
        matched_services=Count('dich_vu_duoc_phep__dich_vu_chi_nhanh__dich_vu_id', distinct=True)
    ).filter(matched_services=len(service_ids)).order_by('ho_ten')


def booking_slot_options(doctor, day, patient_id=None, service_id=None, exclude_appointment_id=None,
                         service_ids=None, duration_minutes=None):
    """Return working-hour starts with availability flags for booking UI."""
    if not is_official_branch(doctor.chi_nhanh_id) or not doctor.chi_nhanh.dang_hoat_dong:
        return []
    if doctor.user_id and not doctor.user.is_active:
        return []
    requested_services = service_ids or ([service_id] if service_id is not None else [])
    duration = duration_minutes or 30
    services_match = True
    if requested_services:
        eligible = eligible_doctors_for_services(doctor.chi_nhanh_id, requested_services)
        services_match = eligible.filter(pk=doctor.pk).exists()
        if duration_minutes is None:
            duration = sum(DanhMucDichVu.objects.filter(pk__in=requested_services).values_list('thoi_luong_phut', flat=True)) or 30

    weekday = (day.weekday() + 1) % 7
    doctor_hours = GioLamViecBacSi.objects.filter(bac_si=doctor, thu=weekday).first()
    if not doctor_hours or doctor_hours.nghi:
        return []
    opens, closes = doctor_hours.gio_bat_dau, doctor_hours.gio_ket_thuc

    branch_hours = GioLamViecChiNhanh.objects.filter(chi_nhanh=doctor.chi_nhanh, thu=weekday).first()
    if branch_hours:
        if branch_hours.nghi:
            return []
        opens = max(opens, branch_hours.gio_mo)
        closes = min(closes, branch_hours.gio_dong)
    else:
        opens = max(opens, doctor.chi_nhanh.gio_mo)
        closes = min(closes, doctor.chi_nhanh.gio_dong)
    if opens > closes:
        return []

    patient_schedule = LichKham.objects.filter(
        benh_nhan_id=patient_id, ngay_kham=day,
    ).exclude(trang_thai='huy') if patient_id else LichKham.objects.none()
    if exclude_appointment_id:
        patient_schedule = patient_schedule.exclude(pk=exclude_appointment_id)
    patient_has_schedule = patient_schedule.exists()

    occupied_rows = LichKham.objects.filter(
        bac_si=doctor, ngay_kham=day,
    ).exclude(trang_thai='huy')
    if exclude_appointment_id:
        occupied_rows = occupied_rows.exclude(pk=exclude_appointment_id)
    occupied = list(occupied_rows.values_list('gio_kham', 'thoi_luong_du_kien'))
    absences = doctor_absences_for_day(doctor.pk, day)
    first_minute = opens.hour * 60 + opens.minute
    first_minute += (-first_minute) % 30
    last_minute = closes.hour * 60 + closes.minute
    now = timezone.now()
    tz = timezone.get_current_timezone()
    options = []
    for minute in range(first_minute, last_minute + 1, 30):
        slot = datetime.time(minute // 60, minute % 60)
        scheduled = timezone.make_aware(datetime.datetime.combine(day, slot), tz)
        slot_end = scheduled + datetime.timedelta(minutes=duration)
        closing = timezone.make_aware(datetime.datetime.combine(day, closes), tz)
        available = (
            services_match
            and not patient_has_schedule
            and duration <= 480
            and scheduled >= now + datetime.timedelta(minutes=60)
            and slot_end <= closing
            and not any(interval_overlaps(scheduled, slot_end, absence.bat_dau, absence.ket_thuc)
                        for absence in absences)
            and not any(
                interval_overlaps(
                    scheduled,
                    slot_end,
                    timezone.make_aware(datetime.datetime.combine(day, busy_time), tz),
                    timezone.make_aware(datetime.datetime.combine(day, busy_time), tz)
                    + datetime.timedelta(minutes=busy_duration or 30),
                )
                for busy_time, busy_duration in occupied
            )
        )
        options.append((slot, available))
    return options


def available_booking_slots(doctor, day, patient_id=None, service_id=None, exclude_appointment_id=None,
                            service_ids=None, duration_minutes=None):
    """Return only starts whose full requested service duration is available."""
    return [slot for slot, available in booking_slot_options(
        doctor, day, patient_id=patient_id, service_id=service_id,
        exclude_appointment_id=exclude_appointment_id, service_ids=service_ids,
        duration_minutes=duration_minutes,
    ) if available]


def validate_exam_time(appointment, user, reason=''):
    now = timezone.localtime()
    # During the appointment day, reception may receive the patient early;
    # do not force a doctor to wait for the exact time slot.
    if appointment.ngay_kham != now.date():
        if not 10 <= len(reason.strip()) <= 500:
            raise ValidationError('Lịch khác ngày hiện tại cần lý do từ 10 đến 500 ký tự để lưu kiểm toán.')
        return 'reason_required'
    return None


def transition_exam(appointment, user, target, reason=''):
    """Caller holds appointment lock inside atomic, including exam creation."""
    if appointment.trang_thai_tiep_nhan == 'vang_mat':
        raise ValidationError('Lịch đã đánh dấu vắng mặt, không thể bắt đầu khám.')
    if appointment.trang_thai_tiep_nhan != 'da_tiep_nhan':
        raise ValidationError('Lễ tân cần xác nhận bệnh nhân đã đến trước khi bác sĩ bắt đầu khám.')
    if not user.is_active or appointment.bac_si.user_id != user.pk:
        raise PermissionDenied('Chỉ bác sĩ phụ trách được chuyển trạng thái khám.')
    if (appointment.trang_thai, target) not in {('cho', 'dang'), ('dang', 'xong')}:
        raise ValidationError('Chỉ chuyển từ chờ khám → đang khám → đã xong. Hãy tải lại trang.')
    exception = validate_exam_time(appointment, user, reason)
    before = appointment.trang_thai
    appointment.trang_thai = target
    appointment.save(update_fields=['trang_thai', 'ngay_cap_nhat'])
    LogHeThong.objects.create(
        model_name='LichKham', object_id=appointment.pk, hanh_dong='update',
        nguoi_thuc_hien=user, du_lieu_cu={'trang_thai': before},
        du_lieu_moi={'trang_thai': target, 'quyen_ngoai_le': exception},
        ghi_chu=('Ngoại lệ thời gian: ' + reason.strip()) if exception else 'Chuyển trạng thái khám.',
    )


@transaction.atomic
def book_appointment(appointment, selected_services=None):
    # Patient first, doctor second: serializes both one/day and slot validation.
    BenhNhan.objects.select_for_update().get(pk=appointment.benh_nhan_id)
    doctor = BacSi.objects.select_for_update().get(pk=appointment.bac_si_id)
    if not is_official_branch(doctor.chi_nhanh_id) or not doctor.chi_nhanh.dang_hoat_dong:
        raise ValidationError({'chi_nhanh': 'Chi nhánh không nhận lịch mới.'})
    if doctor.user_id:
        User.objects.select_for_update().get(pk=doctor.user_id)
    # Serialize booking against an administrator creating/closing an absence.
    absences = doctor_absences_for_day(doctor.pk, appointment.ngay_kham, lock=True)
    proposed_start, proposed_end = appointment_interval(appointment)
    if any(interval_overlaps(proposed_start, proposed_end, row.bat_dau, row.ket_thuc)
           for row in absences):
        raise ValidationError({'gio_kham': 'Khung giờ bị ảnh hưởng bởi lịch nghỉ đột xuất của bác sĩ.'})
    previous_record = LichKham.objects.filter(pk=appointment.pk).values(
        'thoi_luong_du_kien', 'dich_vu_dat_truoc_id',
    ).first() if appointment.pk else None
    previous_service_ids = set(appointment.dich_vu_dat_truoc_nhieu.values_list('pk', flat=True)) if previous_record else set()
    if previous_record and not previous_service_ids and previous_record['dich_vu_dat_truoc_id']:
        previous_service_ids.add(previous_record['dich_vu_dat_truoc_id'])
    service_ids = sorted({service.pk for service in (selected_services or [])})
    if not service_ids and appointment.dich_vu_dat_truoc_id:
        service_ids = [appointment.dich_vu_dat_truoc_id]
    if service_ids:
        ChiNhanh.objects.select_for_update().get(pk=doctor.chi_nhanh_id)
        locked_services = list(DanhMucDichVu.objects.select_for_update().filter(pk__in=service_ids).order_by('pk'))
        if len(locked_services) != len(service_ids):
            raise ValidationError({'dich_vu_dat_truoc_nhieu': 'Có dịch vụ không còn tồn tại.'})
        assignments = list(DichVuChiNhanh.objects.select_for_update().filter(
            chi_nhanh_id=doctor.chi_nhanh_id, dich_vu_id__in=service_ids,
            dang_ap_dung=True, dich_vu__dang_ap_dung=True, dich_vu__cho_phep_dat_lich=True,
        ).order_by('pk'))
        assignment_by_service = {row.dich_vu_id: row for row in assignments}
        links = list(BacSiDichVu.objects.select_for_update().filter(
            bac_si_id=doctor.pk, dich_vu_chi_nhanh_id__in=[row.pk for row in assignments],
            dang_ap_dung=True,
        ).order_by('pk'))
        linked_service_ids = {row.dich_vu_chi_nhanh.dich_vu_id for row in links}
        if set(assignment_by_service) != set(service_ids) or linked_service_ids != set(service_ids):
            raise ValidationError({'dich_vu_dat_truoc_nhieu': 'Bác sĩ hoặc chi nhánh chưa được phân công một hay nhiều dịch vụ đã chọn.'})
        if appointment.dich_vu_dat_truoc_id not in service_ids:
            appointment.dich_vu_dat_truoc_id = service_ids[0]
        if previous_record and previous_service_ids == set(service_ids):
            appointment.thoi_luong_du_kien = previous_record['thoi_luong_du_kien'] or 30
        else:
            appointment.thoi_luong_du_kien = sum(row.thoi_luong_phut for row in locked_services)
    appointment.full_clean()
    start, end = appointment_interval(appointment)
    other_schedules = LichKham.objects.select_for_update().filter(
        bac_si_id=doctor.pk, ngay_kham=appointment.ngay_kham,
    ).exclude(trang_thai='huy')
    if appointment.pk:
        other_schedules = other_schedules.exclude(pk=appointment.pk)
    if any(interval_overlaps(start, end, *appointment_interval(other)) for other in other_schedules):
        raise ValidationError({'gio_kham': 'Khoảng thời gian khám bị chồng lấn với lịch khác của bác sĩ.'})
    if appointment.thoi_luong_du_kien > 480:
        raise ValidationError({'gio_kham': 'Tổng thời lượng dịch vụ vượt quá giới hạn một lượt khám.'})
    appointment.save()
    if service_ids:
        from .models import DichVuDatTruocLichKham
        current_ids = set(appointment.dich_vu_dat_truoc_nhieu.values_list('pk', flat=True))
        if appointment.pk and current_ids and current_ids != set(service_ids):
            if appointment.trang_thai != 'cho':
                raise ValidationError('Không thể đổi dịch vụ đặt trước sau khi lịch đã bắt đầu.')
        appointment.dich_vu_dat_truoc_nhieu.set(service_ids)
        # Preserve the legacy FK as the first selected service for existing integrations.
        if appointment.dich_vu_dat_truoc_id != service_ids[0]:
            appointment.dich_vu_dat_truoc = locked_services[service_ids.index(appointment.dich_vu_dat_truoc_id)]
            appointment.save(update_fields=['dich_vu_dat_truoc'])
    return appointment
