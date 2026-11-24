"""Branch-scoped receptionist workflows."""
import datetime
from functools import wraps

from django import forms
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from core.appointments import (available_booking_slots, book_appointment,
    bookable_services_for_branch, eligible_doctors_for_service, eligible_doctors_for_services)
from core.forms import DatLichForm
from core.branches import active_branch_queryset, branch_queryset_with_existing, is_official_branch
from core.models import (BacSi, BenhNhan, ChiNhanh, HoaDon, LeTanChiNhanh,
    LichKham, LogHeThong)
from core.payments.access import can_admin, receptionist_profile as get_receptionist_profile


def receptionist_profile(user, *, lock=False):
    return get_receptionist_profile(user, lock=lock)


def receptionist_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        profile = receptionist_profile(request.user)
        if not request.user.is_active or not profile:
            raise PermissionDenied('Tài khoản không có quyền lễ tân.')
        request.reception_profile = profile
        return view(request, *args, **kwargs)
    return wrapped


class ReceptionAccountForm(forms.ModelForm):
    chi_nhanh = forms.ModelChoiceField(queryset=active_branch_queryset().order_by('ten'), label='Chi nhánh')
    password = forms.CharField(required=False, strip=False, widget=forms.PasswordInput, label='Mật khẩu mới')

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active']

    def __init__(self, *args, profile=None, **kwargs):
        self.profile = profile
        super().__init__(*args, **kwargs)
        self.fields['chi_nhanh'].queryset = active_branch_queryset().order_by('ten')
        self.fields['password'].required = not bool(self.instance.pk)
        if profile:
            self.fields['chi_nhanh'].initial = profile.chi_nhanh_id
            self.fields['chi_nhanh'].queryset = branch_queryset_with_existing(
                self.fields['chi_nhanh'].queryset, profile.chi_nhanh_id,
            )

    def clean(self):
        data = super().clean()
        branch = data.get('chi_nhanh')
        current_profile = LeTanChiNhanh.objects.filter(user=self.instance).first() if self.instance.pk else None
        if branch and not is_official_branch(branch.pk) and (
            not current_profile or current_profile.chi_nhanh_id != branch.pk
        ):
            self.add_error('chi_nhanh', 'Chỉ được phân công lễ tân mới tại 9 chi nhánh EyeViora.')
        if self.instance.pk:
            current = User.objects.get(pk=self.instance.pk)
            if current.is_superuser or current.has_perm('core.quan_tri_he_thong'):
                raise forms.ValidationError('Không thể gán tài khoản Admin làm lễ tân.')
            if hasattr(current, 'bac_si') or hasattr(current, 'benh_nhan'):
                raise forms.ValidationError('Tài khoản đang có vai trò khác, không thể gán làm lễ tân.')
        else:
            username = data.get('username')
            current = User.objects.filter(username=username).first() if username else None
            if current and (current.is_superuser or current.is_staff or hasattr(current, 'bac_si')
                            or hasattr(current, 'benh_nhan')):
                raise forms.ValidationError('Chỉ gán tài khoản nhân viên chưa có vai trò khác.')
        password = data.get('password')
        if password:
            validate_password(password, self.instance)
        return data


@login_required
@require_http_methods(['GET', 'POST'])
@transaction.atomic
def receptionist_accounts(request, pk=None):
    if not can_admin(request.user):
        raise PermissionDenied
    profile = get_object_or_404(LeTanChiNhanh.objects.select_for_update(), pk=pk) if pk else None
    user = User.objects.select_for_update().get(pk=profile.user_id) if profile else User(is_active=True)
    form = ReceptionAccountForm(request.POST or None, instance=user, profile=profile)
    if request.method == 'POST' and form.is_valid():
        account = form.save(commit=False)
        account.is_staff = account.is_superuser = False
        if form.cleaned_data['password']:
            account.set_password(form.cleaned_data['password'])
        account.save()
        profile = profile or LeTanChiNhanh(user=account)
        profile.chi_nhanh = form.cleaned_data['chi_nhanh']
        profile.save()
        LogHeThong.objects.create(model_name='LeTanChiNhanh', object_id=profile.pk,
            hanh_dong='update' if pk else 'create', nguoi_thuc_hien=request.user,
            du_lieu_moi={'user_id': account.pk, 'chi_nhanh_id': profile.chi_nhanh_id})
        messages.success(request, 'Đã lưu tài khoản lễ tân.')
        return redirect('reception_accounts')
    rows = LeTanChiNhanh.objects.select_related('user', 'chi_nhanh').order_by('user__username')
    return render(request, 'core/reception_accounts.html', {'form': form, 'profile': profile,
                   'rows': rows if not pk else []})


@receptionist_required
@require_GET
def reception_dashboard(request):
    profile = request.reception_profile
    today = timezone.localdate()
    base = LichKham.objects.filter(bac_si__chi_nhanh_id=profile.chi_nhanh_id)
    search = request.GET.get('q', '').strip()[:100]
    status = request.GET.get('status', '').strip()
    day_raw = request.GET.get('date', '')
    try:
        day = datetime.date.fromisoformat(day_raw) if day_raw else today
    except ValueError:
        day = today
    rows = base.filter(ngay_kham=day)
    valid_status = {choice[0] for choice in LichKham._meta.get_field('trang_thai_tiep_nhan').choices}
    if status in valid_status:
        rows = rows.filter(trang_thai_tiep_nhan=status)
    else:
        status = ''
    if search:
        rows = rows.filter(Q(benh_nhan__ho_ten__icontains=search) | Q(benh_nhan__so_dien_thoai__icontains=search) | Q(pk__iexact=search) if search.isdigit() else Q(benh_nhan__ho_ten__icontains=search) | Q(benh_nhan__so_dien_thoai__icontains=search))
    stats = {
        'total': base.filter(ngay_kham=today).count(),
        'waiting': base.filter(ngay_kham=today, trang_thai_tiep_nhan__in=['cho_xac_nhan', 'da_xac_nhan']).count(),
        'arrived': base.filter(ngay_kham=today, trang_thai_tiep_nhan='da_tiep_nhan').count(),
        'examining': base.filter(ngay_kham=today, trang_thai='dang').count(),
        'finished': base.filter(ngay_kham=today, trang_thai='xong').count(),
    }
    rows = rows.select_related('benh_nhan', 'bac_si__chi_nhanh', 'dich_vu_dat_truoc').prefetch_related(
        'dich_vu_dat_truoc_nhieu'
    ).order_by('gio_kham', 'pk')
    invoices = base.filter(trang_thai='xong', trang_thai_tiep_nhan='da_tiep_nhan', phieu_kham__isnull=False,
                           hoa_don__isnull=False).filter(
        Q(hoa_don__trang_thai='unpaid') | Q(hoa_don__can_doi_soat=True)
    )
    if search:
        invoice_search = (Q(benh_nhan__ho_ten__icontains=search)
                          | Q(benh_nhan__so_dien_thoai__icontains=search))
        if search.isdigit():
            invoice_search |= Q(pk=int(search)) | Q(hoa_don__pk=int(search))
        invoices = invoices.filter(invoice_search)
    invoices = invoices.select_related('benh_nhan', 'bac_si__chi_nhanh', 'hoa_don').order_by('-ngay_kham', '-pk')
    return render(request, 'core/reception_dashboard.html', {'branch': profile.chi_nhanh,
        'stats': stats, 'page_obj': Paginator(rows, 30).get_page(request.GET.get('page')),
        'date': day, 'search': search, 'status': status,
        'invoice_page': Paginator(invoices, 20).get_page(request.GET.get('invoice_page')),
        'invoice_count': invoices.count()})


@receptionist_required
@require_POST
@transaction.atomic
def reception_transition(request, pk, action):
    profile = receptionist_profile(request.user, lock=True)
    appointment = get_object_or_404(LichKham.objects.select_for_update().select_related('bac_si'),
        pk=pk, bac_si__chi_nhanh_id=profile.chi_nhanh_id)
    before = appointment.trang_thai_tiep_nhan
    now = timezone.localtime()
    if action == 'confirm' and appointment.trang_thai == 'cho' and before == 'cho_xac_nhan':
        appointment.trang_thai_tiep_nhan = 'da_xac_nhan'
    elif action == 'arrive' and appointment.trang_thai == 'cho' and before in {'cho_xac_nhan', 'da_xac_nhan'}:
        appointment.trang_thai_tiep_nhan = 'da_tiep_nhan'
    elif action == 'confirm_after_exam' and appointment.trang_thai == 'xong' and before in {'cho_xac_nhan', 'da_xac_nhan'}:
        if request.POST.get('patient_present') != 'yes':
            messages.error(request, 'Hãy xác nhận bệnh nhân đang có mặt tại quầy trước khi mở thanh toán.')
            return redirect('reception_dashboard')
        appointment.trang_thai_tiep_nhan = 'da_tiep_nhan'
    elif action == 'no_show' and appointment.trang_thai == 'cho' and before in {'cho_xac_nhan', 'da_xac_nhan'}:
        scheduled = timezone.make_aware(datetime.datetime.combine(appointment.ngay_kham, appointment.gio_kham), timezone.get_current_timezone())
        if now < scheduled + datetime.timedelta(minutes=30):
            messages.error(request, 'Chỉ đánh dấu vắng mặt từ 30 phút sau giờ hẹn.')
            return redirect('reception_dashboard')
        appointment.trang_thai_tiep_nhan = 'vang_mat'
    else:
        messages.error(request, 'Trạng thái lịch hiện tại không cho phép thao tác này.')
        return redirect('reception_dashboard')
    appointment.save(update_fields=['trang_thai_tiep_nhan', 'ngay_cap_nhat'])
    LogHeThong.objects.create(model_name='LichKham', object_id=appointment.pk, hanh_dong='update',
        nguoi_thuc_hien=request.user, du_lieu_cu={'trang_thai_tiep_nhan': before},
        du_lieu_moi={'trang_thai_tiep_nhan': appointment.trang_thai_tiep_nhan},
        ghi_chu=('Lễ tân xác nhận bệnh nhân có mặt sau khi khám để mở thu tiền.'
                 if action == 'confirm_after_exam' else 'Thao tác lễ tân'))
    messages.success(request, 'Đã cập nhật tiếp nhận.')
    return redirect('reception_dashboard')


class ReceptionBookingForm(DatLichForm):
    so_dien_thoai = forms.CharField(max_length=15, label='Số điện thoại bệnh nhân')

    def __init__(self, *args, branch, patient=None, **kwargs):
        self.branch = branch
        self.patient = patient
        super().__init__(*args, branches=ChiNhanh.objects.filter(pk=branch.pk), **kwargs)
        self.fields['chi_nhanh'].initial = branch.pk
        self.fields['chi_nhanh'].widget = forms.HiddenInput()
        self.fields['so_dien_thoai'].initial = patient.so_dien_thoai if patient else None
        self.order_fields(['so_dien_thoai', 'chi_nhanh', 'dich_vu_dat_truoc_nhieu', 'bac_si', 'ngay_kham', 'gio_kham', 'ghi_chu'])


def _reception_booking_post_data(request):
    """Keep older receptionist clients working while the form uses the multi-service field."""
    data = request.POST.copy()
    if not data.getlist('dich_vu_dat_truoc_nhieu'):
        legacy_service_ids = data.getlist('dich_vu_dat_truoc')
        if legacy_service_ids:
            data.setlist('dich_vu_dat_truoc_nhieu', legacy_service_ids)
    return data

    def clean_so_dien_thoai(self):
        phone = self.cleaned_data['so_dien_thoai'].strip()
        patient = BenhNhan.objects.filter(so_dien_thoai=phone).first()
        if not patient:
            raise forms.ValidationError('Không tìm thấy bệnh nhân theo số điện thoại; hãy tạo hồ sơ qua quy trình đăng ký hiện có.')
        self.patient = patient
        return phone


@receptionist_required
@require_http_methods(['GET', 'POST'])
@transaction.atomic
def reception_book(request):
    profile = request.reception_profile
    source = request.POST if request.method == 'POST' else request.GET
    services = bookable_services_for_branch(profile.chi_nhanh_id)
    service_raws = source.getlist('dich_vu_dat_truoc_nhieu') or source.getlist('dich_vu_dat_truoc')
    service_ids = list(dict.fromkeys(int(value) for value in service_raws if value.isascii() and value.isdigit()))
    selected_services = list(services.filter(pk__in=service_ids))
    service_ids = [service.pk for service in selected_services]
    doctors = eligible_doctors_for_services(profile.chi_nhanh_id, service_ids) if selected_services else BacSi.objects.none()
    doctor_raw = source.get('bac_si', '')
    doctor = doctors.filter(pk=doctor_raw).first() if doctor_raw.isdigit() else None
    try: day = datetime.date.fromisoformat(source.get('ngay_kham', ''))
    except ValueError: day = None
    patient = BenhNhan.objects.filter(so_dien_thoai=source.get('so_dien_thoai', '').strip()).first()
    slots = available_booking_slots(doctor, day, patient_id=patient.pk, service_ids=service_ids) if doctor and day and patient else []
    form_data = _reception_booking_post_data(request) if request.method == 'POST' else None
    form = ReceptionBookingForm(form_data, branch=profile.chi_nhanh,
        patient=patient, services=services, doctors=doctors, slots=slots)
    if request.method == 'POST' and form.is_valid():
        current_profile = receptionist_profile(request.user, lock=True)
        if not current_profile or current_profile.chi_nhanh_id != profile.chi_nhanh_id:
            raise PermissionDenied('Phân công chi nhánh đã thay đổi; hãy tải lại trang.')
        appointment = form.save(commit=False)
        appointment.benh_nhan = form.patient
        try:
            book_appointment(appointment, selected_services=form.cleaned_data['dich_vu_dat_truoc_nhieu'])
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, 'Không thể đặt lịch: ' + (' '.join(exc.messages) if isinstance(exc, ValidationError) else 'khung giờ vừa được đặt.'))
        else:
            LogHeThong.objects.create(model_name='LichKham', object_id=appointment.pk, hanh_dong='create',
                nguoi_thuc_hien=request.user, du_lieu_moi={'benh_nhan_id': appointment.benh_nhan_id,
                'chi_nhanh_id': profile.chi_nhanh_id, 'nguoi_dat_ho': request.user.pk}, ghi_chu='Lễ tân đặt lịch hộ bệnh nhân')
            messages.success(request, 'Đã đặt lịch hộ bệnh nhân.')
            return redirect('reception_dashboard')
    return render(request, 'core/reception_booking.html', {'form': form, 'branch': profile.chi_nhanh})


@receptionist_required
@require_http_methods(['GET', 'POST'])
def reception_reschedule(request, pk):
    profile = request.reception_profile
    appointment = get_object_or_404(LichKham.objects.select_related('benh_nhan'), pk=pk,
        bac_si__chi_nhanh_id=profile.chi_nhanh_id)
    if appointment.trang_thai != 'cho' or appointment.trang_thai_tiep_nhan not in {'cho_xac_nhan', 'da_xac_nhan'} or HoaDon.objects.filter(lich_kham=appointment).exists():
        raise PermissionDenied('Lịch này không còn đủ điều kiện đổi.')
    source = request.POST if request.method == 'POST' else request.GET
    services = bookable_services_for_branch(profile.chi_nhanh_id)
    prior_service_ids = list(appointment.dich_vu_dat_truoc_nhieu.values_list('pk', flat=True))
    if not prior_service_ids and appointment.dich_vu_dat_truoc_id:
        prior_service_ids = [appointment.dich_vu_dat_truoc_id]
    service_raws = source.getlist('dich_vu_dat_truoc_nhieu') or source.getlist('dich_vu_dat_truoc') or list(map(str, prior_service_ids))
    service_ids = list(dict.fromkeys(int(value) for value in service_raws if value.isascii() and value.isdigit()))
    selected_services = list(services.filter(pk__in=service_ids))
    service_ids = [service.pk for service in selected_services]
    doctors = eligible_doctors_for_services(profile.chi_nhanh_id, service_ids) if selected_services else BacSi.objects.none()
    doctor_raw = source.get('bac_si', str(appointment.bac_si_id))
    doctor = doctors.filter(pk=doctor_raw).first() if doctor_raw.isdigit() else None
    try: day = datetime.date.fromisoformat(source.get('ngay_kham', appointment.ngay_kham.isoformat()))
    except ValueError: day = None
    same_services = set(service_ids) == set(prior_service_ids)
    slots = available_booking_slots(doctor, day, patient_id=appointment.benh_nhan_id,
        exclude_appointment_id=appointment.pk, service_ids=service_ids,
        duration_minutes=appointment.thoi_luong_du_kien if same_services else None) if doctor and day and selected_services else []
    form_data = _reception_booking_post_data(request) if request.method == 'POST' else None
    form = ReceptionBookingForm(form_data, branch=profile.chi_nhanh, patient=appointment.benh_nhan,
        instance=appointment, services=services, doctors=doctors, slots=slots,
        initial={'so_dien_thoai': appointment.benh_nhan.so_dien_thoai,
                 'dich_vu_dat_truoc_nhieu': service_ids})
    form.fields['ly_do_thay_doi'] = forms.CharField(
        min_length=10, max_length=500, label='Lý do đổi lịch',
        widget=forms.Textarea(attrs={'rows': 2, 'maxlength': 500}),
    )
    form.order_fields(['so_dien_thoai', 'chi_nhanh', 'dich_vu_dat_truoc_nhieu', 'bac_si',
                       'ngay_kham', 'gio_kham', 'ly_do_thay_doi', 'ghi_chu'])
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                current_profile = receptionist_profile(request.user, lock=True)
                if not current_profile or current_profile.chi_nhanh_id != profile.chi_nhanh_id:
                    raise PermissionDenied('Phân công chi nhánh đã thay đổi; hãy tải lại trang.')
                locked = LichKham.objects.select_for_update().get(pk=pk, bac_si__chi_nhanh_id=profile.chi_nhanh_id)
                if (locked.trang_thai != 'cho'
                    or locked.trang_thai_tiep_nhan not in {'cho_xac_nhan', 'da_xac_nhan'}
                    or HoaDon.objects.filter(lich_kham=locked).exists()):
                    raise ValidationError('Lịch đã thay đổi trạng thái hoặc phát sinh hóa đơn.')
                before = {'bac_si_id': locked.bac_si_id, 'ngay_kham': str(locked.ngay_kham),
                          'gio_kham': str(locked.gio_kham)}
                changed = form.save(commit=False)
                changed.pk = locked.pk
                changed.benh_nhan_id = locked.benh_nhan_id
                book_appointment(changed, selected_services=form.cleaned_data['dich_vu_dat_truoc_nhieu'])
                LogHeThong.objects.create(model_name='LichKham', object_id=changed.pk, hanh_dong='update',
                    nguoi_thuc_hien=request.user, du_lieu_cu=before,
                    du_lieu_moi={'bac_si_id': changed.bac_si_id, 'ngay_kham': str(changed.ngay_kham),
                                 'gio_kham': str(changed.gio_kham)},
                    ghi_chu='Lễ tân đổi lịch; lý do: ' + form.cleaned_data['ly_do_thay_doi'].strip())
                after = {'bac_si_id': changed.bac_si_id, 'ngay_kham': str(changed.ngay_kham),
                         'gio_kham': str(changed.gio_kham)}
                if before != after:
                    from core.receipts.booking import notify_appointment_change
                    notify_appointment_change(changed)
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, 'Không thể đổi lịch: ' + (' '.join(exc.messages) if isinstance(exc, ValidationError) else 'khung giờ bị trùng.'))
        else:
            return redirect('reception_dashboard')
    return render(request, 'core/reception_booking.html', {'form': form, 'branch': profile.chi_nhanh, 'appointment': appointment})


@receptionist_required
@require_POST
@transaction.atomic
def reception_cancel(request, pk):
    profile = receptionist_profile(request.user, lock=True)
    appointment = get_object_or_404(LichKham.objects.select_for_update(), pk=pk, bac_si__chi_nhanh_id=profile.chi_nhanh_id)
    if appointment.trang_thai != 'cho' or appointment.trang_thai_tiep_nhan not in {'cho_xac_nhan', 'da_xac_nhan'} or HoaDon.objects.filter(lich_kham=appointment).exists():
        raise PermissionDenied('Lịch không thể hủy ở trạng thái hiện tại.')
    reason = request.POST.get('ly_do', '').strip()
    if not 10 <= len(reason) <= 500:
        messages.error(request, 'Nhập lý do hủy lịch từ 10 đến 500 ký tự để lưu nhật ký.')
        return redirect('affected_appointments' if request.POST.get('return_to') == 'affected' else 'reception_dashboard')
    now = timezone.localtime()
    scheduled = timezone.make_aware(datetime.datetime.combine(appointment.ngay_kham, appointment.gio_kham), timezone.get_current_timezone())
    if now >= scheduled:
        messages.error(request, 'Không thể hủy lịch đã đến giờ khám; hãy ghi nhận vắng mặt nếu đủ điều kiện.')
        return redirect('reception_dashboard')
    appointment.trang_thai = 'huy'
    appointment.save(update_fields=['trang_thai', 'ngay_cap_nhat'])
    LogHeThong.objects.create(model_name='LichKham', object_id=pk, hanh_dong='update', nguoi_thuc_hien=request.user,
        du_lieu_cu={'trang_thai': 'cho'}, du_lieu_moi={'trang_thai': 'huy'},
        ghi_chu='Lễ tân hủy lịch; lý do: ' + reason)
    from core.receipts.booking import notify_appointment_change
    notify_appointment_change(appointment, cancelled=True)
    return redirect('reception_dashboard')
