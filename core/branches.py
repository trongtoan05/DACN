"""The configured EyeViora branch whitelist and shared active-branch query."""
from django.conf import settings

from .models import ChiNhanh


def official_branch_ids():
    return tuple(getattr(settings, 'PUBLIC_CLINIC_BRANCH_IDS', ()))


def is_official_branch(branch_id):
    try:
        return int(branch_id) in official_branch_ids()
    except (TypeError, ValueError):
        return False


def active_branch_queryset(queryset=None):
    """Only configured EyeViora branches whose persisted active flag is on."""
    queryset = queryset if queryset is not None else ChiNhanh.objects.all()
    return queryset.filter(pk__in=official_branch_ids(), dang_hoat_dong=True)


def branch_queryset_with_existing(queryset, branch_id):
    """Include one legacy relation for safe read/edit; never expose other retired branches."""
    if branch_id and not is_official_branch(branch_id):
        queryset = queryset | ChiNhanh.objects.filter(pk=branch_id)
    return queryset.distinct()
