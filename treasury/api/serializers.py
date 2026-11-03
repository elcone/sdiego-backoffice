from collections.abc import Mapping

from rest_framework import serializers

from treasury.models import BankTransaction, Charge, ChargeType, ExpenseType, House


class HouseSerializer(serializers.ModelSerializer):
    total_charges = serializers.DecimalField(
        max_digits=24, decimal_places=2, read_only=True
    )
    total_payments = serializers.DecimalField(
        max_digits=24, decimal_places=2, read_only=True
    )
    balance = serializers.DecimalField(max_digits=24, decimal_places=2, read_only=True)

    class Meta:
        model = House
        fields = ("id", "number", "total_charges", "total_payments", "balance")


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

    def validate_period(self, value):
        if value.day != 1:
            raise serializers.ValidationError(
                "El periodo debe ser el primer día del mes."
            )
        return value


class BulkChargeSerializer(ChargeSerializer):
    class Meta(ChargeSerializer.Meta):
        fields = ("charge_type", "period", "due_date", "amount", "description")

    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            forbidden = set(data) - set(self.fields)
            if forbidden:
                raise serializers.ValidationError(
                    {
                        key: "Este campo no se admite en la generación masiva."
                        for key in sorted(forbidden)
                    }
                )
        return super().to_internal_value(data)


class BankTransactionSerializer(serializers.ModelSerializer):
    house_label = serializers.CharField(
        source="house.number", read_only=True, default=None
    )
    expense_type_label = serializers.CharField(
        source="expense_type.name", read_only=True, default=None
    )
    account_balance = serializers.DecimalField(
        source="balance", max_digits=18, decimal_places=2, read_only=True
    )

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
            "account_balance",
            "concept",
            "house",
            "house_label",
            "expense_type",
            "expense_type_label",
            "description",
            "metadata",
            "is_reviewed",
        )
        read_only_fields = (
            "id",
            "bank_account",
            "bank_receipt",
            "line_type",
            "transaction_timestamp",
            "amount",
            "balance",
            "account_balance",
            "concept",
            "metadata",
        )

    def to_internal_value(self, data):
        if isinstance(data, Mapping) and self.instance is not None:
            allowed = {"description", "is_reviewed"}
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
