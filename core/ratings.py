from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.models import BenhNhan, DanhGiaLuotKham, LichKham, LogHeThong
from core.payments.access import can_admin


class PatientRatingForm(forms.ModelForm):
    class Meta:
        model = DanhGiaLuotKham
        fields = ('diem', 'nhan_xet')
        labels = {'diem': 'Mức đánh giá', 'nhan_xet': 'Nhận xét'}
        widgets = {
            'diem': forms.RadioSelect(choices=[(value, f'{value}/5') for value in range(1, 6)]),
            'nhan_xet': forms.Textarea(attrs={'rows': 4, 'maxlength': 1000}),
        }

    def clean_diem(self):
        value = self.cleaned_data['diem']
        if not 1 <= value <= 5:
            raise forms.ValidationError('Điểm đánh giá phải từ 1 đến 5.')
        return value


def _patient_for_account(user):
    patient = BenhNhan.objects.filter(user_id=user.pk).first()
    if patient is None:
        patient = BenhNhan.objects.filter(user__isnull=True, so_dien_thoai=user.username).first()
    return patient


@login_required
@require_http_methods(['GET', 'POST'])
def patient_rating(request, appointment_id):
    patient = _patient_for_account(request.user)
    if patient is None:
        raise PermissionDenied('Tài khoản chưa có hồ sơ bệnh nhân.')
    appointment = get_object_or_404(LichKham.objects.select_related('bac_si__chi_nhanh'),
                                    pk=appointment_id, benh_nhan=patient, trang_thai='xong')
    rating = DanhGiaLuotKham.objects.filter(lich_kham=appointment).first()
    if rating:
        if request.method == 'POST':
            messages.info(request, 'Lượt khám này đã được đánh giá; mỗi lượt chỉ nhận một đánh giá.')
            return redirect('patient_rating', appointment_id=appointment.pk)
        return render(request, 'core/patient_rating.html', {
            'appointment': appointment, 'rating': rating, 'form': None,
        })
    form = PatientRatingForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                locked = get_object_or_404(LichKham.objects.select_for_update(), pk=appointment.pk,
                                           benh_nhan=patient, trang_thai='xong')
                if DanhGiaLuotKham.objects.filter(lich_kham=locked).exists():
                    messages.info(request, 'Lượt khám vừa được đánh giá; mỗi lượt chỉ nhận một đánh giá.')
                else:
                    rating = form.save(commit=False)
                    rating.lich_kham = locked
                    rating.nguoi_danh_gia = request.user
                    rating.full_clean()
                    rating.save()
                    LogHeThong.objects.create(
                        model_name='DanhGiaLuotKham', object_id=rating.pk, hanh_dong='create',
                        nguoi_thuc_hien=request.user,
                        du_lieu_moi={'lich_kham_id': locked.pk, 'diem': rating.diem},
                        ghi_chu='Bệnh nhân gửi đánh giá sau lượt khám hoàn tất.',
                        ip_address=request.META.get('REMOTE_ADDR'),
                    )
                    messages.success(request, 'Cảm ơn bạn đã đánh giá trải nghiệm khám.')
        except IntegrityError:
            messages.info(request, 'Lượt khám vừa được đánh giá; mỗi lượt chỉ nhận một đánh giá.')
        except ValidationError as exc:
            form.add_error(None, exc)
        return redirect('patient_rating', appointment_id=appointment.pk)
    return render(request, 'core/patient_rating.html', {
        'appointment': appointment, 'rating': None, 'form': form,
    })


def _admin_rating_queryset(user):
    rows = DanhGiaLuotKham.objects.select_related(
        'nguoi_danh_gia', 'quan_tri_vien', 'lich_kham__benh_nhan', 'lich_kham__bac_si__chi_nhanh',
    )
    if can_admin(user):
        return rows
    raise PermissionDenied('Chỉ quản trị viên mới được quản lý đánh giá.')


@login_required
@require_http_methods(['GET', 'POST'])
def rating_admin_list(request):
    rows = _admin_rating_queryset(request.user)
    if request.method == 'POST':
        rating_id = request.POST.get('rating_id', '')
        if not rating_id.isascii() or not rating_id.isdigit():
            raise PermissionDenied('Mã đánh giá không hợp lệ.')
        rating = get_object_or_404(rows, pk=int(rating_id))
        reason = request.POST.get('ghi_chu_quan_tri', '').strip()
        if len(reason) < 10:
            messages.error(request, 'Ghi chú quản trị cần tối thiểu 10 ký tự.')
        else:
            with transaction.atomic():
                locked = get_object_or_404(
                    _admin_rating_queryset(request.user).select_for_update(of=('self',)), pk=rating.pk)
                old = {'dang_hien_thi': locked.dang_hien_thi}
                locked.dang_hien_thi = not locked.dang_hien_thi
                locked.ghi_chu_quan_tri = reason
                locked.quan_tri_vien = request.user
                locked.ngay_quan_tri = timezone.now()
                locked.save(update_fields=['dang_hien_thi', 'ghi_chu_quan_tri', 'quan_tri_vien', 'ngay_quan_tri'])
                LogHeThong.objects.create(
                    model_name='DanhGiaLuotKham', object_id=locked.pk, hanh_dong='update',
                    nguoi_thuc_hien=request.user, du_lieu_cu=old,
                    du_lieu_moi={'dang_hien_thi': locked.dang_hien_thi, 'ghi_chu_quan_tri': reason},
                    ghi_chu='Quản trị viên điều chỉnh trạng thái hiển thị đánh giá.',
                    ip_address=request.META.get('REMOTE_ADDR'),
                )
            messages.success(request, 'Đã cập nhật trạng thái hiển thị đánh giá.')
        return redirect('rating_admin_list')
    return render(request, 'core/ratings/admin_list.html', {'ratings': rows})
