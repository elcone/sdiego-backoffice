import json

from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

UI_PERMISSIONS = (
    "treasury.add_banktransaction",
    "treasury.change_banktransaction",
    "treasury.add_charge",
    "treasury.add_chargetype",
    "treasury.change_chargetype",
    "treasury.delete_chargetype",
    "treasury.add_expensetype",
    "treasury.change_expensetype",
    "treasury.delete_expensetype",
)


def session_data(request):
    user = request.user
    authenticated = user.is_authenticated
    return {
        "authenticated": authenticated,
        "user": {"username": user.get_username()} if authenticated else None,
        "permissions": [p for p in UI_PERMISSIONS if user.has_perm(p)]
        if authenticated
        else [],
        "csrf_token": get_token(request),
    }


@never_cache
@require_GET
def session_view(request):
    return JsonResponse(session_data(request))


@never_cache
@require_POST
@csrf_protect
def login_view(request):
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        data = None
    if not isinstance(data, dict) or any(
        not isinstance(data.get(key), str) for key in ("username", "password")
    ):
        return JsonResponse({"detail": "Credenciales inválidas."}, status=400)
    form = AuthenticationForm(request, data=data)
    if not form.is_valid():
        return JsonResponse({"detail": "Credenciales inválidas."}, status=400)
    login(request, form.get_user())
    return JsonResponse(session_data(request))


@never_cache
@require_POST
@csrf_protect
def logout_view(request):
    logout(request)
    return JsonResponse(session_data(request))
