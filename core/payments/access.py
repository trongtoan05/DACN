"""Clinic scope shared by HTTP reads and locked payment writes."""
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied

from core.models import LichKham, LeTanChiNhanh


def receptionist_profile(user, *, lock=False):
    if not user.is_authenticated:
        return None
    profiles = LeTanChiNhanh.objects
    if lock:
        profiles = profiles.select_for_update()
    return profiles.filter(user_id=user.pk).first()


def is_receptionist(user):
    return bool(receptionist_profile(user))


def can_collect(user):
    if not user.is_authenticated or not user.is_active:
        return False
    reception = receptionist_profile(user)
    # Counter collection is part of the receptionist role and branch assignment.
    # Admin/staff permissions do not create a separate cashier account type.
    return bool(reception and not (
        user.is_staff or user.is_superuser or hasattr(user, 'bac_si')
        or hasattr(user, 'benh_nhan')
    ))


def can_admin(user):
    """Global administration is explicit; ``is_staff`` only enables Django staff UI."""
    return (
        user.is_authenticated
        and user.is_active
        and (user.is_superuser or not LeTanChiNhanh.objects.filter(user_id=user.pk).exists())
        and (
            user.is_superuser
            or (user.is_staff and user.has_perm('core.quan_tri_he_thong'))
        )
    )


def scoped_appointments(user):
    reception = receptionist_profile(user)
    if reception and can_collect(user):
        return LichKham.objects.filter(bac_si__chi_nhanh_id=reception.chi_nhanh_id)
    if can_collect(user) or can_admin(user):
        return LichKham.objects.all()
    return LichKham.objects.none()


def require_scope(user, appointment):
    """Call inside atomic after locking appointment; serialize reassignment too."""
    reception = receptionist_profile(user, lock=True)
    fresh_user = User.objects.get(pk=user.pk)
    if not can_collect(fresh_user):
        raise PermissionDenied('Tài khoản không có quyền thu tiền tại quầy.')
    if reception and reception.chi_nhanh_id != appointment.bac_si.chi_nhanh_id:
        raise PermissionDenied('Hóa đơn không thuộc chi nhánh được phân công.')
    if reception and appointment.trang_thai_tiep_nhan != 'da_tiep_nhan':
        raise PermissionDenied('Chỉ thu tại quầy sau khi lễ tân đã xác nhận bệnh nhân có mặt.')
