from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect


def has_manager_access(user):
    return bool(
        getattr(user, "is_authenticated", False)
        and (
            getattr(user, "is_superuser", False)
            or getattr(user, "is_staff", False)
            or user.groups.filter(name="Managers").exists()
        )
    )


def has_sales_sensitive_access(user):
    return has_manager_access(user)


def has_order_reconciliation_access(user):
    return bool(getattr(user, "is_authenticated", False))


def has_admin_access(user):
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_superuser", False)
    )


def manager_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if has_manager_access(request.user):
            return view_func(request, *args, **kwargs)

        messages.error(request, "Employee accounts can only access daily operations.")
        return redirect("dashboard")

    return wrapped


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if has_admin_access(request.user):
            return view_func(request, *args, **kwargs)

        messages.error(request, "Only admin accounts can modify historical orders.")
        return redirect("dashboard")

    return wrapped
