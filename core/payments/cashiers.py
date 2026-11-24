"""Compatibility redirects for the retired branch-manager account screens."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.views.decorators.http import require_GET


@login_required
@require_GET
def branch_manager_list(request):
    return redirect('reception_accounts')


@login_required
@require_GET
def branch_manager_form(request, pk=None):
    return redirect('reception_accounts')
