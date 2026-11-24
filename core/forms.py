import datetime

from django import forms
from django.contrib.auth.models import Permission, User
from django.contrib.gis.geos import Point
from .branches import active_branch_queryset, branch_queryset_with_existing, is_official_branch

from .models import (
    BacSi,
    BacSiDichVu,
    ChiNhanh,
    BenhNhan,
    DanhMucDichVu,
    DichVuChiNhanh,
    GioLamViecBacSi,
    GioLamViecChiNhanh,
    Khoa,
    LichKham,
    LeTanChiNhanh,
)


class BookingSlotSelect(forms.Select):
    def __init__(self, *args, unavailable_values=(), **kwargs):
        self.unavailable_values = {str(value) for value in unavailable_values}
        super().__init__(*args, **kwargs)

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value is not None and str(value) in self.unavailable_values:
            option['attrs']['disabled'] = True
            option['attrs']['class'] = (option['attrs'].get('class', '') + ' slot-unavailable').strip()
        return option


class DatLichForm(forms.ModelForm):
    dich_vu_dat_truoc_nhieu = forms.ModelMultipleChoiceField(
        queryset=DanhMucDichVu.objects.none(), required=True, label="Dịch vụ khám mắt",
        widget=forms.CheckboxSelectMultiple(),
        help_text="Chọn một hoặc nhiều dịch vụ. Bác sĩ sẽ xác nhận dịch vụ thực tế sau khi khám.",
    )
    chi_nhanh = forms.ModelChoiceField(
        queryset=ChiNhanh.objects.none(),
        label="Chi nhánh",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    gio_kham = forms.ChoiceField(
        choices=(),
        label="Khung giờ còn trống",
        widget=BookingSlotSelect(attrs={"class": "form-control"}),
    )

    class Meta:
        model = LichKham
        fields = ["bac_si", "ngay_kham", "gio_kham", "ghi_chu"]

        widgets = {
            "ngay_kham": forms.DateInput(attrs={
                "type": "date",
                "class": "form-control",
            }),
            "ghi_chu": forms.Textarea(attrs={
                "rows": 3,
                "class": "form-control",
            }),
        }

    bac_si = forms.ModelChoiceField(
        queryset=BacSi.objects.none(),
        label="Bác sĩ",
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    def __init__(self, *args, branches=None, services=None, doctors=None, slots=None, slot_options=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["chi_nhanh"].queryset = branches or ChiNhanh.objects.none()
        self.fields["dich_vu_dat_truoc_nhieu"].queryset = services or DanhMucDichVu.objects.none()
        self.fields["bac_si"].queryset = doctors or BacSi.objects.none()
        available_values = {slot.strftime("%H:%M") for slot in (slots or [])}
        options = slot_options or [(slot, True) for slot in (slots or [])]
        unavailable_values = set()
        choices = [("", "— Chọn khung giờ —")]
        for slot, available in options:
            value = slot.strftime("%H:%M")
            label = value if available else f"{value} — Không còn trống"
            choices.append((value, label))
            if not available:
                unavailable_values.add(value)
        self.available_slot_values = available_values
        self.fields["gio_kham"].choices = choices
        self.fields["gio_kham"].widget = BookingSlotSelect(
            unavailable_values=unavailable_values,
            attrs={"class": "form-control"},
        )
        self.order_fields(["chi_nhanh", "dich_vu_dat_truoc_nhieu", "bac_si", "ngay_kham", "gio_kham", "ghi_chu"])

    def clean_gio_kham(self):
        try:
            value = self.cleaned_data["gio_kham"]
            parsed = datetime.time.fromisoformat(value)
        except (TypeError, ValueError):
            raise forms.ValidationError("Khung giờ không hợp lệ.")
        if value not in self.available_slot_values:
            raise forms.ValidationError("Khung giờ này vừa có người đặt hoặc không còn phù hợp. Vui lòng chọn giờ khác.")
        return parsed

    def clean(self):
        cleaned = super().clean()
        branch = cleaned.get("chi_nhanh")
        selected_services = list(cleaned.get("dich_vu_dat_truoc_nhieu") or [])
        doctor = cleaned.get("bac_si")
        if branch and (not branch.dang_hoat_dong or not is_official_branch(branch.pk)):
            self.add_error("chi_nhanh", "Chi nhánh không thuộc danh sách đang vận hành.")
        if branch and selected_services:
            total_duration = sum(service.thoi_luong_phut for service in selected_services)
            if total_duration > 480:
                self.add_error("dich_vu_dat_truoc_nhieu", "Tổng thời lượng các dịch vụ không được vượt quá 480 phút cho một lượt khám.")
            valid_services = set(DichVuChiNhanh.objects.filter(
                chi_nhanh=branch, dich_vu__in=selected_services, dang_ap_dung=True,
                chi_nhanh__dang_hoat_dong=True, dich_vu__dang_ap_dung=True,
                dich_vu__cho_phep_dat_lich=True,
            ).values_list("dich_vu_id", flat=True))
            if valid_services != {service.pk for service in selected_services}:
                self.add_error("dich_vu_dat_truoc_nhieu", "Có dịch vụ không được đặt tại chi nhánh này.")
        if branch and doctor and doctor.chi_nhanh_id != branch.pk:
            self.add_error("bac_si", "Bác sĩ không thuộc chi nhánh đã chọn.")
        if branch and doctor and selected_services:
            assigned = set(BacSiDichVu.objects.filter(
                bac_si=doctor, dich_vu_chi_nhanh__chi_nhanh=branch,
                dich_vu_chi_nhanh__dich_vu__in=selected_services, dang_ap_dung=True,
                dich_vu_chi_nhanh__dang_ap_dung=True,
            ).values_list("dich_vu_chi_nhanh__dich_vu_id", flat=True))
            if assigned != {service.pk for service in selected_services}:
                self.add_error("bac_si", "Bác sĩ chưa được phân công tất cả dịch vụ đã chọn tại chi nhánh.")
        return cleaned


class ContactFeedbackForm(forms.Form):
    ho_ten = forms.CharField(
        max_length=120,
        label="Họ tên",
        error_messages={
            "required": "Vui lòng nhập họ tên.",
            "max_length": "Họ tên tối đa 120 ký tự.",
        },
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Nguyễn Văn A"}),
    )
    email = forms.EmailField(
        label="Email liên hệ",
        error_messages={
            "required": "Vui lòng nhập email liên hệ.",
            "invalid": "Email không đúng định dạng.",
        },
        widget=forms.EmailInput(attrs={"class": "form-control", "placeholder": "ban@email.com"}),
    )
    chu_de = forms.CharField(
        max_length=180,
        label="Chủ đề",
        error_messages={
            "required": "Vui lòng nhập chủ đề.",
            "max_length": "Chủ đề tối đa 180 ký tự.",
        },
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Góp ý về chức năng đặt lịch"}),
    )
    noi_dung = forms.CharField(
        min_length=10,
        label="Nội dung góp ý",
        error_messages={
            "required": "Vui lòng nhập nội dung góp ý.",
            "min_length": "Nội dung góp ý tối thiểu 10 ký tự.",
        },
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 6,
                "placeholder": "Nhập góp ý hoặc vấn đề bạn gặp...",
            }
        ),
    )


class AdminChiNhanhForm(forms.ModelForm):
    lat = forms.FloatField(
        min_value=-90,
        max_value=90,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "any"}),
        label="Vĩ độ",
    )
    lon = forms.FloatField(
        min_value=-180,
        max_value=180,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "any"}),
        label="Kinh độ",
    )

    class Meta:
        model = ChiNhanh
        fields = [
            "ten",
            "lat",
            "lon",
            "dang_hoat_dong",
        ]
        labels = {
            "ten": "Tên chi nhánh",
            "dang_hoat_dong": "Chi nhánh đang hoạt động",
        }
        widgets = {
            "ten": forms.TextInput(attrs={"class": "form-control"}),
            "dang_hoat_dong": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.vi_tri:
            self.fields["lat"].initial = self.instance.vi_tri.y
            self.fields["lon"].initial = self.instance.vi_tri.x

    def clean(self):
        cleaned = super().clean()
        branch_is_official = bool(self.instance.pk and is_official_branch(self.instance.pk))
        if cleaned.get('dang_hoat_dong') and not branch_is_official:
            self.add_error('dang_hoat_dong', 'Chỉ 9 chi nhánh chính thức mới được hoạt động. Chi nhánh cũ được giữ để tra cứu lịch sử.')
        return cleaned

    def save(self, commit=True):
        is_new = self.instance.pk is None
        obj = super().save(commit=False)
        lat = self.cleaned_data.get("lat")
        lon = self.cleaned_data.get("lon")
        obj.vi_tri = Point(lon, lat, srid=4326)
        if is_new:
            # These details are temporarily hidden from the demo form.
            obj.dia_chi = ""
            obj.phuong = ""
            obj.so_dien_thoai = ""
            obj.email = ""
        # Keep required model fields consistent while admin now manages
        # detailed operating hours via schedule-by-week table.
        if not obj.gio_mo:
            obj.gio_mo = datetime.time(7, 0)
        if not obj.gio_dong:
            obj.gio_dong = datetime.time(17, 0)
        if commit:
            obj.save()
        return obj


class AdminKhoaForm(forms.ModelForm):
    class Meta:
        model = Khoa
        fields = ["ten", "chi_nhanh"]
        widgets = {
            "ten": forms.TextInput(attrs={"class": "form-control"}),
            "chi_nhanh": forms.Select(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = active_branch_queryset()
        if self.instance and self.instance.pk:
            queryset = branch_queryset_with_existing(queryset, self.instance.chi_nhanh_id)
            if not is_official_branch(self.instance.chi_nhanh_id):
                self.fields['chi_nhanh'].disabled = True
        self.fields['chi_nhanh'].queryset = queryset


class AdminGioLamViecChiNhanhForm(forms.ModelForm):
    class Meta:
        model = GioLamViecChiNhanh
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        branches = active_branch_queryset()
        if self.instance and self.instance.pk:
            branch_id = self.instance.chi_nhanh_id
            branches = branch_queryset_with_existing(branches, branch_id)
            if not is_official_branch(branch_id):
                self.fields['chi_nhanh'].disabled = True
        self.fields['chi_nhanh'].queryset = branches.distinct()

    def clean(self):
        cleaned = super().clean()
        branch = cleaned.get('chi_nhanh')
        existing_branch_id = self.instance.chi_nhanh_id if self.instance and self.instance.pk else None
        if branch and not is_official_branch(branch.pk) and existing_branch_id != branch.pk:
            self.add_error('chi_nhanh', 'Không thể tạo lịch hoạt động mới ngoài 9 chi nhánh EyeViora.')
        return cleaned


class AdminBenhNhanForm(forms.ModelForm):
    class Meta:
        model = BenhNhan
        fields = ("user", "ho_ten", "ngay_sinh", "gioi_tinh", "so_dien_thoai", "dia_chi")


class AdminDichVuChiNhanhForm(forms.ModelForm):
    class Meta:
        model = DichVuChiNhanh
        fields = ["chi_nhanh", "dich_vu", "dang_ap_dung"]
        widgets = {
            "chi_nhanh": forms.Select(attrs={"class": "form-control"}),
            "dich_vu": forms.Select(attrs={"class": "form-control"}),
            "dang_ap_dung": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        help_texts = {
            "dich_vu": "Chỉ dịch vụ đang áp dụng trong danh mục chung mới được thêm mới.",
            "dang_ap_dung": "Ngừng cung cấp không xóa lịch hẹn hoặc lịch sử dịch vụ.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        branch_qs = active_branch_queryset().order_by("ten")
        service_qs = DanhMucDichVu.objects.filter(dang_ap_dung=True).order_by("ma_dich_vu")
        if self.instance and self.instance.pk:
            branch_qs = branch_queryset_with_existing(branch_qs, self.instance.chi_nhanh_id)
            service_qs = DanhMucDichVu.objects.filter(pk=self.instance.dich_vu_id) | service_qs
            self.fields["chi_nhanh"].disabled = True
            self.fields["dich_vu"].disabled = True
        self.fields["chi_nhanh"].queryset = branch_qs.distinct()
        self.fields["dich_vu"].queryset = service_qs.distinct()


class AdminBacSiDichVuForm(forms.ModelForm):
    chi_nhanh = forms.ModelChoiceField(
        queryset=ChiNhanh.objects.none(),
        label="Chi nhánh",
        help_text="Chọn chi nhánh rồi bấm ‘Tải bác sĩ và dịch vụ’ để lọc danh sách.",
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    class Meta:
        model = BacSiDichVu
        fields = ["bac_si", "dich_vu_chi_nhanh", "dang_ap_dung"]
        widgets = {
            "bac_si": forms.Select(attrs={"class": "form-control"}),
            "dich_vu_chi_nhanh": forms.Select(attrs={"class": "form-control"}),
            "dang_ap_dung": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        help_texts = {
            "dich_vu_chi_nhanh": "Chỉ chọn dịch vụ chi nhánh đang cung cấp.",
            "dang_ap_dung": "Ngừng phân công không hủy lịch hẹn đã tạo.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current_branch_id = None
        if self.is_bound:
            current_branch_id = self.data.get("chi_nhanh")
        elif self.instance and self.instance.pk:
            current_branch_id = self.instance.dich_vu_chi_nhanh.chi_nhanh_id

        branches = active_branch_queryset().order_by("ten")
        if self.instance and self.instance.pk:
            current_branch_id = self.instance.dich_vu_chi_nhanh.chi_nhanh_id
            branches = branch_queryset_with_existing(branches, current_branch_id)
            self.fields["chi_nhanh"].disabled = True
            self.fields["bac_si"].disabled = True
            self.fields["dich_vu_chi_nhanh"].disabled = True
            self.fields["chi_nhanh"].initial = current_branch_id
        self.fields["chi_nhanh"].queryset = branches.distinct()

        try:
            branch_id = int(current_branch_id) if current_branch_id else None
        except (TypeError, ValueError):
            branch_id = None
        if branch_id:
            self.fields["bac_si"].queryset = BacSi.objects.filter(
                chi_nhanh_id=branch_id,
            ).select_related("chi_nhanh").order_by("ho_ten")
            service_rows = DichVuChiNhanh.objects.filter(
                chi_nhanh_id=branch_id,
                dang_ap_dung=True,
                dich_vu__dang_ap_dung=True,
            ).select_related("chi_nhanh", "dich_vu").order_by("dich_vu__ma_dich_vu")
            if self.instance and self.instance.pk:
                service_rows = DichVuChiNhanh.objects.filter(
                    pk=self.instance.dich_vu_chi_nhanh_id,
                ) | service_rows
            self.fields["dich_vu_chi_nhanh"].queryset = service_rows.distinct()
            self.fields["bac_si"].label_from_instance = lambda doctor: doctor.ho_ten
            self.fields["dich_vu_chi_nhanh"].label_from_instance = (
                lambda row: f"{row.dich_vu.ma_dich_vu} — {row.dich_vu.ten}"
            )

        self.order_fields(["chi_nhanh", "bac_si", "dich_vu_chi_nhanh", "dang_ap_dung"])

    def clean(self):
        cleaned = super().clean()
        branch = cleaned.get("chi_nhanh")
        doctor = cleaned.get("bac_si")
        branch_service = cleaned.get("dich_vu_chi_nhanh")
        if branch and doctor and doctor.chi_nhanh_id != branch.pk:
            self.add_error("bac_si", "Bác sĩ không thuộc chi nhánh đã chọn.")
        if branch and branch_service and branch_service.chi_nhanh_id != branch.pk:
            self.add_error("dich_vu_chi_nhanh", "Dịch vụ không thuộc chi nhánh đã chọn.")
        return cleaned


class AdminBacSiForm(forms.ModelForm):
    class Meta:
        model = BacSi
        fields = ["ho_ten", "so_dien_thoai", "chuyen_khoa", "chi_nhanh", "user"]
        widgets = {
            "ho_ten": forms.TextInput(attrs={"class": "form-control"}),
            "so_dien_thoai": forms.TextInput(attrs={"class": "form-control"}),
            "chuyen_khoa": forms.TextInput(attrs={"class": "form-control"}),
            "chi_nhanh": forms.Select(attrs={"class": "form-control"}),
            "user": forms.Select(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        branches = active_branch_queryset()
        if self.instance and self.instance.pk:
            branches = branch_queryset_with_existing(branches, self.instance.chi_nhanh_id)
        self.fields['chi_nhanh'].queryset = branches.order_by('ten')
        self.fields["user"].required = False
        self.fields["user"].queryset = User.objects.filter(le_tan_chi_nhanh__isnull=True)
        self.fields["user"].label = "Tài khoản liên kết (tùy chọn)"

        chi_nhanh_id = None
        if self.is_bound:
            chi_nhanh_id = self.data.get("chi_nhanh")
        elif self.instance and self.instance.pk:
            chi_nhanh_id = self.instance.chi_nhanh_id

        if self.instance and self.instance.pk and LichKham.objects.filter(bac_si=self.instance).exists():
            self.fields["chi_nhanh"].disabled = True
            self.fields["chi_nhanh"].help_text = "Bác sĩ đã có lịch khám; không thể chuyển chi nhánh để giữ nguyên lịch sử."

    def clean(self):
        cleaned_data = super().clean()
        chi_nhanh = cleaned_data.get("chi_nhanh")
        if chi_nhanh:
            if not is_official_branch(chi_nhanh.pk) and (
                not self.instance.pk or self.instance.chi_nhanh_id != chi_nhanh.pk
            ):
                self.add_error('chi_nhanh', 'Không thể phân công bác sĩ mới tại chi nhánh đã ngừng vận hành.')
            has_matching_department = bool(
                self.instance and self.instance.pk and self.instance.khoa_id
                and Khoa.objects.filter(pk=self.instance.khoa_id, chi_nhanh_id=chi_nhanh.pk).exists()
            )
            if not has_matching_department:
                if not Khoa.objects.filter(chi_nhanh=chi_nhanh).exists():
                    self.add_error("chi_nhanh", "Chi nhánh chưa có khoa để gắn hồ sơ bác sĩ.")
        return cleaned_data

    def save(self, commit=True):
        doctor = super().save(commit=False)
        branch = self.cleaned_data.get('chi_nhanh')
        if not doctor.khoa_id or not Khoa.objects.filter(pk=doctor.khoa_id, chi_nhanh_id=branch.pk).exists():
            doctor.khoa = Khoa.objects.filter(chi_nhanh=branch).order_by('ten', 'pk').first()
        if commit:
            doctor.save()
            self.save_m2m()
        return doctor


class AdminGioLamViecBacSiForm(forms.ModelForm):
    class Meta:
        model = GioLamViecBacSi
        fields = "__all__"
        widgets = {
            "bac_si": forms.Select(attrs={"class": "form-control"}),
            "thu": forms.Select(attrs={"class": "form-control"}),
            "gio_bat_dau": forms.TimeInput(attrs={"class": "form-control", "type": "time"}),
            "gio_ket_thuc": forms.TimeInput(attrs={"class": "form-control", "type": "time"}),
            "nghi": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bac_si"].queryset = BacSi.objects.select_related("chi_nhanh").order_by("ho_ten")
        self.fields["bac_si"].label_from_instance = (
            lambda obj: f"{obj.ho_ten} - {obj.chi_nhanh.ten}"
        )

    def clean(self):
        cleaned = super().clean()
        doctor = cleaned.get('bac_si')
        if doctor and not is_official_branch(doctor.chi_nhanh_id) and (
            not self.instance.pk or self.instance.bac_si_id != doctor.pk
        ):
            self.add_error('bac_si', 'Không thể tạo lịch làm việc mới tại chi nhánh đã ngừng vận hành.')
        return cleaned


class AdminUserForm(forms.ModelForm):
    ROLE_USER = "nguoi_dung"
    ROLE_DOCTOR = "bac_si"
    ROLE_RECEPTION = "le_tan"
    ROLE_ADMIN = "admin"

    xem_lich_su_lien_chi_nhanh = forms.BooleanField(
        required=False,
        label='Được tra cứu lịch sử khám liên chi nhánh',
        help_text='Chỉ bật cho bác sĩ cần quyền nghiệp vụ này. Mỗi lượt tra cứu bắt buộc ghi lý do và nhật ký.',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    chi_nhanh = forms.ModelChoiceField(
        queryset=active_branch_queryset().order_by('ten'),
        required=False,
        label='Chi nhánh phụ trách (dành cho lễ tân)',
        widget=forms.Select(attrs={'class': 'form-control'}),
    )

    role = forms.ChoiceField(
        required=True,
        label="Vai trò",
        choices=(
            (ROLE_USER, "Người dùng"),
            (ROLE_DOCTOR, "Bác sĩ"),
            (ROLE_RECEPTION, "Lễ tân"),
            (ROLE_ADMIN, "Nhân viên quản trị"),
        ),
        widget=forms.Select(attrs={"class": "form-control"}),
        help_text="Bác sĩ phải được liên kết trong danh mục Bác sĩ. Lễ tân cần được phân công chi nhánh.",
    )

    password = forms.CharField(
        required=False,
        label="Mật khẩu mới",
        widget=forms.PasswordInput(render_value=False, attrs={"class": "form-control"}),
        help_text="Để trống nếu không đổi mật khẩu.",
    )

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "first_name",
            "last_name",
            "is_active",
            "role",
            "password",
        ]
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['chi_nhanh'].queryset = active_branch_queryset().order_by('ten')
        if self.instance and self.instance.pk:
            self.fields['xem_lich_su_lien_chi_nhanh'].initial = self.instance.user_permissions.filter(
                content_type__app_label='core', codename='view_cross_branch_medical_history'
            ).exists()
            if self.instance.is_staff or self.instance.is_superuser:
                self.fields["role"].initial = self.ROLE_ADMIN
            elif LeTanChiNhanh.objects.filter(user=self.instance).exists():
                self.fields["role"].initial = self.ROLE_RECEPTION
                receptionist = LeTanChiNhanh.objects.filter(
                    user=self.instance
                ).select_related('chi_nhanh').first()
                if receptionist:
                    self.fields['chi_nhanh'].initial = receptionist.chi_nhanh_id
                    self.fields['chi_nhanh'].queryset = branch_queryset_with_existing(
                        self.fields['chi_nhanh'].queryset, receptionist.chi_nhanh_id,
                    )
            elif BacSi.objects.filter(user=self.instance).exists():
                self.fields["role"].initial = self.ROLE_DOCTOR
            else:
                self.fields["role"].initial = self.ROLE_USER
        else:
            self.fields["role"].initial = self.ROLE_USER

        if not self.instance or not self.instance.pk:
            self.fields["password"].required = True
            self.fields["password"].help_text = "Bắt buộc khi tạo tài khoản."

    def clean(self):
        cleaned_data = super().clean()
        role = cleaned_data.get("role")
        current_user = self.instance if self.instance and self.instance.pk else None
        has_doctor_profile = bool(current_user and BacSi.objects.filter(user=current_user).exists())
        if role == self.ROLE_RECEPTION:
            branch = cleaned_data.get('chi_nhanh')
            if not branch:
                self.add_error('chi_nhanh', 'Chọn chi nhánh phụ trách cho tài khoản lễ tân.')
            elif not is_official_branch(branch.pk):
                existing_profile = LeTanChiNhanh.objects.filter(user=current_user).first() if current_user else None
                if not existing_profile or existing_profile.chi_nhanh_id != branch.pk:
                    self.add_error('chi_nhanh', 'Chỉ được phân công lễ tân mới tại 9 chi nhánh chính thức.')
            if has_doctor_profile or (current_user and hasattr(current_user, 'benh_nhan')):
                self.add_error('role', 'Tài khoản đang có hồ sơ nghiệp vụ khác, không thể gán làm lễ tân.')

        if role == self.ROLE_DOCTOR and not has_doctor_profile:
            self.add_error(
                "role",
                "Tài khoản này chưa liên kết bác sĩ. Hãy vào danh mục Bác sĩ để liên kết trước.",
            )

        if cleaned_data.get('xem_lich_su_lien_chi_nhanh') and role != self.ROLE_DOCTOR:
            self.add_error('xem_lich_su_lien_chi_nhanh', 'Quyền tra cứu liên chi nhánh chỉ dành cho tài khoản bác sĩ.')

        if role in {self.ROLE_USER, self.ROLE_ADMIN} and has_doctor_profile:
            self.add_error(
                "role",
                "Tài khoản đang liên kết Bác sĩ. Hãy gỡ liên kết ở danh mục Bác sĩ nếu muốn đổi vai trò.",
            )
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        role = self.cleaned_data.get("role", self.ROLE_USER)
        if user.is_superuser:
            # Defensive preservation: privileged-account edits must never silently demote it.
            user.is_staff = True
            user.is_superuser = True
        elif role == self.ROLE_ADMIN:
            user.is_staff = True
            user.is_superuser = False
        else:
            user.is_staff = False
            user.is_superuser = False

        raw_password = self.cleaned_data.get("password", "").strip()
        if raw_password:
            user.set_password(raw_password)
        elif not user.pk:
            user.set_unusable_password()
        if commit:
            user.save()
            if role == self.ROLE_RECEPTION:
                profile = LeTanChiNhanh.objects.filter(user=user).first()
                if profile:
                    profile.chi_nhanh = self.cleaned_data['chi_nhanh']
                    profile.save(update_fields=['chi_nhanh'])
                else:
                    LeTanChiNhanh.objects.create(user=user, chi_nhanh=self.cleaned_data['chi_nhanh'])
            else:
                LeTanChiNhanh.objects.filter(user=user).delete()
            permission = Permission.objects.get(
                content_type__app_label='core', codename='quan_tri_he_thong'
            )
            if user.is_superuser or role == self.ROLE_ADMIN:
                user.user_permissions.add(permission)
            else:
                user.user_permissions.remove(permission)
            cross_branch_permission = Permission.objects.get(
                content_type__app_label='core', codename='view_cross_branch_medical_history'
            )
            if role == self.ROLE_DOCTOR and self.cleaned_data.get('xem_lich_su_lien_chi_nhanh'):
                user.user_permissions.add(cross_branch_permission)
            else:
                user.user_permissions.remove(cross_branch_permission)
        return user



