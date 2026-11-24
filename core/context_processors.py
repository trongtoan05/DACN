"""Values every server-rendered page needs: brand name and shared navigation rights."""
from django.conf import settings
from django.db import DatabaseError

from core.payments.access import can_admin, can_collect
from core.models import BenhNhan


def _safe(rule, user):
    """Never let navigation break a page: permission lookups touch the database."""
    try:
        return bool(rule(user))
    except (DatabaseError, AttributeError):
        return False


def brand(request):
    user = getattr(request, 'user', None)
    authenticated = bool(user is not None and user.is_authenticated)
    nav_can_admin = authenticated and _safe(can_admin, user)
    nav_can_collect = authenticated and _safe(can_collect, user)
    patient = None
    if authenticated and not nav_can_admin and not nav_can_collect:
        try:
            if not hasattr(user, 'bac_si') and not hasattr(user, 'le_tan_chi_nhanh'):
                patient = BenhNhan.objects.filter(user=user).values_list('ho_ten', flat=True).first()
                if not patient:
                    patient = BenhNhan.objects.filter(
                        user__isnull=True, so_dien_thoai=user.username,
                    ).values_list('ho_ten', flat=True).first()
        except (DatabaseError, AttributeError, BenhNhan.DoesNotExist):
            patient = None
    return {
        'brand_name': getattr(settings, 'SITE_BRAND_NAME', 'EyeViora'),
        'brand_code': getattr(settings, 'SITE_BRAND_CODE', 'EYEViora'),
        'nav_can_collect': nav_can_collect,
        'nav_can_admin': nav_can_admin,
        'nav_is_patient': bool(patient),
        'nav_patient_name': patient or '',
    }
