import datetime

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.appointments import available_booking_slots, book_appointment
from core.models import (BacSi, BacSiDichVu, BacSiNghiDotXuat, DichVuChiNhanh,
                         GioLamViecBacSi, LichKham, LeTanChiNhanh, LogHeThong,
                         ThongBao)
from core import tests as fixture_tests


class DoctorAbsenceTests(TestCase):
    def setUp(self):
        fixture_tests.AppointmentBookingTests.setUp(self)
        self.client.logout()
        self.admin = User.objects.create_superuser('absence-admin', 'admin@example.invalid', 'Secret@2026!')
        self.receptionist = User.objects.create_user('absence-reception', password='Reception@2026!')
        LeTanChiNhanh.objects.create(user=self.receptionist, chi_nhanh=self.clinic)
        self.day = self.next_monday
        self.start = timezone.make_aware(datetime.datetime.combine(self.day, datetime.time(9)))
        self.end = self.start + datetime.timedelta(minutes=30)
        self.appointment = LichKham.objects.create(
            benh_nhan=self.patient, bac_si=self.doctor, dich_vu_dat_truoc=self.service,
            ngay_kham=self.day, gio_kham=datetime.time(9),
        )

    def create_absence(self, **overrides):
        values = {'bac_si': self.doctor, 'bat_dau': self.start, 'ket_thuc': self.end,
                  'ly_do': 'Nghỉ đột xuất theo yêu cầu', 'nguoi_tao': self.admin}
        values.update(overrides)
        return BacSiNghiDotXuat.objects.create(**values)

    def test_admin_can_record_absence_and_booking_is_not_automatically_changed(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('doctor_absence_create'), {
            'bac_si': self.doctor.pk,
            'bat_dau': self.start.strftime('%Y-%m-%dT%H:%M'),
            'ket_thuc': self.end.strftime('%Y-%m-%dT%H:%M'),
            'ly_do': 'Nghỉ đột xuất theo yêu cầu',
        })
        self.assertRedirects(response, reverse('doctor_absence_admin'))
        absence = BacSiNghiDotXuat.objects.get()
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.bac_si_id, self.doctor.pk)
        self.assertEqual(self.appointment.trang_thai, 'cho')
        self.assertTrue(LogHeThong.objects.filter(model_name='BacSiNghiDotXuat', object_id=absence.pk,
                                                  nguoi_thuc_hien=self.admin).exists())

    def test_overlapping_leave_periods_are_rejected(self):
        self.create_absence()
        overlapping = BacSiNghiDotXuat(
            bac_si=self.doctor, bat_dau=self.start + datetime.timedelta(minutes=10),
            ket_thuc=self.end + datetime.timedelta(minutes=10), ly_do='Lịch nghỉ bị trùng', nguoi_tao=self.admin,
        )
        with self.assertRaises(ValidationError):
            overlapping.full_clean()

    def test_absence_removes_booking_slot_and_backend_booking_rejects_bypass(self):
        self.create_absence()
        self.assertNotIn(datetime.time(9), available_booking_slots(
            self.doctor, self.day, patient_id=self.patient.pk, service_ids=[self.service.pk],
        ))
        attempt = LichKham(benh_nhan=self.patient, bac_si=self.doctor,
                           dich_vu_dat_truoc=self.service, ngay_kham=self.day,
                           gio_kham=datetime.time(9))
        with self.assertRaises(ValidationError):
            book_appointment(attempt, selected_services=[self.service])

    def test_reception_page_is_branch_scoped_and_cancel_notifies_patient(self):
        self.create_absence()
        self.client.force_login(self.receptionist)
        page = self.client.get(reverse('affected_appointments'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f'#{self.appointment.pk}')
        response = self.client.post(reverse('reception_cancel', args=[self.appointment.pk]),
                                    {'ly_do': 'Lịch cần hủy do bác sĩ nghỉ'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ThongBao.objects.filter(nguoi_nhan=self.patient.user,
                                                tieu_de='Lịch khám đã được hủy').exists())
        self.assertTrue(LogHeThong.objects.filter(model_name='LichKham', object_id=self.appointment.pk,
                                                  nguoi_thuc_hien=self.receptionist).exists())

    def test_non_admin_cannot_manage_absence(self):
        self.client.force_login(self.receptionist)
        self.assertEqual(self.client.get(reverse('doctor_absence_admin')).status_code, 403)

    def test_reception_can_reassign_only_to_doctor_with_service_and_open_slot(self):
        self.create_absence()
        replacement_user = User.objects.create_user('replacement-doctor')
        replacement = BacSi.objects.create(
            user=replacement_user, ho_ten='Bác sĩ thay thế', chuyen_khoa=self.doctor.chuyen_khoa,
            khoa=self.department, chi_nhanh=self.clinic, so_dien_thoai='0909000099',
        )
        BacSiDichVu.objects.create(bac_si=replacement, dich_vu_chi_nhanh=self.branch_service)
        GioLamViecBacSi.objects.create(
            bac_si=replacement, thu=1, gio_bat_dau=datetime.time(7),
            gio_ket_thuc=datetime.time(17), nghi=False,
        )
        self.client.force_login(self.receptionist)
        response = self.client.post(reverse('reassign_affected_appointment', args=[self.appointment.pk]), {
            'bac_si': replacement.pk, 'ly_do': 'Điều phối do bác sĩ nghỉ đột xuất',
        })
        self.assertRedirects(response, reverse('affected_appointments'))
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.bac_si_id, replacement.pk)
        self.assertTrue(LogHeThong.objects.filter(model_name='LichKham', object_id=self.appointment.pk,
                                                  nguoi_thuc_hien=self.receptionist,
                                                  ghi_chu__contains='Điều phối do bác sĩ nghỉ').exists())

