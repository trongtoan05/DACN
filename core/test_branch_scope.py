import datetime

from django.contrib.auth.models import User
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from core.branches import active_branch_queryset
from core.appointments import book_appointment
from core.forms import AdminChiNhanhForm
from core.models import ChiNhanh
from core.payments.cashiers import BranchManagerForm
from core.reception import ReceptionAccountForm
from core.test_vnpay import fixtures as payment_fixtures


class OfficialBranchScopeTests(TestCase):
    def make_branch(self, name):
        return ChiNhanh.objects.create(
            ten=name, dia_chi='Địa chỉ thử nghiệm', phuong='Phường thử nghiệm',
            vi_tri=Point(106.7, 10.77, srid=4326),
            gio_mo=datetime.time(7), gio_dong=datetime.time(17),
        )

    def test_public_queryset_excludes_other_active_rows(self):
        official = self.make_branch('Official test branch')
        legacy = self.make_branch('Legacy active test branch')
        self.enterContext(override_settings(PUBLIC_CLINIC_BRANCH_IDS=(official.pk,)))
        self.assertEqual(list(active_branch_queryset().values_list('pk', flat=True)), [official.pk])
        self.assertTrue(ChiNhanh.objects.get(pk=legacy.pk).dang_hoat_dong)

    def test_admin_cannot_reactivate_legacy_branch(self):
        legacy = self.make_branch('Legacy test branch')
        self.enterContext(override_settings(PUBLIC_CLINIC_BRANCH_IDS=()))
        form = AdminChiNhanhForm(instance=legacy, data={
            'ten': legacy.ten, 'dia_chi': legacy.dia_chi, 'so_dien_thoai': '', 'email': '',
            'phuong': legacy.phuong, 'lat': '10.77', 'lon': '106.7', 'dang_hoat_dong': 'on',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('dang_hoat_dong', form.errors)

    def test_reception_and_cashier_cannot_be_newly_assigned_to_legacy_branch(self):
        legacy = self.make_branch('Legacy test branch')
        self.enterContext(override_settings(PUBLIC_CLINIC_BRANCH_IDS=()))
        reception = ReceptionAccountForm(data={
            'username': 'new_reception_test', 'password': 'Reception@Strong-2026!',
            'first_name': 'Test', 'last_name': 'Reception', 'email': '', 'is_active': 'on',
            'chi_nhanh': legacy.pk,
        }, instance=User(is_active=True))
        cashier = BranchManagerForm(data={
            'username': 'new_cashier_test', 'password': 'Cashier@Strong-2026!',
            'first_name': 'Test', 'last_name': 'Cashier', 'email': '', 'is_active': 'on',
            'chi_nhanh': legacy.pk,
        }, instance=User(is_active=True))
        self.assertFalse(reception.is_valid())
        self.assertFalse(cashier.is_valid())

    def test_booking_service_rejects_legacy_branch_even_when_database_row_is_active(self):
        _, _, _, appointment = payment_fixtures()
        self.enterContext(override_settings(PUBLIC_CLINIC_BRANCH_IDS=()))
        with self.assertRaises(ValidationError):
            book_appointment(appointment)
