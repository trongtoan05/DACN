import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (BacSi, BacSiDichVu, BenhNhan, ChiNhanh,
                         DanhMucDichVu, DichVuChiNhanh,
                         GiaoDichThanhToan, HoaDon, Khoa, LeTanChiNhanh,
                         LichKham, QuanLyChiNhanh)
from core.forms import AdminChiNhanhForm
from core.test_vnpay import fixtures


class AdminDashboardTests(TestCase):
    def setUp(self):
        self.admin, _, self.patient_user, self.appointment = fixtures()
        self.branch = self.appointment.bac_si.chi_nhanh
        self.client.force_login(self.admin)

    def test_admin_dashboard_aggregates_branch_activity_and_settled_invoice_once(self):
        service = DanhMucDichVu.objects.create(
            ma_dich_vu='DASHBOARD_TEST', ten='Dashboard test', nhom='Kham mat',
            don_gia=Decimal('120000'), dang_ap_dung=True, cho_phep_dat_lich=True,
        )
        branch_service = DichVuChiNhanh.objects.create(
            chi_nhanh=self.branch, dich_vu=service, dang_ap_dung=True,
        )
        BacSiDichVu.objects.create(bac_si=self.appointment.bac_si,
                                   dich_vu_chi_nhanh=branch_service, dang_ap_dung=True)
        invoice = HoaDon.objects.create(
            lich_kham=self.appointment, trang_thai=HoaDon.TrangThai.DA_THANH_TOAN,
            so_tien=Decimal('120000'), ngay_thanh_toan=timezone.now(),
        )
        GiaoDichThanhToan.objects.create(
            hoa_don=invoice, so_tien=invoice.so_tien,
            phuong_thuc=GiaoDichThanhToan.PhuongThuc.VNPAY,
            chi_nhanh=self.branch, tmn_code='TEST',
            trang_thai=GiaoDichThanhToan.TrangThai.THANH_CONG,
            ma_phan_hoi='00', trang_thai_vnpay='00', ma_giao_dich_vnpay='12345678',
            vnp_pay_date='20261101120000', het_han_luc=timezone.now(),
            hoan_tat_luc=timezone.now(),
        )
        # Failed/duplicate transaction history must not multiply invoice revenue.
        GiaoDichThanhToan.objects.create(
            hoa_don=invoice, so_tien=invoice.so_tien,
            phuong_thuc=GiaoDichThanhToan.PhuongThuc.VNPAY,
            trang_thai=GiaoDichThanhToan.TrangThai.THAT_BAI,
            het_han_luc=timezone.now(), hoan_tat_luc=timezone.now(),
        )
        GiaoDichThanhToan.objects.create(
            hoa_don=invoice, so_tien=invoice.so_tien,
            phuong_thuc=GiaoDichThanhToan.PhuongThuc.VNPAY,
            trang_thai=GiaoDichThanhToan.TrangThai.THU_TRUNG,
            het_han_luc=timezone.now(), hoan_tat_luc=timezone.now(),
        )

        response = self.client.get(reverse('custom_admin_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['stats']['doanh_thu'], Decimal('120000'))
        self.assertEqual(response.context['stats']['hoa_don_da_thu'], 1)
        self.assertEqual(response.context['stats']['phieu_kham'], 1)
        self.assertEqual(response.context['stats']['dich_vu'], 1)
        row = next(row for row in response.context['branch_stats'] if row['id'] == self.branch.pk)
        self.assertEqual((row['doctor_count'], row['service_count'], row['completed_count']), (1, 1, 1))
        self.assertEqual(row['revenue'], Decimal('120000'))
        self.assertContains(response, 'Doanh thu thực thu')
        self.assertContains(response, reverse('reception_accounts'))
        self.assertNotContains(response, 'BHYT mô phỏng')

    def test_historical_revenue_stays_with_payment_branch_after_doctor_reassignment(self):
        invoice = HoaDon.objects.create(
            lich_kham=self.appointment, trang_thai=HoaDon.TrangThai.DA_THANH_TOAN,
            so_tien=Decimal('90000'), ngay_thanh_toan=timezone.now(),
        )
        GiaoDichThanhToan.objects.create(
            hoa_don=invoice, so_tien=invoice.so_tien,
            phuong_thuc=GiaoDichThanhToan.PhuongThuc.VNPAY,
            chi_nhanh=self.branch, tmn_code='TEST',
            trang_thai=GiaoDichThanhToan.TrangThai.THANH_CONG,
            ma_phan_hoi='00', trang_thai_vnpay='00', ma_giao_dich_vnpay='87654321',
            vnp_pay_date='20261101120000', het_han_luc=timezone.now(),
            hoan_tat_luc=timezone.now(),
        )
        new_branch = ChiNhanh.objects.create(
            ten='Reassigned clinic', dia_chi='New address', phuong='New ward',
            vi_tri=Point(106.8, 10.8, srid=4326), gio_mo=datetime.time(7),
            gio_dong=datetime.time(17),
        )
        new_department = Khoa.objects.create(ten='Eye', chi_nhanh=new_branch)
        doctor = self.appointment.bac_si
        doctor.chi_nhanh = new_branch
        doctor.khoa = new_department
        with self.assertRaises(ValidationError):
            doctor.save()

        # Simulate a legacy/direct database reassignment that bypassed model validation.
        BacSi.objects.filter(pk=doctor.pk).update(chi_nhanh=new_branch, khoa=new_department)

        old_branch_stats = self.client.get(reverse('custom_admin_dashboard'), {
            'branch': self.branch.pk,
        }).context['branch_stats'][0]
        new_branch_stats = self.client.get(reverse('custom_admin_dashboard'), {
            'branch': new_branch.pk,
        }).context['branch_stats'][0]
        self.assertEqual(old_branch_stats['revenue'], Decimal('90000'))
        self.assertEqual(old_branch_stats['paid_invoice_count'], 1)
        self.assertEqual(new_branch_stats['revenue'], 0)
        self.assertEqual(new_branch_stats['paid_invoice_count'], 0)

    def test_branch_and_date_filters_limit_activity_and_revenue(self):
        invoice = HoaDon.objects.create(
            lich_kham=self.appointment, trang_thai=HoaDon.TrangThai.DA_THANH_TOAN,
            so_tien=Decimal('85000'), ngay_thanh_toan=timezone.now(),
        )
        other_branch = ChiNhanh.objects.create(
            ten='Other clinic', dia_chi='Other address', phuong='Other ward',
            vi_tri=Point(106.8, 10.8, srid=4326), gio_mo=datetime.time(7),
            gio_dong=datetime.time(17),
        )

        response = self.client.get(reverse('custom_admin_dashboard'), {
            'branch': other_branch.pk,
            'date_from': (timezone.localdate() + datetime.timedelta(days=2)).isoformat(),
            'date_to': (timezone.localdate() + datetime.timedelta(days=3)).isoformat(),
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['stats']['doanh_thu'], 0)
        self.assertEqual(response.context['stats']['lich_kham'], 0)
        self.assertEqual(response.context['branch_stats'], [{
            'id': other_branch.pk, 'ten': other_branch.ten, 'dang_hoat_dong': True,
            'doctor_count': 0, 'service_count': 0, 'appointment_count': 0,
            'completed_count': 0, 'patient_count': 0, 'paid_invoice_count': 0, 'revenue': 0,
        }])
        self.assertNotEqual(invoice.lich_kham.bac_si.chi_nhanh_id, other_branch.pk)

    def test_unpaid_invoice_and_cancelled_or_absent_appointments_do_not_count_as_completed_revenue(self):
        patient_user = User.objects.create_user('dashboard_second_patient')
        patient = BenhNhan.objects.create(
            user=patient_user, ho_ten='Second patient', ngay_sinh=datetime.date(1990, 1, 1),
            gioi_tinh='nam', so_dien_thoai='0901000909', dia_chi='Test address',
        )
        appointment = LichKham.objects.create(
            benh_nhan=patient, bac_si=self.appointment.bac_si,
            ngay_kham=self.appointment.ngay_kham, gio_kham=datetime.time(10),
            trang_thai='cho', trang_thai_tiep_nhan='vang_mat',
        )
        HoaDon.objects.create(lich_kham=appointment, so_tien=Decimal('50000'))
        cancelled_user = User.objects.create_user('dashboard_cancelled_patient')
        cancelled_patient = BenhNhan.objects.create(
            user=cancelled_user, ho_ten='Cancelled patient', ngay_sinh=datetime.date(1991, 1, 1),
            gioi_tinh='nam', so_dien_thoai='0901000910', dia_chi='Test address',
        )
        LichKham.objects.create(
            benh_nhan=cancelled_patient, bac_si=self.appointment.bac_si,
            ngay_kham=self.appointment.ngay_kham, gio_kham=datetime.time(11),
            trang_thai='huy',
        )

        response = self.client.get(reverse('custom_admin_dashboard'))

        self.assertEqual(response.context['stats']['doanh_thu'], 0)
        self.assertEqual(response.context['stats']['hoa_don_da_thu'], 0)
        self.assertEqual(response.context['stats']['phieu_kham'], 1)
        self.assertEqual(response.context['status_counts']['huy'], 1)
        self.assertEqual(response.context['status_counts']['vang_mat'], 1)
        self.assertEqual(response.context['status_counts']['cho'], 0)

    def test_non_admin_roles_cannot_access_admin_statistics(self):
        doctor_user = User.objects.create_user('dashboard_doctor')
        self.appointment.bac_si.user = doctor_user
        self.appointment.bac_si.save(update_fields=['user'])
        branch_manager = User.objects.create_user('dashboard_branch_manager')
        QuanLyChiNhanh.objects.create(user=branch_manager, chi_nhanh=self.branch)
        receptionist = User.objects.create_user('dashboard_receptionist')
        LeTanChiNhanh.objects.create(user=receptionist, chi_nhanh=self.branch)

        for user in (self.patient_user, doctor_user, branch_manager, receptionist):
            with self.subTest(username=user.username):
                self.client.force_login(user)
                response = self.client.get(reverse('custom_admin_dashboard'))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url, reverse('home'))

