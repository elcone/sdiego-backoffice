from collections.abc import Mapping

from rest_framework import serializers

from treasury.models import BankTransaction, Charge, ChargeType, ExpenseType, House


class HouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = House
        fields = ("id", "number")


class ChargeTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChargeType
        fields = ("id", "name")


class ExpenseTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseType
        fields = ("id", "name")


class ChargeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Charge
        fields = (
            "id",
            "house",
            "charge_type",
            "period",
            "due_date",
            "amount",
            "description",
        )


class BankTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankTransaction
        fields = (
            "id",
            "bank_account",
            "bank_receipt",
            "line_type",
            "transaction_timestamp",
            "amount",
            "balance",
            "concept",
            "house",
            "expense_type",
            "description",
        )
        read_only_fields = (
            "id",
            "bank_account",
            "bank_receipt",
            "line_type",
            "transaction_timestamp",
            "amount",
            "balance",
            "concept",
        )

    def to_internal_value(self, data):
        if isinstance(data, Mapping) and self.instance is not None:
            allowed = {"description"}
            if self.instance.line_type == BankTransaction.LineType.CREDIT:
                allowed.add("house")
            else:
                allowed.add("expense_type")
            forbidden = set(data) - allowed
            if forbidden:
                raise serializers.ValidationError(
                    {
                        key: "Este campo no se puede modificar para este movimiento."
                        for key in sorted(forbidden)
                    }
                )
        return super().to_internal_value(data)
