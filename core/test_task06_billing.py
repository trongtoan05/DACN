from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from core.billing.services import BillingError, bill_exam, validate_invoice_total
from core.exam_services.services import doctor_form_context, revision, sync_doctor_services
from core.models import ChiTietHoaDon, DichVuThucHien, HoaDon, LichKham, LichSuKhamBenh, PhieuKham
from core.test_exam_services import fixtures


class Task06BillingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin, cls.patient, cls.appointment, cls.doctor, cls.other_doctor, cls.service, *_ = fixtures()
        cls.appointment.dich_vu_dat_truoc = cls.service
        cls.appointment.save(update_fields=['dich_vu_dat_truoc'])
        cls.phieu = PhieuKham.objects.get(lich_kham=cls.appointment)

    def performed(self, *, quantity=1, booked=True, confirmed=True, service=None):
        return DichVuThucHien.objects.create(
            phieu_kham=self.phieu, dich_vu=service or self.service,
            so_luong=quantity, nguoi_thuc_hien=self.appointment.bac_si,
            la_dich_vu_dat_truoc=booked, da_xac_nhan_bac_si=confirmed,
        )

    def test_unconfirmed_booked_service_is_not_billable(self):
        self.performed(confirmed=False)
        with self.assertRaises(BillingError):
            bill_exam(self.phieu.pk, self.admin)
        self.assertFalse(HoaDon.objects.exists())

    def test_confirmed_booked_service_is_invoiced_only_after_exam_completion(self):
        LichKham.objects.filter(pk=self.appointment.pk).update(
            trang_thai='dang', trang_thai_tiep_nhan='da_tiep_nhan',
        )
        data = {'services-TOTAL_FORMS': '0', 'services-INITIAL_FORMS': '0', 'services_revision': revision([])}
        context = doctor_form_context(self.appointment, self.phieu, data)
        sync_doctor_services(
            self.appointment.pk, self.appointment.bac_si, self.doctor, self.phieu,
            context['services_formset'], context['services_revision'], booked_performed=True,
        )
        row = DichVuThucHien.objects.get(phieu_kham=self.phieu)
        self.assertTrue(row.la_dich_vu_dat_truoc)
        self.assertTrue(row.da_xac_nhan_bac_si)
        with self.assertRaises(BillingError):
            bill_exam(self.phieu.pk, self.admin)
        self.assertFalse(HoaDon.objects.exists())
        LichKham.objects.filter(pk=self.appointment.pk).update(trang_thai='xong')
        invoice = bill_exam(self.phieu.pk, self.doctor, doctor_completion=True)
        self.assertEqual(invoice.so_tien, Decimal('100000'))
        self.assertEqual(HoaDon.objects.filter(lich_kham=self.appointment).count(), 1)

    def test_booked_service_not_checked_by_doctor_creates_no_performed_row(self):
        LichKham.objects.filter(pk=self.appointment.pk).update(
            trang_thai='dang', trang_thai_tiep_nhan='da_tiep_nhan',
        )
        context = doctor_form_context(self.appointment, self.phieu, {
            'services-TOTAL_FORMS': '0', 'services-INITIAL_FORMS': '0', 'services_revision': revision([]),
        })
        sync_doctor_services(
            self.appointment.pk, self.appointment.bac_si, self.doctor, self.phieu,
            context['services_formset'], context['services_revision'], booked_performed=False,
        )
        self.assertFalse(DichVuThucHien.objects.filter(phieu_kham=self.phieu).exists())

    def test_doctor_completion_prepares_one_unpaid_invoice_without_payment(self):
        # Exercise the real completion endpoint with an in-progress exam. The
        # reusable payment fixture starts with a completed exam and history row.
        LichSuKhamBenh.objects.filter(phieu_kham=self.phieu).delete()
        self.phieu.delete()
        LichKham.objects.filter(pk=self.appointment.pk).update(
            trang_thai='dang', trang_thai_tiep_nhan='da_tiep_nhan',
        )
        self.client.force_login(self.doctor)
        response = self.client.post(reverse('doctor_exam', args=[self.appointment.pk]), {
            'action': 'complete', 'trieu_chung': 'Mờ mắt', 'chan_doan': 'Theo dõi',
            'huong_dieu_tri': 'Tái khám', 'services-TOTAL_FORMS': '0',
            'services-INITIAL_FORMS': '0', 'services_revision': revision([]),
            'booked_performed': str(self.service.pk),
        })
        self.assertEqual(response.status_code, 302, {
            'services': response.context['services_formset'].errors if response.context else None,
            'messages': [str(item) for item in response.context['messages']] if response.context else None,
        })
        invoice = HoaDon.objects.get(lich_kham=self.appointment)
        self.assertEqual(invoice.trang_thai, HoaDon.TrangThai.CHUA_THANH_TOAN)
        self.assertEqual(invoice.so_tien, Decimal('100000'))
        self.assertFalse(invoice.giao_dichs.exists())
        self.assertTrue(ChiTietHoaDon.objects.get(hoa_don=invoice).la_dich_vu_dat_truoc)

    def test_confirmed_booked_service_is_charged_once_with_source_snapshot(self):
        row = self.performed()
        invoice = bill_exam(self.phieu.pk, self.admin)
        line = ChiTietHoaDon.objects.get(hoa_don=invoice)
        self.assertEqual(invoice.so_tien, Decimal('100000'))
        self.assertEqual(line.dich_vu_thuc_hien_id, row.pk)
        self.assertTrue(line.la_dich_vu_dat_truoc)
        validate_invoice_total(invoice)

    def test_repeated_performed_quantity_is_billed_by_quantity(self):
        self.performed(quantity=2)
        invoice = bill_exam(self.phieu.pk, self.admin)
        line = ChiTietHoaDon.objects.get(hoa_don=invoice)
        self.assertEqual(line.so_luong, 2)
        self.assertEqual(line.thanh_tien, Decimal('200000'))
        self.assertEqual(invoice.so_tien, Decimal('200000'))

