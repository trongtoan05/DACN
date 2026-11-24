import datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections
from django.test import TestCase, TransactionTestCase

from core.appointments import available_booking_slots, book_appointment
from core.models import (BacSiDichVu, BenhNhan, DanhMucDichVu, DichVuChiNhanh,
                         LichKham)
from core import tests as fixture_tests


class ServiceDurationBookingTests(TestCase):
    def setUp(self):
        fixture_tests.AppointmentBookingTests.setUp(self)
        self.service.thoi_luong_phut = 30
        self.service.save(update_fields=['thoi_luong_phut', 'ngay_cap_nhat'])
        self.second_service = DanhMucDichVu.objects.create(
            ma_dich_vu='DURATION_SECOND', ten='Dịch vụ thứ hai', nhom='Khám mắt',
            don_gia=100000, cho_phep_dat_lich=True, thoi_luong_phut=45,
        )
        assignment = DichVuChiNhanh.objects.create(chi_nhanh=self.clinic, dich_vu=self.second_service)
        BacSiDichVu.objects.create(bac_si=self.doctor, dich_vu_chi_nhanh=assignment)

    def test_selected_service_durations_are_summed_and_snapshot_kept_on_reschedule(self):
        appointment = LichKham(
            benh_nhan=self.patient, bac_si=self.doctor, dich_vu_dat_truoc=self.service,
            ngay_kham=self.next_monday, gio_kham=datetime.time(9),
        )
        book_appointment(appointment, selected_services=[self.service, self.second_service])
        self.assertEqual(appointment.thoi_luong_du_kien, 75)
        self.service.thoi_luong_phut = 60
        self.service.save(update_fields=['thoi_luong_phut', 'ngay_cap_nhat'])
        appointment.gio_kham = datetime.time(11)
        book_appointment(appointment, selected_services=[self.service, self.second_service])
        appointment.refresh_from_db()
        self.assertEqual(appointment.thoi_luong_du_kien, 75)

    def test_slots_use_full_service_duration_for_conflicts_and_closing_time(self):
        second_patient = BenhNhan.objects.create(
            ho_ten='Bệnh nhân thời lượng', ngay_sinh=datetime.date(1990, 1, 1),
            gioi_tinh='nu', so_dien_thoai='0901222888', dia_chi='HCM',
        )
        LichKham.objects.create(
            benh_nhan=second_patient, bac_si=self.doctor, ngay_kham=self.next_monday,
            gio_kham=datetime.time(10), thoi_luong_du_kien=60,
        )
        slots = available_booking_slots(
            self.doctor, self.next_monday, patient_id=self.patient.pk,
            service_ids=[self.service.pk, self.second_service.pk],
        )
        self.assertIn(datetime.time(8, 0), slots)
        self.assertNotIn(datetime.time(9), slots)
        self.assertNotIn(datetime.time(16), slots)
        self.assertIn(datetime.time(15, 30), slots)

    def test_backend_rejects_overlapping_interval_even_when_start_times_differ(self):
        second_patient = BenhNhan.objects.create(
            ho_ten='Bệnh nhân khác thời lượng', ngay_sinh=datetime.date(1990, 1, 1),
            gioi_tinh='nu', so_dien_thoai='0901222889', dia_chi='HCM',
        )
        LichKham.objects.create(
            benh_nhan=second_patient, bac_si=self.doctor, ngay_kham=self.next_monday,
            gio_kham=datetime.time(10), thoi_luong_du_kien=30,
        )
        attempt = LichKham(
            benh_nhan=self.patient, bac_si=self.doctor, dich_vu_dat_truoc=self.service,
            ngay_kham=self.next_monday, gio_kham=datetime.time(9, 30),
        )
        with self.assertRaises(ValidationError):
            book_appointment(attempt, selected_services=[self.service, self.second_service])

    def test_service_duration_validation_limits_values(self):
        self.service.thoi_luong_phut = 0
        with self.assertRaises(ValidationError):
            self.service.full_clean()


class ServiceDurationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        fixture_tests.AppointmentBookingTests.setUp(self)
        self.second_patient = BenhNhan.objects.create(
            ho_ten='Bệnh nhân đồng thời', ngay_sinh=datetime.date(1990, 1, 1),
            gioi_tinh='nu', so_dien_thoai='0901222890', dia_chi='HCM',
        )
        self.second_service = DanhMucDichVu.objects.create(
            ma_dich_vu='DURATION_CONCURRENT', ten='Dịch vụ đồng thời', nhom='Khám mắt',
            don_gia=100000, cho_phep_dat_lich=True, thoi_luong_phut=45,
        )
        assignment = DichVuChiNhanh.objects.create(chi_nhanh=self.clinic, dich_vu=self.second_service)
        BacSiDichVu.objects.create(bac_si=self.doctor, dich_vu_chi_nhanh=assignment)

    def test_simultaneous_bookings_with_overlapping_durations_only_allow_one(self):
        barrier = Barrier(2)

        def book(index):
            close_old_connections()
            try:
                patient = [self.patient, self.second_patient][index]
                start = [datetime.time(9), datetime.time(9, 30)][index]
                appointment = LichKham(
                    benh_nhan_id=patient.pk, bac_si_id=self.doctor.pk,
                    ngay_kham=self.next_monday, gio_kham=start,
                )
                barrier.wait(timeout=10)
                try:
                    book_appointment(appointment, selected_services=[self.service, self.second_service])
                    return 'saved'
                except (ValidationError, IntegrityError):
                    return 'blocked'
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(book, index) for index in range(2)]
            outcomes = [future.result(timeout=25) for future in results]
        self.assertCountEqual(outcomes, ['saved', 'blocked'])
        self.assertEqual(LichKham.objects.filter(
            bac_si=self.doctor, ngay_kham=self.next_monday,
        ).exclude(trang_thai='huy').count(), 1)
