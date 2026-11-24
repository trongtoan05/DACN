from django.urls import path
from . import views
from .payments import views as payment_views
from .payments import cashiers as cashier_views
from .service_catalog import views as catalog_views
from .receipts import views as receipt_views
from . import reception as reception_views
from . import doctor_absences as absence_views
from . import exam_corrections as correction_views
from . import ratings as rating_views

urlpatterns = [
    path('le-tan/', reception_views.reception_dashboard, name='reception_dashboard'),
    path('le-tan/lich-bi-anh-huong/', absence_views.affected_appointments_page, name='affected_appointments'),
    path('le-tan/lich/<int:pk>/chuyen-bac-si/', absence_views.reassign_affected_appointment, name='reassign_affected_appointment'),
    path('le-tan/dat-lich/', reception_views.reception_book, name='reception_book'),
    path('le-tan/lich/<int:pk>/doi/', reception_views.reception_reschedule, name='reception_reschedule'),
    path('le-tan/lich/<int:pk>/huy/', reception_views.reception_cancel, name='reception_cancel'),
    path('le-tan/lich/<int:pk>/<str:action>/', reception_views.reception_transition, name='reception_transition'),
    path('quan-tri/le-tan/', reception_views.receptionist_accounts, name='reception_accounts'),
    path('quan-tri/le-tan/<int:pk>/sua/', reception_views.receptionist_accounts, name='reception_account_edit'),
    path('benh-an/anh/<path:path>', views.private_exam_image, name='private_exam_image'),
    path('thanh-toan/hoa-don/<int:invoice_id>/phieu/xem/', receipt_views.receipt_pdf, name='receipt_view'),
    path('thanh-toan/hoa-don/<int:invoice_id>/phieu/tai/', receipt_views.receipt_pdf, {'download': True}, name='receipt_download'),
    path('thanh-toan/hoa-don/<int:invoice_id>/phieu/in/', receipt_views.receipt_print, name='receipt_print'),
    path('thanh-toan/hoa-don/<int:invoice_id>/email/thu-lai/', receipt_views.receipt_retry, name='receipt_email_retry'),
    path('quan-tri/thu-ngan/', cashier_views.branch_manager_list, name='cashier_list'),
    path('quan-tri/thu-ngan/tao/', cashier_views.branch_manager_form, name='cashier_create'),
    path('quan-tri/thu-ngan/<int:pk>/sua/', cashier_views.branch_manager_form, name='cashier_edit'),
    path('quan-tri/thu-ngan/hoa-don/', payment_views.payment_list, {'monitor': True}, name='cashier_monitor'),
    path('quan-tri/thu-ngan/hoa-don/<int:lich_id>/', payment_views.payment_detail, {'monitor': True}, name='cashier_monitor_detail'),
    path('quan-tri/thu-ngan/hoa-don/<int:invoice_id>/ket-thuc-doi-soat/', payment_views.reconciliation_close, name='cashier_monitor_reconcile_close'),
    path('thanh-toan/hoa-don/<int:invoice_id>/tien-mat/', payment_views.payment_cash, name='payment_cash'),

    path('quan-tri/dich-vu/', catalog_views.service_list, name='service_list'),
    path('quan-tri/dich-vu/tao/', catalog_views.service_form, name='service_create'),
    path('quan-tri/dich-vu/<int:pk>/', catalog_views.service_detail, name='service_detail'),
    path('quan-tri/dich-vu/<int:pk>/sua/', catalog_views.service_form, name='service_edit'),
    path('quan-tri/dich-vu/<int:pk>/trang-thai/', catalog_views.service_status, name='service_status'),

    path('thanh-toan/', payment_views.payment_list, name='payment_list'),
    path('thanh-toan/lich-kham/<int:lich_id>/', payment_views.payment_detail, name='payment_detail'),
    path('thanh-toan/lich-kham/<int:lich_id>/dich-vu/', payment_views.services_save, name='payment_services_save'),
    path('thanh-toan/lich-kham/<int:lich_id>/hoa-don/', payment_views.invoice_create, name='invoice_create'),
    path('thanh-toan/hoa-don/<int:invoice_id>/vnpay/', payment_views.payment_start, name='payment_start'),
    path('thanh-toan/vnpay/return/', payment_views.vnpay_return, name='vnpay_return'),
    path('thanh-toan/vnpay/ipn/', payment_views.vnpay_ipn, name='vnpay_ipn'),

    # Trang chủ
    path("", views.home, name="home"),
    path("dich-vu/", views.public_service_catalog, name="service_catalog_public"),
    path("gioi-thieu/", views.gioi_thieu, name="gioi_thieu"),

    # Chi tiết phòng khám
    path("chi-nhanh/<int:id>/", views.branch_detail, name="branch_detail"),

    # Đặt lịch theo chi nhánh và dịch vụ
    path("dat-lich/<int:bv_id>/", views.dat_lich, name="dat_lich"),
    path("dat-lich/", views.dat_lich, name="dat_lich_picker"),
    path("api/dat-lich/tuy-chon/", views.appointment_options_api, name="appointment_options_api"),
    path("api/clinic-route/", views.clinic_route_api, name="clinic_route_api"),

    # Auth
    path("login/", views.user_login, name="login"),
    path("quan-tri/", views.custom_admin_dashboard, name="custom_admin_dashboard"),
    path("quan-tri/chi-nhanh/", views.custom_admin_clinics, name="custom_admin_clinics"),
    path("quan-tri/chi-nhanh/tao/", views.custom_admin_clinic_create, name="custom_admin_clinic_create"),
    path("quan-tri/chi-nhanh/<int:pk>/sua/", views.custom_admin_clinic_edit, name="custom_admin_clinic_edit"),
    path("quan-tri/chi-nhanh/<int:pk>/xoa/", views.custom_admin_clinic_delete, name="custom_admin_clinic_delete"),
    path("quan-tri/khoa/", views.custom_admin_departments, name="custom_admin_departments"),
    path("quan-tri/khoa/tao/", views.custom_admin_department_create, name="custom_admin_department_create"),
    path("quan-tri/khoa/<int:pk>/sua/", views.custom_admin_department_edit, name="custom_admin_department_edit"),
    path("quan-tri/khoa/<int:pk>/xoa/", views.custom_admin_department_delete, name="custom_admin_department_delete"),
    path("quan-tri/bac-si/", views.custom_admin_doctors, name="custom_admin_doctors"),
    path("quan-tri/bac-si/nghi-dot-xuat/", absence_views.doctor_absence_admin, name="doctor_absence_admin"),
    path("quan-tri/bac-si/nghi-dot-xuat/tao/", absence_views.doctor_absence_create, name="doctor_absence_create"),
    path("quan-tri/bac-si/nghi-dot-xuat/<int:pk>/dong/", absence_views.doctor_absence_close, name="doctor_absence_close"),
    path("quan-tri/phieu-kham/yeu-cau-chinh-sua/", correction_views.correction_review_list, name="exam_correction_review_list"),
    path("quan-tri/phieu-kham/yeu-cau-chinh-sua/<int:request_id>/", correction_views.correction_review, name="exam_correction_review"),
    path("quan-tri/danh-gia/", rating_views.rating_admin_list, name="rating_admin_list"),
    path("quan-tri/bac-si/tao/", views.custom_admin_doctor_create, name="custom_admin_doctor_create"),
    path("quan-tri/bac-si/<int:pk>/sua/", views.custom_admin_doctor_edit, name="custom_admin_doctor_edit"),
    path("quan-tri/bac-si/<int:pk>/xoa/", views.custom_admin_doctor_delete, name="custom_admin_doctor_delete"),
    path("quan-tri/api/khoa/", views.custom_admin_departments_api, name="custom_admin_departments_api"),
    path("quan-tri/du-lieu/<slug:model_key>/", views.custom_admin_model_list, name="custom_admin_model_list"),
    path("quan-tri/du-lieu/<slug:model_key>/tao/", views.custom_admin_model_create, name="custom_admin_model_create"),
    path("quan-tri/du-lieu/<slug:model_key>/<int:pk>/sua/", views.custom_admin_model_edit, name="custom_admin_model_edit"),
    path("quan-tri/du-lieu/<slug:model_key>/<int:pk>/xoa/", views.custom_admin_model_delete, name="custom_admin_model_delete"),
    path("register/", views.register, name="register"),
    path("logout/", views.user_logout, name="logout"),
    path("quen-mat-khau/", views.forgot_password, name="forgot_password"),
    path("lien-he/", views.contact_feedback, name="contact_feedback"),
    path("thong-bao/", views.notifications, name="notifications"),
    path("lich-kham-sap-toi/", views.upcoming_appointments, name="upcoming_appointments"),
    path("lich-kham/<int:lich_id>/huy/", views.cancel_appointment, name="cancel_appointment"),
    path("lich-su-kham/", views.medical_history, name="medical_history"),
    path("phieu-kham/<int:phieu_id>/", views.phieu_kham_detail, name="phieu_kham_detail"),
    path("bac-si/kham/<int:lich_id>/", views.doctor_exam, name="doctor_exam"),
    path("bac-si/", views.bac_si_home, name="bac_si_home"),
    path("bac-si/benh-nhan/<int:benh_nhan_id>/", views.bac_si_patient_detail, name="bac_si_patient_detail"),
    path("bac-si/lich/<int:lich_id>/bat-dau/", views.start_exam, name="start_exam"),
    path("bac-si/thong-bao/", views.bac_si_notifications, name="bac_si_notifications"),
    path("bac-si/lich-lam-viec/", views.bac_si_schedule, name="bac_si_schedule"),
    path("bac-si/phieu-kham/<int:phieu_id>/sua/", views.bac_si_phieu_kham_edit, name="bac_si_phieu_kham_edit"),
    path("bac-si/phieu-kham/<int:exam_id>/de-nghi-sua/", correction_views.correction_request, name="exam_correction_request"),
    path("bac-si/lich-su-lien-chi-nhanh/", correction_views.cross_branch_history, name="cross_branch_history"),
    path("lich-kham/<int:appointment_id>/danh-gia/", rating_views.patient_rating, name="patient_rating"),
    path("profile/", views.profile, name="profile"),

]
