"""Controlled, audited corrections to completed clinical examination notes."""
import json

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.models import (BacSi, BenhNhan, ChiTietKhamMat, DonKinh, LeTanChiNhanh, LichSuKhamBenh, LogHeThong, PhieuKham,
                         YeuCauChinhSuaPhieuKham)
from core.payments.access import can_admin
from core.exam_forms import ExamEyeForm, GlassesPrescriptionForm, MedicineFormSet


class CorrectionRequestForm(forms.Form):
    ly_do = forms.CharField(min_length=10, max_length=1000, label='Lý do nghiệp vụ',
                            widget=forms.Textarea(attrs={'rows': 3}))
    trieu_chung = forms.CharField(max_length=10000, label='Triệu chứng',
                                  widget=forms.Textarea(attrs={'rows': 3}))
    chan_doan = forms.CharField(max_length=10000, label='Chẩn đoán',
                                widget=forms.Textarea(attrs={'rows': 3}))
    huong_dieu_tri = forms.CharField(max_length=10000, label='Hướng điều trị',
                                     widget=forms.Textarea(attrs={'rows': 3}))


def _eye_instance(exam):
    try:
        return exam.chi_tiet_mat
    except ChiTietKhamMat.DoesNotExist:
        return None


def _glasses_instance(exam):
    try:
        return exam.don_kinh
    except DonKinh.DoesNotExist:
        return None


def _json_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _snapshot(exam, eye):
    def fields(instance, excluded):
        if instance is None:
            return None
        return {field.name: _json_value(getattr(instance, field.name))
                for field in instance._meta.fields if field.name not in excluded}

    return {
        'clinical': {key: getattr(exam, key) for key in ('trieu_chung', 'chan_doan', 'huong_dieu_tri')},
        'eye': ({field.name: _json_value(getattr(eye, field.name))
                 for field in eye._meta.fields if field.name not in ('id', 'phieu_kham')}
                if eye else {}),
        'glasses_prescription': fields(getattr(exam, 'don_kinh', None), {'id', 'phieu_kham'}),
        'medicine_prescriptions': [fields(row, {'id', 'phieu_kham'})
                                   for row in exam.don_thuocs.order_by('id')],
        'performed_services': [
            {'service_id': row.dich_vu_id, 'service_name': row.dich_vu.ten,
             'quantity': row.so_luong, 'note': row.ghi_chu, 'doctor_id': row.nguoi_thuc_hien_id,
             'recorded_by_id': row.nguoi_ghi_nhan_id, 'performed_at': _json_value(row.thoi_gian_tao),
             'cancelled': row.da_huy, 'booked_service': row.la_dich_vu_dat_truoc,
             'doctor_confirmed': row.da_xac_nhan_bac_si}
            for row in exam.dich_vu_thuc_hien.select_related('dich_vu').order_by('id')
        ],
        'images': [
            {'file': row.hinh_anh.name, 'description': row.mo_ta, 'order': row.thu_tu,
             'created_at': _json_value(row.ngay_tao)}
            for row in exam.hinh_anhs.order_by('thu_tu', 'id')
        ],
    }


def _doctor_exam_or_404(request, exam_id):
    try:
        doctor = request.user.bac_si
    except BacSi.DoesNotExist:
        raise PermissionDenied
    return get_object_or_404(PhieuKham.objects.select_related('lich_kham__bac_si__chi_nhanh', 'benh_nhan'),
                             pk=exam_id, lich_kham__bac_si=doctor)


@login_required
@require_http_methods(['GET', 'POST'])
def correction_request(request, exam_id):
    exam = _doctor_exam_or_404(request, exam_id)
    if exam.lich_kham.trang_thai != 'xong':
        raise PermissionDenied('Chỉ được đề nghị sửa phiếu khám đã hoàn tất.')
    eye = _eye_instance(exam)
    glasses = _glasses_instance(exam)
    current = _snapshot(exam, eye)
    form = CorrectionRequestForm(request.POST or None, initial={**current['clinical']})
    eye_form = ExamEyeForm(request.POST or None, instance=eye, prefix='eye')
    glasses_submitted = any(key.startswith('glasses-') for key in request.POST)
    glasses_form = GlassesPrescriptionForm(
        request.POST if glasses_submitted else None, instance=glasses, prefix='glasses')
    medicine_submitted = 'medicine-TOTAL_FORMS' in request.POST
    medicine_formset = MedicineFormSet(
        request.POST if medicine_submitted else None, instance=exam, prefix='medicine')
    valid = form.is_valid() and eye_form.is_valid()
    valid = valid and (not glasses_submitted or glasses_form.is_valid())
    valid = valid and (not medicine_submitted or medicine_formset.is_valid())
    if request.method == 'POST' and valid:
        try:
            with transaction.atomic():
                locked_exam = get_object_or_404(
                    PhieuKham.objects.select_for_update().select_related('lich_kham'),
                    pk=exam.pk, lich_kham__bac_si=request.user.bac_si, lich_kham__trang_thai='xong')
                locked_eye = _eye_instance(locked_exam)
                locked_glasses = _glasses_instance(locked_exam)
                locked_form = CorrectionRequestForm(request.POST)
                locked_eye_form = ExamEyeForm(request.POST, instance=locked_eye, prefix='eye')
                locked_glasses_form = GlassesPrescriptionForm(
                    request.POST if glasses_submitted else None,
                    instance=locked_glasses, prefix='glasses')
                locked_medicine_formset = MedicineFormSet(
                    request.POST if medicine_submitted else None,
                    instance=locked_exam, prefix='medicine')
                forms_valid = (locked_form.is_valid() and locked_eye_form.is_valid()
                               and (not glasses_submitted or locked_glasses_form.is_valid())
                               and (not medicine_submitted or locked_medicine_formset.is_valid()))
                if not forms_valid:
                    form, eye_form = locked_form, locked_eye_form
                    glasses_form, medicine_formset = locked_glasses_form, locked_medicine_formset
                else:
                    before = _snapshot(locked_exam, locked_eye)
                    clinical = {
                        key: locked_form.cleaned_data[key]
                        for key in ('trieu_chung', 'chan_doan', 'huong_dieu_tri')
                    }
                    changed = (clinical != before['clinical'] or locked_eye_form.has_changed()
                               or (glasses_submitted and locked_glasses_form.has_changed())
                               or (medicine_submitted and locked_medicine_formset.has_changed()))
                    if not changed:
                        locked_form.add_error(None, 'Nội dung chỉnh sửa không có thay đổi.')
                        form, eye_form = locked_form, locked_eye_form
                        glasses_form, medicine_formset = locked_glasses_form, locked_medicine_formset
                    else:
                        if clinical != before['clinical']:
                            locked_exam.trieu_chung = clinical['trieu_chung']
                            locked_exam.chan_doan = clinical['chan_doan']
                            locked_exam.huong_dieu_tri = clinical['huong_dieu_tri']
                            locked_exam._audit_user = request.user
                            locked_exam.save()
                        new_eye = locked_eye_form.save(commit=False)
                        if locked_eye_form.has_changed() or locked_eye:
                            new_eye.phieu_kham = locked_exam
                            new_eye.full_clean()
                            new_eye.save()
                        if glasses_submitted and locked_glasses_form.has_changed():
                            glasses_values = ('sph_phai', 'cyl_phai', 'axis_phai', 'sph_trai',
                                              'cyl_trai', 'axis_trai', 'ghi_chu')
                            if locked_glasses_form.cleaned_data.get('bac_si_xac_nhan'):
                                saved_glasses = locked_glasses_form.save(commit=False)
                                saved_glasses.phieu_kham = locked_exam
                                saved_glasses.full_clean()
                                saved_glasses.save()
                            elif locked_glasses and not any(
                                locked_glasses_form.cleaned_data.get(name) for name in glasses_values
                            ):
                                locked_glasses.delete()
                        if medicine_submitted:
                            for medicine in locked_medicine_formset.save(commit=False):
                                medicine.phieu_kham = locked_exam
                                medicine.save()
                            for medicine in locked_medicine_formset.deleted_objects:
                                medicine.delete()
                        after = _snapshot(locked_exam, _eye_instance(locked_exam))
                        proposed = after
                        timestamp = timezone.now()
                        history = YeuCauChinhSuaPhieuKham.objects.create(
                            phieu_kham=locked_exam, nguoi_de_nghi=request.user,
                            ly_do=locked_form.cleaned_data['ly_do'].strip(),
                            du_lieu_truoc=before, du_lieu_de_xuat=proposed,
                            trang_thai=YeuCauChinhSuaPhieuKham.TrangThai.DA_DUYET,
                            nguoi_duyet=request.user, du_lieu_sau=after,
                            ngay_xu_ly=timestamp,
                        )
                        LogHeThong.objects.create(
                            model_name='YeuCauChinhSuaPhieuKham', object_id=history.pk,
                            hanh_dong='update', nguoi_thuc_hien=request.user,
                            du_lieu_cu=before, du_lieu_moi=after,
                            ghi_chu=f'Bác sĩ chỉnh sửa phiếu đã hoàn tất. Lý do: {history.ly_do}. '
                                    'Dịch vụ, hóa đơn và giao dịch không bị thay đổi.',
                            ip_address=request.META.get('REMOTE_ADDR'),
                        )
                        messages.success(request, 'Đã lưu chỉnh sửa. Bản cũ và bản mới được giữ trong lịch sử phiếu khám.')
                        return redirect('phieu_kham_detail', phieu_id=locked_exam.pk)
        except IntegrityError:
            form.add_error(None, 'Không thể ghi lịch sử chỉnh sửa. Vui lòng thử lại.')
    return render(request, 'core/exam_corrections/request.html', {
        'exam': exam, 'form': form, 'eye_form': eye_form,
        'glasses_form': glasses_form, 'medicine_formset': medicine_formset,
    })


def _authorized_reviewer(user, branch_id):
    return can_admin(user)


def _reviewable_queryset(user):
    rows = YeuCauChinhSuaPhieuKham.objects.select_related(
        'phieu_kham__benh_nhan', 'phieu_kham__lich_kham__bac_si__chi_nhanh',
        'nguoi_de_nghi', 'nguoi_duyet',
    ).filter(trang_thai=YeuCauChinhSuaPhieuKham.TrangThai.CHO_DUYET)
    if can_admin(user):
        return rows
    raise PermissionDenied('Chỉ quản trị viên mới được xem lịch sử yêu cầu chỉnh sửa cũ.')


@login_required
@require_http_methods(['GET', 'POST'])
def correction_review_list(request):
    rows = _reviewable_queryset(request.user)
    return render(request, 'core/exam_corrections/list.html', {'requests': rows})


@login_required
@require_http_methods(['GET', 'POST'])
def correction_review(request, request_id):
    row = get_object_or_404(_reviewable_queryset(request.user), pk=request_id)
    if request.method == 'POST':
        action = request.POST.get('action')
        note = request.POST.get('ghi_chu_duyet', '').strip()
        if action not in ('approve', 'reject'):
            raise PermissionDenied('Thao tác không hợp lệ.')
        if row.nguoi_de_nghi_id == request.user.pk:
            raise PermissionDenied('Người đề nghị không được tự phê duyệt hoặc từ chối.')
        if action == 'reject' and len(note) < 10:
            messages.error(request, 'Lý do từ chối cần tối thiểu 10 ký tự.')
            return redirect('exam_correction_review', request_id=row.pk)
        try:
            with transaction.atomic():
                locked = get_object_or_404(
                    YeuCauChinhSuaPhieuKham.objects.select_for_update().select_related(
                        'phieu_kham__lich_kham__bac_si__chi_nhanh'), pk=row.pk)
                exam = PhieuKham.objects.select_for_update().get(pk=locked.phieu_kham_id)
                appointment = exam.lich_kham
                if not _authorized_reviewer(request.user, appointment.bac_si.chi_nhanh_id):
                    raise PermissionDenied
                if locked.trang_thai != YeuCauChinhSuaPhieuKham.TrangThai.CHO_DUYET:
                    messages.error(request, 'Yêu cầu đã được xử lý.')
                    return redirect('exam_correction_review', request_id=locked.pk)
                if appointment.trang_thai != 'xong':
                    raise ValidationError('Trạng thái phiếu khám đã thay đổi; không thể duyệt yêu cầu.')
                if action == 'approve':
                    proposed = locked.du_lieu_de_xuat
                    clinical = proposed.get('clinical', {})
                    if set(clinical) != {'trieu_chung', 'chan_doan', 'huong_dieu_tri'}:
                        raise ValidationError('Nội dung đề xuất không hợp lệ.')
                    eye_data = proposed.get('eye', {})
                    eye_form = ExamEyeForm(data={f'eye-{key}': value for key, value in eye_data.items()},
                                           instance=_eye_instance(exam), prefix='eye')
                    if not eye_form.is_valid():
                        raise ValidationError('Dữ liệu khám mắt không còn hợp lệ; không thể duyệt.')
                    before = _snapshot(exam, _eye_instance(exam))
                    stored_before = locked.du_lieu_truoc
                    compatible_legacy_snapshot = (
                        set(stored_before) == {'clinical', 'eye'}
                        and before['clinical'] == stored_before.get('clinical')
                        and before['eye'] == stored_before.get('eye')
                    )
                    if before != stored_before and not compatible_legacy_snapshot:
                        raise ValidationError('Hồ sơ đã thay đổi sau khi gửi yêu cầu; cần lập yêu cầu mới.')
                    exam.trieu_chung = clinical['trieu_chung']
                    exam.chan_doan = clinical['chan_doan']
                    exam.huong_dieu_tri = clinical['huong_dieu_tri']
                    exam._audit_user = request.user
                    exam.save()
                    eye = eye_form.save(commit=False)
                    if eye_form.has_changed() or _eye_instance(exam):
                        eye.phieu_kham = exam
                        eye.full_clean()
                        eye.save()
                    locked.du_lieu_sau = _snapshot(exam, _eye_instance(exam))
                    locked.trang_thai = YeuCauChinhSuaPhieuKham.TrangThai.DA_DUYET
                else:
                    locked.trang_thai = YeuCauChinhSuaPhieuKham.TrangThai.TU_CHOI
                locked.nguoi_duyet = request.user
                locked.ghi_chu_duyet = note
                locked.ngay_xu_ly = timezone.now()
                locked.save(update_fields=['du_lieu_sau', 'trang_thai', 'nguoi_duyet', 'ghi_chu_duyet', 'ngay_xu_ly'])
                LogHeThong.objects.create(
                    model_name='YeuCauChinhSuaPhieuKham', object_id=locked.pk, hanh_dong='update',
                    nguoi_thuc_hien=request.user,
                    du_lieu_cu={'trang_thai': YeuCauChinhSuaPhieuKham.TrangThai.CHO_DUYET},
                    du_lieu_moi={'trang_thai': locked.trang_thai,
                                 'du_lieu_truoc': locked.du_lieu_truoc if action == 'approve' else None,
                                 'du_lieu_sau': locked.du_lieu_sau if action == 'approve' else None,
                                 'ghi_chu_duyet': note},
                    ghi_chu='Xử lý yêu cầu chỉnh sửa hồ sơ chuyên môn; không sửa dữ liệu hóa đơn/giao dịch.',
                    ip_address=request.META.get('REMOTE_ADDR'),
                )
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
            return redirect('exam_correction_review', request_id=row.pk)
        messages.success(request, 'Đã cập nhật kết quả xử lý yêu cầu.')
        return redirect('exam_correction_review_list')
    return render(request, 'core/exam_corrections/review.html', {'item': row})


@login_required
@require_http_methods(['GET', 'POST'])
def cross_branch_history(request):
    try:
        doctor = request.user.bac_si
    except BacSi.DoesNotExist:
        raise PermissionDenied
    if LeTanChiNhanh.objects.filter(user_id=request.user.pk).exists():
        raise PermissionDenied('Tài khoản được phân công lễ tân không được tra cứu hồ sơ liên chi nhánh.')
    if not request.user.has_perm('core.view_cross_branch_medical_history'):
        raise PermissionDenied('Tài khoản bác sĩ chưa được cấp quyền tra cứu liên chi nhánh.')
    patient = None
    history = []
    reason = ''
    phone = ''
    error = ''
    if request.method == 'POST':
        phone = request.POST.get('so_dien_thoai', '').strip()
        reason = request.POST.get('ly_do', '').strip()
        if len(reason) < 20:
            error = 'Lý do nghiệp vụ cần tối thiểu 20 ký tự.'
        elif not phone:
            error = 'Nhập số điện thoại bệnh nhân để tra cứu.'
        else:
            patient = BenhNhan.objects.filter(so_dien_thoai=phone).first()
            if patient:
                history = list(LichSuKhamBenh.objects.filter(
                    benh_nhan=patient, phieu_kham__isnull=False,
                    phieu_kham__lich_kham__trang_thai='xong',
                ).select_related('bac_si', 'chi_nhanh', 'phieu_kham', 'phieu_kham__lich_kham')
                 .prefetch_related('phieu_kham__dich_vu_thuc_hien__dich_vu').order_by('-ngay_kham', '-ngay_tao'))
                LogHeThong.objects.create(
                    model_name='LichSuKhamBenhAccess', object_id=patient.pk, hanh_dong='create',
                    nguoi_thuc_hien=request.user,
                    du_lieu_moi={'doctor_id': doctor.pk, 'doctor_branch_id': doctor.chi_nhanh_id,
                                 'reason': reason, 'result_count': len(history)},
                    ghi_chu='Tra cứu lịch sử khám liên chi nhánh; chỉ ghi metadata truy cập, không sao chép hồ sơ.',
                    ip_address=request.META.get('REMOTE_ADDR'),
                )
            else:
                error = 'Không tìm thấy hồ sơ bệnh nhân với số điện thoại này.'
    return render(request, 'core/cross_branch_history.html', {
        'patient': patient, 'history': history, 'reason': reason, 'phone': phone, 'error': error,
    })

