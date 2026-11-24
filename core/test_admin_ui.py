import datetime

from django.contrib.auth.models import User
from django.contrib.gis.geos import Point
from django.test import TestCase
from django.urls import reverse

from core.models import ChiNhanh, DanhMucDichVu, LeTanChiNhanh, QuanLyChiNhanh
from core.test_vnpay import fixtures


class AdminInterfaceTests(TestCase):
    def setUp(self):
        self.admin, _, _, self.appointment = fixtures()
        self.branch = self.appointment.bac_si.chi_nhanh
        self.client.force_login(self.admin)

    def test_admin_pages_render_with_eye_viora_shell(self):
        service = DanhMucDichVu.objects.create(
            ma_dich_vu='ADMIN-UI', ten='Dịch vụ giao diện', nhom='Khám mắt', don_gia=120000,
        )
        model_keys = (
            'noi-dung-gioi-thieu', 'chi-nhanh', 'gio-lam-viec-chi-nhanh',
            'dich-vu-chi-nhanh', 'khoa', 'bac-si', 'bac-si-dich-vu', 'tai-khoan',
            'gio-lam-viec-bac-si', 'benh-nhan', 'lich-kham', 'phieu-kham',
            'phieu-kham-hinh-anh', 'lich-su-kham-benh', 'log-lich-kham',
            'log-he-thong', 'thong-bao',
        )
        urls = [reverse('custom_admin_dashboard'), reverse('service_list'),
                reverse('service_detail', args=[service.pk]), reverse('reception_accounts'),
                reverse('cashier_list'), reverse('cashier_create'), reverse('cashier_monitor')]
        urls.extend(reverse('custom_admin_model_list', args=[key]) for key in model_keys)

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'EyeViora')
                self.assertContains(response, 'logoEyeViora.png')
                if url == reverse('cashier_monitor'):
                    self.assertContains(response, 'desk-sidebar')
                else:
                    self.assertContains(response, 'admin-sidebar')

    def test_branch_admin_defaults_to_active_and_keeps_history_filterable(self):
        retired = ChiNhanh.objects.create(
            ten='Chi nhánh lịch sử kiểm thử', dia_chi='Địa chỉ cũ', phuong='Phường cũ',
            vi_tri=Point(106.8, 10.8, srid=4326), gio_mo=datetime.time(7),
            gio_dong=datetime.time(17), dang_hoat_dong=False,
        )

        active_list = self.client.get(reverse('custom_admin_model_list', args=['chi-nhanh']))
        self.assertEqual(active_list.status_code, 200)
        self.assertContains(active_list, self.branch.ten)
        self.assertNotContains(active_list, retired.ten)
        self.assertContains(active_list, '1 chi nhánh')

        history_list = self.client.get(reverse('custom_admin_model_list', args=['chi-nhanh']), {
            'branch_scope': 'history',
        })
        self.assertContains(history_list, retired.ten)
        self.assertContains(history_list, 'Ngừng hoạt động · lịch sử')

    def test_dashboard_defaults_to_active_branches_and_can_include_history(self):
        retired = ChiNhanh.objects.create(
            ten='Lịch sử dashboard kiểm thử', dia_chi='Địa chỉ cũ', phuong='Phường cũ',
            vi_tri=Point(106.9, 10.9, srid=4326), gio_mo=datetime.time(7),
            gio_dong=datetime.time(17), dang_hoat_dong=False,
        )
        active = self.client.get(reverse('custom_admin_dashboard'))
        self.assertEqual(active.context['branch_status'], 'active')
        self.assertEqual(active.context['stats']['chi_nhanh'], 1)
        self.assertNotIn(retired.pk, [row['id'] for row in active.context['branch_stats']])

        history = self.client.get(reverse('custom_admin_dashboard'), {'branch_status': 'history'})
        self.assertEqual(history.context['stats']['chi_nhanh'], 0)
        self.assertIn(retired.pk, [row['id'] for row in history.context['branch_stats']])

    def test_staff_assignment_pages_identify_inactive_historical_branch(self):
        retired = ChiNhanh.objects.create(
            ten='Bình Chánh · hồ sơ cũ', dia_chi='Địa chỉ lịch sử', phuong='Bình Chánh',
            vi_tri=Point(106.7, 10.7, srid=4326), gio_mo=datetime.time(7),
            gio_dong=datetime.time(17), dang_hoat_dong=False,
        )
        receptionist = User.objects.create_user('letan-history-ui', is_active=True)
        LeTanChiNhanh.objects.create(user=receptionist, chi_nhanh=retired)
        manager = User.objects.create_user('manager-history-ui', is_active=True)
        QuanLyChiNhanh.objects.create(user=manager, chi_nhanh=retired)

        reception_page = self.client.get(reverse('reception_accounts'))
        manager_page = self.client.get(reverse('cashier_list'))
        self.assertContains(reception_page, receptionist.username)
        self.assertContains(manager_page, manager.username)
        for page in (reception_page, manager_page):
            self.assertContains(page, retired.ten)
            self.assertContains(page, 'Ngừng hoạt động · lịch sử')

    def test_non_admin_does_not_receive_admin_navigation(self):
        non_admin = User.objects.create_user('admin-ui-non-admin')
        self.client.force_login(non_admin)
        response = self.client.get(reverse('custom_admin_dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('home'))
