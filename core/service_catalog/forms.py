from django import forms
from django.core.validators import MaxValueValidator, MinValueValidator

from core.models import DanhMucDichVu
from .presentation import catalog_description


class DichVuForm(forms.ModelForm):
    thoi_luong_phut = forms.IntegerField(
        required=False, min_value=15, max_value=480, label='Thời lượng dự kiến (phút)',
        help_text='Dùng để giữ đủ thời gian khám khi chọn một hoặc nhiều dịch vụ; không phải thời lượng bảo đảm.',
        widget=forms.NumberInput(attrs={'min': '15', 'max': '480', 'step': '15'}),
    )

    class Meta:
        model = DanhMucDichVu
        fields = ['ma_dich_vu', 'ten', 'nhom', 'mo_ta', 'don_gia', 'thoi_luong_phut', 'dang_ap_dung', 'cho_phep_dat_lich']
        widgets = {
            'mo_ta': forms.Textarea(attrs={'rows': 4}),
            'don_gia': forms.NumberInput(attrs={'min': '0', 'max': '9999999999', 'step': '1'}),
        }
        help_texts = {
            'ma_dich_vu': 'Mã được chuẩn hóa thành chữ in hoa và giữ cố định sau khi tạo.',
            'nhom': 'Nhập nhóm hiện có hoặc tên nhóm mới, ví dụ: Khám bệnh, Xét nghiệm.',
            'don_gia': 'Nhập số nguyên VND, từ 0 đến 9.999.999.999. Đây là bảng giá chung toàn hệ thống.',
            'cho_phep_dat_lich': 'Bật cho dịch vụ bệnh nhân có thể chọn trước; kỹ thuật chỉ được bác sĩ chỉ định thì để tắt.',
        }
        error_messages = {
            'ma_dich_vu': {'unique': 'Mã dịch vụ này đã tồn tại.', 'required': 'Vui lòng nhập mã dịch vụ.'},
            'ten': {'required': 'Vui lòng nhập tên dịch vụ.'},
            'nhom': {'required': 'Vui lòng nhập nhóm dịch vụ.'},
            'don_gia': {
                'required': 'Vui lòng nhập đơn giá.', 'invalid': 'Đơn giá phải là số nguyên VND hợp lệ.',
                'min_value': 'Đơn giá không được âm.', 'max_decimal_places': 'Đơn giá VND không có phần thập phân.',
                'max_digits': 'Đơn giá tối đa 9.999.999.999 VND.',
            },
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial['mo_ta'] = catalog_description(self.instance.ma_dich_vu, self.instance.mo_ta)
            # Enforced by Django's bound form, not just an HTML disabled attribute.
            self.fields['ma_dich_vu'].disabled = True

    def clean_thoi_luong_phut(self):
        return self.cleaned_data.get('thoi_luong_phut') or getattr(self.instance, 'thoi_luong_phut', 30) or 30

    def clean_ma_dich_vu(self):
        return self.cleaned_data['ma_dich_vu'].strip().upper()
