from rest_framework.permissions import BasePermission


class CanChangeBankTransaction(BasePermission):
    message = "No tiene permiso para modificar movimientos bancarios."

    def has_permission(self, request, view):
        return request.method != "PATCH" or request.user.has_perm(
            "treasury.change_banktransaction"
        )
