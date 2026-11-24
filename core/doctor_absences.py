"""Admin scheduling and branch-scoped handling of appointments affected by doctor leave."""
import datetime

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core.appointments import (available_booking_slots, book_appointment,
                               eligible_doctors_for_services)
from core.branches import active_branch_queryset, is_official_branch
from core.models import BacSi, BacSiNghiDotXuat, LichKham, LogHeThong
from core.payments.access import can_admin, receptionist_profile
from core.schedule_conflicts import affected_appointments, doctor_is_absent_for_appointment
from core.receipts.booking import notify_appointment_change


class DoctorAbsenceForm(forms.ModelForm):
    bac_si = forms.ModelChoiceField(
        queryset=BacSi.objects.none(), label='Bác sĩ',
        widget=forms.Select(attrs={'class': 'admin-input'}),
    )
    bat_dau = forms.DateTimeField(
        label='Bắt đầu nghỉ', input_formats=['%Y-%m-%dT%H:%M'],
        widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local', 'class': 'admin-input'}),
    )
    ket_thuc = forms.DateTimeField(
        label='Kết thúc nghỉ', input_formats=['%Y-%m-%dT%H:%M'],
        widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local', 'class': 'admin-input'}),
    )
    ly_do = forms.CharField(
        min_length=10, max_length=500, label='Lý do ghi nhận',
        widget=forms.Textarea(attrs={'rows': 3, 'maxlength': 500, 'class': 'admin-input'}),
    )

    class Meta:
        model = BacSiNghiDotXuat
        fields = ['bac_si', 'bat_dau', 'ket_thuc', 'ly_do']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['bac_si'].queryset = BacSi.objects.filter(
            chi_nhanh__in=active_branch_queryset(),
        ).select_related('chi_nhanh').order_by('chi_nhanh__ten', 'ho_ten')

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get('bat_dau'), cleaned.get('ket_thuc')
        if start and end and start >= end:
            self.add_error('ket_thuc', 'Thời điểm kết thúc phải sau thời điểm bắt đầu.')
        return cleaned


@login_required
@require_GET
def doctor_absence_admin(request):
    if not can_admin(request.user):
        raise PermissionDenied
    form = DoctorAbsenceForm()
    rows = list(BacSiNghiDotXuat.objects.select_related('bac_si__chi_nhanh', 'nguoi_tao').order_by('-bat_dau')[:100])
    for row in rows:
        row.affected_count = len(affected_appointments(row))
    return render(request, 'core/doctor_absences/admin.html', {'form': form, 'rows': rows})


@login_required
@require_POST
@transaction.atomic
def doctor_absence_create(request):
    if not can_admin(request.user):
        raise PermissionDenied
    form = DoctorAbsenceForm(request.POST)
    if form.is_valid():
        doctor = BacSi.objects.select_for_update().get(pk=form.cleaned_data['bac_si'].pk)
        if not is_official_branch(doctor.chi_nhanh_id) or not doctor.chi_nhanh.dang_hoat_dong:
            form.add_error('bac_si', 'Chỉ lập nghỉ cho bác sĩ đang thuộc chi nhánh EyeViora hoạt động.')
        else:
            absence = form.save(commit=False)
            absence.bac_si = doctor
            absence.nguoi_tao = request.user
            try:
                absence.full_clean()
                absence.save()
                LogHeThong.objects.create(
                    model_name='BacSiNghiDotXuat', object_id=absence.pk, hanh_dong='create',
                    nguoi_thuc_hien=request.user,
                    du_lieu_moi={'bac_si_id': doctor.pk, 'bat_dau': absence.bat_dau.isoformat(),
                                 'ket_thuc': absence.ket_thuc.isoformat(), 'ly_do': absence.ly_do},
                    ghi_chu='Ghi nhận nghỉ đột xuất; lịch bị ảnh hưởng chờ lễ tân xử lý.',
                )
            except (ValidationError, IntegrityError) as exc:
                form.add_error(None, 'Không thể lưu lịch nghỉ: ' + (' '.join(exc.messages) if isinstance(exc, ValidationError) else 'dữ liệu trùng.'))
            else:
                messages.success(request, 'Đã ghi nhận lịch nghỉ. Các lịch bị ảnh hưởng được liệt kê để lễ tân xử lý; hệ thống không tự đổi lịch.')
                return redirect('doctor_absence_admin')
    rows = list(BacSiNghiDotXuat.objects.select_related('bac_si__chi_nhanh', 'nguoi_tao').order_by('-bat_dau')[:100])
    for row in rows:
        row.affected_count = len(affected_appointments(row))
    return render(request, 'core/doctor_absences/admin.html', {'form': form, 'rows': rows}, status=400)


@login_required
@require_POST
@transaction.atomic
def doctor_absence_close(request, pk):
    if not can_admin(request.user):
        raise PermissionDenied
    doctor_id = get_object_or_404(BacSiNghiDotXuat.objects.only('bac_si_id'), pk=pk).bac_si_id
    BacSi.objects.select_for_update().get(pk=doctor_id)
    absence = get_object_or_404(BacSiNghiDotXuat.objects.select_for_update(), pk=pk)
    if absence.dang_ap_dung:
        absence.dang_ap_dung = False
        absence.save(update_fields=['dang_ap_dung', 'ngay_cap_nhat'])
        LogHeThong.objects.create(
            model_name='BacSiNghiDotXuat', object_id=absence.pk, hanh_dong='update',
            nguoi_thuc_hien=request.user, du_lieu_cu={'dang_ap_dung': True},
            du_lieu_moi={'dang_ap_dung': False}, ghi_chu='Đóng lịch nghỉ đột xuất.',
        )
    messages.success(request, 'Đã đóng lịch nghỉ. Không tự khôi phục các lịch đã đổi hoặc hủy trước đó.')
    return redirect('doctor_absence_admin')


@login_required
@require_GET
def affected_appointments_page(request):
    profile = receptionist_profile(request.user)
    if not request.user.is_active or not profile:
        raise PermissionDenied('Tài khoản không có quyền lễ tân.')
    absences = BacSiNghiDotXuat.objects.filter(
        bac_si__chi_nhanh_id=profile.chi_nhanh_id, dang_ap_dung=True,
    ).select_related('bac_si', 'bac_si__chi_nhanh').order_by('bat_dau')
    groups = []
    for absence in absences:
        appointments = affected_appointments(absence, branch_id=profile.chi_nhanh_id)
        for appointment in appointments:
            appointment.can_reassign = False
            appointment.replacement_candidates = []
            if (appointment.trang_thai != 'cho'
                    or appointment.trang_thai_tiep_nhan not in {'cho_xac_nhan', 'da_xac_nhan'}
                    or hasattr(appointment, 'hoa_don')):
                continue
            service_ids = set(appointment.dich_vu_dat_truoc_nhieu.values_list('pk', flat=True))
            if appointment.dich_vu_dat_truoc_id:
                service_ids.add(appointment.dich_vu_dat_truoc_id)
            if not service_ids:
                continue
            candidates = eligible_doctors_for_services(profile.chi_nhanh_id, sorted(service_ids)).exclude(
                pk=appointment.bac_si_id,
            ).select_related('chi_nhanh', 'user')
            for candidate in candidates:
                slots = available_booking_slots(
                    candidate, appointment.ngay_kham, patient_id=appointment.benh_nhan_id,
                    exclude_appointment_id=appointment.pk, service_ids=sorted(service_ids),
                    duration_minutes=appointment.thoi_luong_du_kien,
                )
                if appointment.gio_kham in slots:
                    appointment.replacement_candidates.append(candidate)
            appointment.can_reassign = bool(appointment.replacement_candidates)
        if appointments:
            groups.append({'absence': absence, 'appointments': appointments})
    return render(request, 'core/doctor_absences/affected.html', {'groups': groups, 'branch': profile.chi_nhanh})


@login_required
@require_POST
@transaction.atomic
def reassign_affected_appointment(request, pk):
    profile = receptionist_profile(request.user, lock=True)
    if not request.user.is_active or not profile:
        raise PermissionDenied('Tài khoản không có quyền lễ tân.')
    appointment = get_object_or_404(
        LichKham.objects.select_for_update().select_related('benh_nhan', 'bac_si__chi_nhanh'),
        pk=pk, bac_si__chi_nhanh_id=profile.chi_nhanh_id,
    )
    if (appointment.trang_thai != 'cho'
            or appointment.trang_thai_tiep_nhan not in {'cho_xac_nhan', 'da_xac_nhan'}
            or hasattr(appointment, 'hoa_don')):
        raise PermissionDenied('Chỉ chuyển được lịch chờ xác nhận, chưa tiếp nhận, chưa khám và chưa có hóa đơn.')
    if not doctor_is_absent_for_appointment(appointment.bac_si_id, appointment, lock=True):
        raise PermissionDenied('Lịch này không còn nằm trong khoảng nghỉ đang áp dụng.')
    raw_doctor = request.POST.get('bac_si', '')
    reason = request.POST.get('ly_do', '').strip()
    if not 10 <= len(reason) <= 500:
        messages.error(request, 'Nhập lý do chuyển lịch từ 10 đến 500 ký tự để lưu nhật ký.')
        return redirect('affected_appointments')
    if not raw_doctor.isdigit():
        raise PermissionDenied('Bác sĩ thay thế không hợp lệ.')
    service_ids = set(appointment.dich_vu_dat_truoc_nhieu.values_list('pk', flat=True))
    if appointment.dich_vu_dat_truoc_id:
        service_ids.add(appointment.dich_vu_dat_truoc_id)
    if not service_ids:
        messages.error(request, 'Lịch cũ không có dịch vụ đặt trước; không thể xác minh bác sĩ thay thế an toàn.')
        return redirect('affected_appointments')
    candidates = eligible_doctors_for_services(profile.chi_nhanh_id, sorted(service_ids))
    replacement = candidates.filter(pk=int(raw_doctor)).first()
    if replacement is None or replacement.pk == appointment.bac_si_id:
        messages.error(request, 'Bác sĩ thay thế không được phân công cho toàn bộ dịch vụ tại chi nhánh này.')
        return redirect('affected_appointments')
    selected_services = list(appointment.dich_vu_dat_truoc_nhieu.all().filter(pk__in=service_ids))
    if not selected_services and appointment.dich_vu_dat_truoc_id:
        from core.models import DanhMucDichVu
        selected_services = list(DanhMucDichVu.objects.filter(pk=appointment.dich_vu_dat_truoc_id))
    before = {'bac_si_id': appointment.bac_si_id, 'ngay_kham': str(appointment.ngay_kham),
              'gio_kham': str(appointment.gio_kham)}
    appointment.bac_si = replacement
    try:
        book_appointment(appointment, selected_services=selected_services)
    except (ValidationError, IntegrityError) as exc:
        messages.error(request, 'Không thể chuyển lịch: ' + (' '.join(exc.messages) if isinstance(exc, ValidationError) else 'bác sĩ hoặc khung giờ vừa được phân công.'))
        return redirect('affected_appointments')
    LogHeThong.objects.create(
        model_name='LichKham', object_id=appointment.pk, hanh_dong='update',
        nguoi_thuc_hien=request.user, du_lieu_cu=before,
        du_lieu_moi={'bac_si_id': replacement.pk, 'ngay_kham': str(appointment.ngay_kham),
                     'gio_kham': str(appointment.gio_kham)},
        ghi_chu='Lễ tân chuyển bác sĩ do nghỉ đột xuất; lý do: ' + reason,
    )
    notify_appointment_change(appointment)
    messages.success(request, 'Đã chuyển lịch cho bác sĩ phù hợp còn chỗ và thông báo bệnh nhân.')
    return redirect('affected_appointments')
