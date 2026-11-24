from django.contrib.auth.models import User
from django.contrib.staticfiles import finders
from django.template.loader import get_template, render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse


class FinalUIAuditTests(TestCase):
    def test_account_recovery_uses_public_eye_viora_shell_and_keeps_form(self):
        response = self.client.get(reverse('forgot_password'))

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'EyeViora', response.content)
        self.assertIn(b'logoEyeViora.png', response.content)
        self.assertIn(b'core/css/public.css', response.content)
        self.assertContains(response, 'name="username"')

    def test_patient_notifications_use_patient_shell_and_empty_state(self):
        user = User.objects.create_user('final-ui-patient')
        self.client.force_login(user)

        response = self.client.get(reverse('notifications'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'EyeViora')
        self.assertContains(response, 'core/css/patient.css')
        self.assertContains(response, 'patient-notification-empty')

    @override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'])
    def test_not_found_page_uses_branded_status_shell(self):
        response = self.client.get('/final-ui-audit-missing-page/')

        self.assertEqual(response.status_code, 404)
        self.assertIn(b'EyeViora', response.content)
        self.assertIn(b'logoEyeViora.png', response.content)
        self.assertIn(b'core/css/public.css', response.content)

    def test_error_templates_render_with_official_static_assets(self):
        for name, code in (('403.html', '403'), ('404.html', '404'), ('500.html', '500')):
            with self.subTest(template=name):
                html = get_template(name).render({})
                self.assertIn(code, html)
                self.assertIn('EyeViora', html)
                self.assertIn('logoEyeViora.png', html)
                self.assertIn('core/css/public.css', html)

    def test_receipt_email_uses_eye_viora_brand_without_sandbox_details(self):
        html = render_to_string('core/receipts/payment_success_email.html', {
            'patient': 'Bệnh nhân', 'invoice_id': 17, 'amount': '100.000 VND',
            'method': 'Tiền mặt', 'paid_at': '03/11/2026 10:00', 'clinic': 'Chi nhánh mẫu',
        })

        self.assertIn('EyeViora', html)
        self.assertNotIn('MedCare GIS', html)
        self.assertNotIn('Mailtrap', html)
        self.assertIn('100.000 VND', html)

    def test_official_logo_and_role_stylesheets_are_resolvable(self):
        for asset in (
            'core/img/EyeViora/logoEyeViora.png', 'core/css/public.css',
            'core/css/patient.css', 'core/css/doctor.css', 'core/css/admin.css',
            'core/css/counter.css', 'core/css/operations.css',
        ):
            with self.subTest(asset=asset):
                self.assertIsNotNone(finders.find(asset))
