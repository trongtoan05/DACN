"""TASK08: regressions in the original patient authentication/profile flow."""
import datetime
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import BenhNhan
from core.views import FORGOT_PASSWORD_SESSION_KEY


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PatientAuthRegressions(TestCase):
    password = 'Patient-Test@2026!'

    def patient(self, username='patient123', phone='0901234567'):
        user = User.objects.create_user(username, email='qa@example.invalid', password=self.password)
        patient = BenhNhan.objects.create(user=user, ho_ten='Bệnh Nhân Thử', so_dien_thoai=phone,
                                         ngay_sinh='2000-01-01', gioi_tinh='nam', dia_chi='Địa chỉ thử')
        return user, patient

    def registration(self, **changes):
        data = dict(username='newpatient123', phone='0901111222', name='Bệnh Nhân Mới',
                    email='new@example.invalid', password=self.password)
        data.update(changes)
        return self.client.post(reverse('register'), data, follow=True)

    def test_alphanumeric_registration_has_valid_separate_phone(self):
        self.assertEqual(self.registration().status_code, 200)
        patient = BenhNhan.objects.get(user__username='newpatient123')
        self.assertEqual(patient.so_dien_thoai, '0901111222')
        patient.full_clean()

    def test_registration_rejects_invalid_email_and_phone(self):
        for changes in ({'email': 'invalid'}, {'phone': 'letters'}, {'phone': ''}):
            with self.subTest(changes=changes):
                self.assertEqual(self.registration(**changes).status_code, 200)
                self.assertFalse(User.objects.filter(username='newpatient123').exists())

    def test_registration_is_atomic_if_patient_save_fails(self):
        with patch.object(BenhNhan, 'save', side_effect=IntegrityError('simulated race')):
            self.assertEqual(self.registration().status_code, 200)
        self.assertFalse(User.objects.filter(username='newpatient123').exists())

    def test_anonymous_cannot_use_change_password_on_register(self):
        response = self.client.post(reverse('register'), {
            'action': 'change_password', 'current_password': 'x', 'new_password': 'y', 'confirm_password': 'y'})
        self.assertLess(response.status_code, 500)

    def test_legacy_numeric_username_can_login_and_logout(self):
        user, _ = self.patient(username='0901234567')
        response = self.client.post(reverse('login'), {'username': user.username, 'password': self.password})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)
        self.assertEqual(self.client.get(reverse('logout')).status_code, 405)
        self.client.post(reverse('logout'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_linked_patient_is_found_when_username_differs_from_phone(self):
        user, patient = self.patient()
        self.client.force_login(user)
        response = self.client.get(reverse('profile'))
        self.assertEqual(response.context['bn'], patient)
        self.assertEqual(self.client.get(reverse('medical_history')).context['benh_nhan'], patient)
        self.assertEqual(self.client.get(reverse('upcoming_appointments')).context['benh_nhan'], patient)

    def test_username_phone_collision_cannot_read_another_linked_profile(self):
        _, patient = self.patient()
        intruder = User.objects.create_user(patient.so_dien_thoai, password=self.password)
        self.client.force_login(intruder)
        response = self.client.get(reverse('profile'))
        self.assertNotContains(response, patient.ho_ten)

    def test_legacy_unlinked_profile_remains_accessible(self):
        user, patient = self.patient(username='0901234567')
        patient.user = None
        patient.save(update_fields=['user'])
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('profile')).context['bn'], patient)

    def profile_data(self, patient, **changes):
        data = dict(ho_ten=patient.ho_ten, ngay_sinh='2000-01-01', gioi_tinh='nam',
                    dia_chi='Địa chỉ mới', so_dien_thoai='0909999888')
        data.update(changes)
        return data

    def test_profile_phone_update_keeps_login_identity(self):
        user, patient = self.patient()
        self.client.force_login(user)
        self.client.post(reverse('profile'), self.profile_data(patient))
        user.refresh_from_db()
        patient.refresh_from_db()
        self.assertEqual(user.username, 'patient123')
        self.assertEqual(patient.so_dien_thoai, '0909999888')
        self.assertEqual(self.client.get(reverse('profile')).context['bn'], patient)

    def test_patient_screens_share_eyeviora_navigation(self):
        user, _ = self.patient()
        self.client.force_login(user)
        for route in ('upcoming_appointments', 'medical_history', 'profile'):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'core/css/patient.css')
                self.assertContains(response, 'Lịch sử khám')
                self.assertContains(response, 'Đặt lịch khám')
                self.assertContains(response, 'Đăng xuất')
        self.assertContains(self.client.get(reverse('profile')), 'Bệnh Nhân Thử')

    def test_profile_password_change_keeps_session(self):
        user, _ = self.patient(username='0901234567')
        self.client.force_login(user)
        response = self.client.post(reverse('profile'), dict(action='change_password',
            current_password=self.password, new_password='Replaced-Password@2026!',
            confirm_password='Replaced-Password@2026!'), follow=True)
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password('Replaced-Password@2026!'))
        self.assertIn('_auth_user_id', self.client.session)

    def start_reset(self):
        user, _ = self.patient()
        self.client.post(reverse('forgot_password'), {'step': 'username', 'username': user.username})
        self.assertEqual(len(mail.outbox), 1)
        state = self.client.session[FORGOT_PASSWORD_SESSION_KEY]
        self.assertIn(state['otp_code'], mail.outbox[0].body)
        return user, state

    def reset(self, password='Reset-Password@2026!'):
        return self.client.post(reverse('forgot_password'), dict(step='reset', new_password=password,
            confirm_password=password, username='attacker123', email='wrong@example.invalid'), follow=True)

    def test_reset_requires_otp_then_uses_verified_identity(self):
        user, state = self.start_reset()
        self.reset()
        user.refresh_from_db()
        self.assertTrue(user.check_password(self.password))
        self.client.post(reverse('forgot_password'), {'step': 'otp', 'otp': state['otp_code']})
        self.assertEqual(self.reset().status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password('Reset-Password@2026!'))
        self.assertNotIn(FORGOT_PASSWORD_SESSION_KEY, self.client.session)

    def test_verified_reset_rejects_weak_password(self):
        user, state = self.start_reset()
        self.client.post(reverse('forgot_password'), {'step': 'otp', 'otp': state['otp_code']})
        self.reset('123')
        user.refresh_from_db()
        self.assertTrue(user.check_password(self.password))

    def test_expired_reset_cannot_change_password(self):
        user, state = self.start_reset()
        state.update(verified=True, expires_at=(timezone.now() - datetime.timedelta(seconds=1)).isoformat())
        session = self.client.session
        session[FORGOT_PASSWORD_SESSION_KEY] = state
        session.save()
        self.reset()
        user.refresh_from_db()
        self.assertTrue(user.check_password(self.password))

    def test_wrong_otp_attempts_invalidate_flow(self):
        _, state = self.start_reset()
        wrong = '000000' if state['otp_code'] != '000000' else '111111'
        for _ in range(5):
            self.client.post(reverse('forgot_password'), {'step': 'otp', 'otp': wrong})
        self.assertNotIn(FORGOT_PASSWORD_SESSION_KEY, self.client.session)
