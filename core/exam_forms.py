from django import forms
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory

from .models import ChiTietKhamMat, DonKinh, DonThuoc, PhieuKham


class ExamEyeForm(forms.ModelForm):
    class Meta:
        model = ChiTietKhamMat
        exclude = ('phieu_kham',)
        labels = {
            'thi_luc_phai_khong_kinh': 'Thị lực OD không kính', 'thi_luc_phai_co_kinh': 'Thị lực OD có kính',
            'thi_luc_trai_khong_kinh': 'Thị lực OS không kính', 'thi_luc_trai_co_kinh': 'Thị lực OS có kính',
            'nhan_ap_phai': 'Nhãn áp OD (mmHg)', 'nhan_ap_trai': 'Nhãn áp OS (mmHg)',
            'sph_phai': 'SPH OD (D)', 'sph_trai': 'SPH OS (D)', 'cyl_phai': 'CYL OD (D)', 'cyl_trai': 'CYL OS (D)',
            'axis_phai': 'AXIS OD (0–180°)', 'axis_trai': 'AXIS OS (0–180°)',
            'ban_phan_truoc': 'Khám bán phần trước', 'day_mat': 'Khám đáy mắt', 'ghi_nhan_khac': 'Ghi nhận lâm sàng khác',
        }
        help_texts = {
            'nhan_ap_phai': 'Giới hạn nhập 0–100 mmHg.', 'nhan_ap_trai': 'Giới hạn nhập 0–100 mmHg.',
            'sph_phai': 'Đơn vị diop; giới hạn −30 đến +30.', 'sph_trai': 'Đơn vị diop; giới hạn −30 đến +30.',
            'cyl_phai': 'Đơn vị diop; giới hạn −20 đến +20.', 'cyl_trai': 'Đơn vị diop; giới hạn −20 đến +20.',
            'axis_phai': 'Góc từ 0 đến 180; cần nhập CYL.', 'axis_trai': 'Góc từ 0 đến 180; cần nhập CYL.',
        }
        widgets = {
            'ban_phan_truoc': forms.Textarea(attrs={'rows': 3}),
            'day_mat': forms.Textarea(attrs={'rows': 3}),
            'ghi_nhan_khac': forms.Textarea(attrs={'rows': 3}),
            'nhan_ap_phai': forms.NumberInput(attrs={'min': '0', 'max': '100', 'step': '0.01'}),
            'nhan_ap_trai': forms.NumberInput(attrs={'min': '0', 'max': '100', 'step': '0.01'}),
            'sph_phai': forms.NumberInput(attrs={'min': '-30', 'max': '30', 'step': '0.01'}),
            'sph_trai': forms.NumberInput(attrs={'min': '-30', 'max': '30', 'step': '0.01'}),
            'cyl_phai': forms.NumberInput(attrs={'min': '-20', 'max': '20', 'step': '0.01'}),
            'cyl_trai': forms.NumberInput(attrs={'min': '-20', 'max': '20', 'step': '0.01'}),
            'axis_phai': forms.NumberInput(attrs={'min': '0', 'max': '180', 'step': '1'}),
            'axis_trai': forms.NumberInput(attrs={'min': '0', 'max': '180', 'step': '1'}),
        }


class GlassesPrescriptionForm(forms.ModelForm):
    class Meta:
        model = DonKinh
        exclude = ('phieu_kham', 'ngay_tao')
        labels = {
            'sph_phai': 'SPH OD (D)', 'cyl_phai': 'CYL OD (D)', 'axis_phai': 'AXIS OD (0–180°)',
            'sph_trai': 'SPH OS (D)', 'cyl_trai': 'CYL OS (D)', 'axis_trai': 'AXIS OS (0–180°)',
            'ghi_chu': 'Ghi chú đơn kính', 'bac_si_xac_nhan': 'Tôi xác nhận đây là đơn kính đã kê',
        }
        widgets = {
            'ghi_chu': forms.Textarea(attrs={'rows': 2}),
            'sph_phai': forms.NumberInput(attrs={'min': '-30', 'max': '30', 'step': '0.01'}),
            'sph_trai': forms.NumberInput(attrs={'min': '-30', 'max': '30', 'step': '0.01'}),
            'cyl_phai': forms.NumberInput(attrs={'min': '-20', 'max': '20', 'step': '0.01'}),
            'cyl_trai': forms.NumberInput(attrs={'min': '-20', 'max': '20', 'step': '0.01'}),
            'axis_phai': forms.NumberInput(attrs={'min': '0', 'max': '180', 'step': '1'}),
            'axis_trai': forms.NumberInput(attrs={'min': '0', 'max': '180', 'step': '1'}),
        }

    def clean(self):
        cleaned = super().clean()
        entered = any(cleaned.get(field) not in (None, '') for field in (
            'sph_phai', 'cyl_phai', 'axis_phai', 'sph_trai', 'cyl_trai', 'axis_trai', 'ghi_chu'
        ))
        if entered and not cleaned.get('bac_si_xac_nhan'):
            self.add_error('bac_si_xac_nhan', 'Bác sĩ cần xác nhận đơn kính trước khi lưu.')
        return cleaned


class MedicineForm(forms.ModelForm):
    class Meta:
        model = DonThuoc
        fields = ('ten_thuoc', 'lieu_dung', 'huong_dan', 'thoi_gian_dung', 'ghi_chu')
        widgets = {'huong_dan': forms.Textarea(attrs={'rows': 2}), 'ghi_chu': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False

    def clean(self):
        cleaned = super().clean()
        if self.cleaned_data.get('DELETE'):
            return cleaned
        entered = any(cleaned.get(name) for name in ('ten_thuoc', 'lieu_dung', 'huong_dan', 'thoi_gian_dung', 'ghi_chu'))
        if entered:
            for name, label in (('ten_thuoc', 'Tên thuốc'), ('lieu_dung', 'Liều dùng'), ('huong_dan', 'Hướng dẫn sử dụng')):
                if not cleaned.get(name):
                    self.add_error(name, f'{label} là bắt buộc khi kê thuốc.')
        return cleaned


MedicineFormSet = inlineformset_factory(
    PhieuKham, DonThuoc, form=MedicineForm, extra=1, can_delete=True, max_num=10, validate_max=True,
)
