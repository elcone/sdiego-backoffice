from django.contrib import admin

from .models import BankTransaction, Charge, ChargeType, ExpenseType, House


@admin.register(House)
class HouseAdmin(admin.ModelAdmin):
    list_display = ["number"]
    search_fields = ["number"]


@admin.register(ChargeType)
class ChargeTypeAdmin(admin.ModelAdmin):
    list_display = ["name"]
    search_fields = ["name"]


@admin.register(ExpenseType)
class ExpenseTypeAdmin(admin.ModelAdmin):
    list_display = ["name"]
    search_fields = ["name"]


@admin.register(BankTransaction)
class BankTransactionAdmin(admin.ModelAdmin):
    list_display = [
        "bank_account",
        "bank_receipt",
        "line_type",
        "transaction_timestamp",
        "amount",
        "balance",
        "concept",
        "house",
        "expense_type",
    ]
    search_fields = ["bank_account", "bank_receipt", "concept", "house__number"]
    list_filter = ["line_type", "expense_type"]


@admin.register(Charge)
class ChargeAdmin(admin.ModelAdmin):
    list_display = ["house", "charge_type", "period", "due_date", "amount"]
    search_fields = ["house__number"]
    list_filter = ["charge_type"]
