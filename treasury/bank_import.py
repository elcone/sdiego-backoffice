import csv
import io
import re
import stat
import zipfile
import zlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q

from .bank_matching import assign_house, build_trusted_evidence
from .bank_notes import update_automatic_notes
from .bank_parsing import parse_bank_concept
from .models import BankTransaction, House

MAX_ZIP_SIZE = 2 * 1024 * 1024
MAX_TXT_SIZE = 10 * 1024 * 1024
HEADERS = [
    "#",
    "Fecha Movimiento",
    "Hora",
    "Recibo",
    "Descripción",
    "Cargos",
    "Abonos",
    "Saldo",
]
BANK_FIELDS = ("transaction_timestamp", "amount", "balance", "concept")
MONTHS = dict(
    zip(
        (
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ),
        range(1, 13),
    )
)
BANK_ZONE = ZoneInfo("America/Mexico_City")


class BankImportError(Exception):
    def __init__(self, message, *, status=400, code="invalid_file", errors=None):
        self.detail = message
        self.status = status
        self.code = code
        self.errors = errors or []
        super().__init__(message)


def _invalid(message, row=None):
    raise BankImportError(
        message, errors=[{"row": row, "message": message}] if row else []
    )


def _timestamp(day, clock, row):
    match = re.fullmatch(r"([0-9]{2})-([A-Z][a-z]{2})-([0-9]{4})", day)
    if not match or not re.fullmatch(r"[0-9]{2}:[0-9]{2}:[0-9]{2}", clock):
        _invalid("Fecha u hora bancaria inválida.", row)
    try:
        local = datetime(
            int(match[3]),
            MONTHS[match[2]],
            int(match[1]),
            *map(int, clock.split(":")),
        )
    except (ValueError, KeyError):
        _invalid("Fecha u hora bancaria inválida.", row)
    first = local.replace(tzinfo=BANK_ZONE, fold=0)
    second = local.replace(tzinfo=BANK_ZONE, fold=1)
    try:
        utc = first.astimezone(UTC)
    except (OverflowError, ValueError):
        _invalid("Fecha bancaria fuera del rango permitido.", row)
    if (
        first.utcoffset() != second.utcoffset()
        or utc.astimezone(BANK_ZONE).replace(tzinfo=None) != local
    ):
        _invalid("La hora bancaria es ambigua o inexistente.", row)
    return utc


def _money(value, row, *, balance=False):
    if not re.fullmatch(
        r"-?[0-9]{1,16}\.[0-9]{2}" if balance else r"[0-9]{1,16}\.[0-9]{2}", value
    ):
        _invalid("Importe o saldo inválido; se requieren dos decimales.", row)
    result = Decimal(value)
    if not balance and result <= 0:
        _invalid("El importe debe ser positivo.", row)
    return result


def read_bank_zip(upload, bank_account):
    if getattr(upload, "size", 0) > MAX_ZIP_SIZE:
        raise BankImportError("El ZIP supera el límite de 2 MiB.", status=413)
    raw = upload.read(MAX_ZIP_SIZE + 1)
    if len(raw) > MAX_ZIP_SIZE:
        raise BankImportError("El ZIP supera el límite de 2 MiB.", status=413)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) != 1:
                _invalid("El ZIP debe contener únicamente un archivo TXT.")
            entry = entries[0]
            path = PurePosixPath(entry.filename)
            mode = entry.external_attr >> 16
            if (
                entry.is_dir()
                or path.is_absolute()
                or len(path.parts) != 1
                or "\\" in entry.filename
                or ":" in entry.filename
                or path.suffix.lower() != ".txt"
                or "\x00" in entry.orig_filename
                or (stat.S_IFMT(mode) not in (0, stat.S_IFREG))
                or entry.flag_bits & 1
                or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
            ):
                _invalid("El ZIP contiene un archivo o una ruta no permitidos.")
            if (
                entry.file_size > MAX_TXT_SIZE
                or entry.file_size > max(entry.compress_size, 1) * 100
            ):
                raise BankImportError(
                    "El contenido descomprimido supera los límites permitidos.",
                    status=413,
                )
            with archive.open(entry) as source:
                content = source.read(MAX_TXT_SIZE + 1)
            if len(content) > MAX_TXT_SIZE:
                raise BankImportError("El TXT supera el límite de 10 MiB.", status=413)
        text = content.decode("utf-8")
        if "\x00" in text:
            _invalid("El TXT contiene caracteres no permitidos.")
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except (
        zipfile.BadZipFile,
        zlib.error,
        UnicodeDecodeError,
        csv.Error,
        RuntimeError,
        NotImplementedError,
        EOFError,
        OSError,
    ):
        _invalid("El ZIP o el TXT está corrupto o tiene una codificación inválida.")
    if len(rows) < 3 or len(rows[0]) != 13 or rows[1] != HEADERS:
        _invalid(
            "Se esperaba una línea informativa y los encabezados bancarios en la segunda línea."
        )
    info = rows[0]
    if (
        not info[0].strip()
        or not re.fullmatch(r"[0-9]{8}", info[1])
        or info[2] != "Conecta BanBajio"
        or info[6] != "Rango de Fechas"
        or not re.fullmatch(r"[0-9]{1,7}", info[10])
        or int(info[10]) != len(rows) - 2
    ):
        _invalid("La línea informativa bancaria es inválida o su contador no coincide.")
    _timestamp(info[4], info[5], 1)
    records = []
    identities = set()
    for row_number, row in enumerate(rows[2:], 3):
        if len(row) != 8:
            _invalid("Número de columnas incorrecto.", row_number)
        sequence, day, clock, receipt, concept, debit, credit, balance = row
        if (
            sequence != str(row_number - 2)
            or not re.fullmatch(r"[0-9]{1,255}", receipt)
            or not concept.strip()
        ):
            _invalid("Secuencia, recibo o concepto bancario inválidos.", row_number)
        if bool(debit) == bool(credit):
            _invalid("Debe existir exclusivamente un cargo o un abono.", row_number)
        if credit and concept.startswith(("SPEI Recibido:", "Devolución de SPEI:")):
            line_type = BankTransaction.LineType.CREDIT
        elif debit and concept.startswith(
            (
                "IVA Comisión por Transferencia - Envío ; (SPEI; Banca por Internet)",
                "IVA Comisión por Administración - Renta (Banca por Internet)",
            )
        ):
            line_type = BankTransaction.LineType.COMMISSION_VAT
        elif debit and concept.startswith(
            (
                "Comisión por Transferencia - Envío ; (SPEI; Banca por Internet)",
                "Comisión por Administración - Renta (Banca por Internet) por (",
            )
        ):
            line_type = BankTransaction.LineType.COMMISSION
        else:
            line_type = (
                BankTransaction.LineType.DEBIT
                if debit
                else BankTransaction.LineType.CREDIT
            )
        identity = (bank_account, receipt, line_type)
        if identity in identities:
            _invalid("Identidad bancaria duplicada dentro del archivo.", row_number)
        identities.add(identity)
        record = BankTransaction(
            bank_account=bank_account,
            bank_receipt=receipt,
            line_type=line_type,
            transaction_timestamp=_timestamp(day, clock, row_number),
            amount=_money(debit or credit, row_number),
            balance=_money(balance, row_number, balance=True),
            concept=concept,
        )
        try:
            record.full_clean(validate_unique=False, validate_constraints=False)
        except ValidationError:
            _invalid("Los datos bancarios exceden los límites del modelo.", row_number)
        records.append(record)
    return records


def canonical_bank_account():
    accounts = list(
        BankTransaction.objects.order_by("bank_account")
        .values_list("bank_account", flat=True)
        .distinct()[:2]
    )
    if len(accounts) != 1 or not accounts[0].strip():
        raise BankImportError(
            "No se pudo determinar una única cuenta bancaria existente."
        )
    return accounts[0]


def import_bank_zip(upload):
    records = read_bank_zip(upload, canonical_bank_account())
    inserted = skipped = 0
    conflicts = []
    try:
        with transaction.atomic():
            new_records = []
            for row_number, record in sorted(
                enumerate(records, 3),
                key=lambda item: (item[1].bank_receipt, item[1].line_type),
            ):
                existing, created = BankTransaction.objects.get_or_create(
                    bank_account=record.bank_account,
                    bank_receipt=record.bank_receipt,
                    line_type=record.line_type,
                    defaults={field: getattr(record, field) for field in BANK_FIELDS},
                )
                if created:
                    inserted += 1
                    new_records.append(existing)
                else:
                    fields = [
                        field
                        for field in BANK_FIELDS
                        if getattr(existing, field) != getattr(record, field)
                    ]
                    if fields:
                        conflicts.append(
                            {
                                "row": row_number,
                                "fields": [
                                    "account_balance" if f == "balance" else f
                                    for f in fields
                                ],
                            }
                        )
                    else:
                        skipped += 1
            if conflicts:
                raise BankImportError(
                    "Existen movimientos con datos bancarios diferentes. No se insertó ningún movimiento.",
                    status=409,
                    code="bank_conflict",
                    errors=conflicts,
                )
            if new_records:
                houses = {house.number: house for house in House.objects.all()}
                evidence = build_trusted_evidence(
                    BankTransaction.objects.filter(
                        Q(is_reviewed=True)
                        | Q(metadata__assignment_source="historical_core"),
                        line_type=BankTransaction.LineType.CREDIT,
                        house__isnull=False,
                    ).select_related("house")
                )
                for bank in new_records:
                    bank.metadata.update(parse_bank_concept(bank.concept, houses))
                    assign_house(bank, houses, evidence)
                    bank.description = update_automatic_notes(
                        bank.description, bank.metadata
                    )
                    bank.save(
                        update_fields=[
                            "metadata",
                            "house",
                            "is_reviewed",
                            "description",
                        ]
                    )
    except IntegrityError:
        raise BankImportError(
            "La carga entró en conflicto con los datos existentes. No se insertó ningún movimiento.",
            status=409,
            code="bank_conflict",
        ) from None
    return {
        "found_count": len(records),
        "inserted_count": inserted,
        "skipped_count": skipped,
    }
