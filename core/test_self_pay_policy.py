from django.apps import apps
from django.test import SimpleTestCase
from django.urls import NoReverseMatch, reverse

from core.billing.services import bill_exam
from core.models import (BacSi, BenhNhan, ChiNhanh, ChiTietHoaDon, DichVuThucHien,
                         HoaDon, Khoa, LichKham, PhieuKham)
from core.test_exam_services import fixtures


class ClinicBillingFixtures:
    def prepare(self):
        (self.admin, self.patient_user, self.appointment, self.doctor, self.other_doctor,
         self.service, self.other, self.inactive) = fixtures()
        self.phieu = PhieuKham.objects.get(lich_kham=self.appointment)
        self.date = self.appointment.ngay_kham
        self.clinic = self.appointment.bac_si.chi_nhanh

    def record(self, service=None, quantity=1):
        return DichVuThucHien.objects.create(
            phieu_kham=self.phieu, dich_vu=service or self.service, so_luong=quantity,
            nguoi_thuc_hien=self.appointment.bac_si)

    def invoice(self, quantity=1):
        self.record(quantity=quantity)
        return bill_exam(self.phieu.pk, getattr(self, 'cashier', self.admin))


class ClinicSelfPayPolicyTests(SimpleTestCase):
    def test_insurance_models_are_not_registered_in_runtime(self):
        with self.assertRaises(LookupError):
            apps.get_model("core", "BaoHiemYTe")
        with self.assertRaises(LookupError):
            apps.get_model("core", "QuyTacBHYT")

    def test_clinic_billing_models_have_no_insurance_fields(self):
        for model, fields in ((ChiNhanh, {"co_bhyt", "cap_cuu_24h", "loai_hinh"}),
                              (BenhNhan, {"bhyt"}),
                              (HoaDon, {"bhyt_ho_tro", "bhyt_snapshot", "hoan_tat_bhyt"}),
                              (ChiTietHoaDon, {"quy_tac_bhyt", "ty_le_bhyt", "bhyt_ho_tro"})):
            names = {field.name for field in model._meta.get_fields()}
            self.assertTrue(fields.isdisjoint(names), (model.__name__, fields & names))

    def test_insurance_routes_are_not_registered(self):
        with self.assertRaises(NoReverseMatch):
            reverse("bhyt_rule_list")
        with self.assertRaises(NoReverseMatch):
            reverse("payment_bhyt_apply")
