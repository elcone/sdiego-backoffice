import csv
import io
import stat
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from rest_framework.test import APIClient

from treasury.bank_import import (
    HEADERS,
    BankImportError,
    canonical_bank_account,
    import_bank_zip,
    read_bank_zip,
)
from treasury.bank_parsing import parse_bank_concept
from treasury.models import BankTransaction, ExpenseType, House


def bank_row(
    receipt="100",
    *,
    concept="SPEI Recibido: prueba",
    debit="",
    credit="10.00",
    day="28-Oct-2026",
    clock="23:59:59",
):
    return ["1", day, clock, receipt, concept, debit, credit, "100.00"]


def bank_zip(rows=None, *, name="movimientos.txt", headers=None, info=None):
    rows = rows if rows is not None else [bank_row()]
    text = io.StringIO(newline="")
    writer = csv.writer(text)
    writer.writerow(
        info
        or [
            "Titular",
            "12345678",
            "Conecta BanBajio",
            "01",
            "29-Oct-2026",
            "11:04:47",
            "Rango de Fechas",
            "TODOS",
            "01-Oct-2026 al 29-Oct-2026",
            "",
            str(len(rows)),
            "100.00",
            "100.00",
        ]
    )
    writer.writerow(headers or HEADERS)
    for i, row in enumerate(rows, 1):
        writer.writerow([str(i), *row[1:]])
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, text.getvalue().encode("utf-8"))
    return SimpleUploadedFile(
        "bank.zip", stream.getvalue(), content_type="application/zip"
    )


def seed_bank():
    return BankTransaction.objects.create(
        bank_account="000000000001",
        bank_receipt="seed",
        line_type="CREDIT",
        transaction_timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        amount="1.00",
        balance="1.00",
        concept="Registro existente",
    )


class BankZipTests(SimpleTestCase):
    def test_concepts_preserve_special_types_and_use_bank_direction(self):
        cases = [
            ("SPEI Recibido: prueba", "", "10.00", "CREDIT"),
            ("Devolución de SPEI: prueba", "", "10.00", "CREDIT"),
            ("SPEI Enviado: prueba", "10.00", "", "DEBIT"),
            (
                "Comisión por Transferencia - Envío ; (SPEI; Banca por Internet)",
                "10.00",
                "",
                "COMMISSION",
            ),
            (
                "Comisión por Administración - Renta (Banca por Internet) por (prueba)",
                "10.00",
                "",
                "COMMISSION",
            ),
            (
                "IVA Comisión por Transferencia - Envío ; (SPEI; Banca por Internet)",
                "10.00",
                "",
                "COMMISSION_VAT",
            ),
            (
                "IVA Comisión por Administración - Renta (Banca por Internet)",
                "10.00",
                "",
                "COMMISSION_VAT",
            ),
            ("Pago de Servicios prueba", "10.00", "", "DEBIT"),
            ("Otro abono", "", "10.00", "CREDIT"),
            ("SPEI Recibido: prueba", "10.00", "", "DEBIT"),
            (
                "Comisión por Administración - Renta (Banca por Internet) por (prueba)",
                "",
                "10.00",
                "CREDIT",
            ),
        ]
        for concept, debit, credit, expected in cases:
            with self.subTest(concept=concept, debit=debit, credit=credit):
                record = read_bank_zip(
                    bank_zip([bank_row(concept=concept, debit=debit, credit=credit)]),
                    "canonical",
                )[0]
                self.assertEqual(record.line_type, expected)
                self.assertEqual(str(record.amount), "10.00")
                self.assertEqual(record.concept, concept)
                self.assertIsNone(record.house_id)
                self.assertIsNone(record.expense_type_id)

    def test_unknown_concept_rejects_inconsistent_amounts(self):
        for debit, credit in [
            ("", ""),
            ("1.00", "2.00"),
            ("0.00", ""),
            ("", "0.00"),
            ("-1.00", ""),
            ("", "NaN"),
            ("1.001", ""),
            ("0.00", "10.00"),
        ]:
            with (
                self.subTest(debit=debit, credit=credit),
                self.assertRaises(BankImportError) as error,
            ):
                read_bank_zip(
                    bank_zip(
                        [
                            bank_row(
                                concept="Pago de Servicios", debit=debit, credit=credit
                            )
                        ]
                    ),
                    "canonical",
                )
            self.assertEqual(error.exception.errors[0]["row"], 3)

    def test_reference_zip_and_timezone(self):
        with (Path(settings.BASE_DIR) / "resources/RCM1793293487232.zip").open(
            "rb"
        ) as upload:
            records = read_bank_zip(upload, "canonical")
        self.assertEqual(len(records), 122)
        self.assertEqual(len({(r.bank_receipt, r.line_type) for r in records}), 122)
        self.assertTrue(all(r.amount > 0 for r in records))
        self.assertEqual(sum(r.line_type == "COMMISSION" for r in records), 24)
        self.assertEqual(sum(r.line_type == "COMMISSION_VAT" for r in records), 24)
        parsed = read_bank_zip(bank_zip(), "canonical")[0]
        self.assertEqual(
            parsed.transaction_timestamp, datetime(2026, 10, 29, 5, 59, 59, tzinfo=UTC)
        )

    def test_invalid_fields_and_unknown_formats(self):
        for changes in [
            {1: "31-Feb-2026"},
            {2: "24:00:00"},
            {3: ""},
            {4: "SPEI Recibido: \x00"},
            {1: "31-Dec-9999"},
            {5: "1.00"},
            {6: "NaN"},
            {6: "1.001"},
            {6: "0.00"},
            {6: "-1.00"},
            {7: "Infinity"},
            {1: "03-Apr-2022", 2: "02:30:00"},
            {1: "30-Oct-2022", 2: "01:30:00"},
        ]:
            with self.subTest(changes=changes):
                row = bank_row()
                for key, value in changes.items():
                    row[key] = value
                with self.assertRaises(BankImportError):
                    read_bank_zip(bank_zip([row]), "canonical")
        with self.assertRaises(BankImportError):
            read_bank_zip(bank_zip(headers=HEADERS[::-1]), "canonical")
        with self.assertRaises(BankImportError):
            read_bank_zip(bank_zip([bank_row(), bank_row()]), "canonical")

    def test_header_and_crc_validation(self):
        for rows in [[], [bank_row() + ["extra"]], [bank_row(), []]]:
            with self.subTest(rows=rows), self.assertRaises(BankImportError):
                read_bank_zip(bank_zip(rows), "canonical")
        with self.assertRaises(BankImportError):
            read_bank_zip(bank_zip(info=["invalid"] * 13), "canonical")
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("bank.txt", "marker")
        corrupt = stream.getvalue().replace(b"marker", b"broken", 1)
        with self.assertRaises(BankImportError):
            read_bank_zip(io.BytesIO(corrupt), "canonical")

    def test_dst_uses_zone_rules_not_fixed_offset(self):
        record = read_bank_zip(
            bank_zip([bank_row(day="01-Jul-2021", clock="12:00:00")]), "canonical"
        )[0]
        self.assertEqual(
            record.transaction_timestamp, datetime(2021, 7, 1, 17, tzinfo=UTC)
        )

    def test_zip_security(self):
        for name in [
            "../evil.txt",
            "/evil.txt",
            "folder/file.txt",
            "C:\\evil.txt",
            "file.csv",
        ]:
            with self.subTest(name=name), self.assertRaises(BankImportError):
                read_bank_zip(bank_zip(name=name), "canonical")
        for payload in [b"not a zip", bank_zip().read()[:-20]]:
            with self.assertRaises(BankImportError):
                read_bank_zip(io.BytesIO(payload), "canonical")
        for kind in ["multiple", "symlink", "bomb", "encoding"]:
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                if kind == "multiple":
                    archive.writestr("a.txt", "a")
                    archive.writestr("b.txt", "b")
                elif kind == "symlink":
                    info = zipfile.ZipInfo("link.txt")
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    archive.writestr(info, "target")
                else:
                    archive.writestr(
                        "a.txt", b"a" * 100000 if kind == "bomb" else b"\xff"
                    )
            with self.subTest(kind=kind), self.assertRaises(BankImportError):
                read_bank_zip(io.BytesIO(stream.getvalue()), "canonical")
        with self.assertRaises(BankImportError) as error:
            read_bank_zip(io.BytesIO(b"x" * (2 * 1024 * 1024 + 1)), "canonical")
        self.assertEqual(error.exception.status, 413)


class BankImportTests(TestCase):
    def setUp(self):
        self.seed = seed_bank()

    def test_enrichment_assigns_only_new_credits_and_is_idempotent(self):
        house = House.objects.create(number=6164)
        other = House.objects.create(number=6165)
        self.seed.house = house
        self.seed.is_reviewed = True
        self.seed.metadata = parse_bank_concept(
            "Cuenta Ordenante: 000123 | Ordenante: Juan Perez |", [6164, 6165]
        )
        self.seed.save()
        before = BankTransaction.objects.values().get(pk=self.seed.pk)
        rows = [
            bank_row("100", concept="Concepto del Pago: 6165 |"),
            bank_row("101", concept="Cuenta Ordenante: 000123 |"),
            bank_row("102", concept="Concepto del Pago: 6164 6165 |"),
            bank_row("103", concept="Ordenante: Juan Perez |"),
            bank_row(
                "104", concept="Concepto del Pago: 6164 |", debit="10.00", credit=""
            ),
            bank_row("105", concept="Sin coincidencia"),
        ]
        self.assertEqual(import_bank_zip(bank_zip(rows))["inserted_count"], 6)
        imported = {
            bank.bank_receipt: bank
            for bank in BankTransaction.objects.exclude(pk=self.seed.pk)
        }
        self.assertEqual(imported["100"].house_id, other.pk)
        self.assertEqual(imported["101"].house_id, house.pk)
        for receipt in ("102", "103", "104", "105"):
            self.assertIsNone(imported[receipt].house_id)
        self.assertTrue(imported["102"].metadata["matching"]["ambiguous"])
        self.assertEqual(
            imported["103"].metadata["matching"]["source"], "ordering_party"
        )
        self.assertNotIn("matching", imported["104"].metadata)
        self.assertTrue(all(not bank.is_reviewed for bank in imported.values()))
        self.assertEqual(BankTransaction.objects.values().get(pk=self.seed.pk), before)
        snapshot = list(BankTransaction.objects.order_by("pk").values())
        self.assertEqual(import_bank_zip(bank_zip(rows))["skipped_count"], 6)
        self.assertEqual(
            list(BankTransaction.objects.order_by("pk").values()), snapshot
        )

    def test_ambiguous_trusted_account_does_not_assign(self):
        for number in (6164, 6165):
            BankTransaction.objects.create(
                bank_account=self.seed.bank_account,
                bank_receipt=str(number),
                line_type="CREDIT",
                transaction_timestamp=self.seed.transaction_timestamp,
                amount="10.00",
                balance="100.00",
                concept="Cuenta Ordenante: 000123 |",
                house=House.objects.create(number=number),
                is_reviewed=True,
                metadata={"origin_account": "000123"},
            )
        import_bank_zip(bank_zip([bank_row(concept="Cuenta Ordenante: 000123 |")]))
        bank = BankTransaction.objects.get(bank_receipt="100")
        self.assertIsNone(bank.house_id)
        self.assertTrue(bank.metadata["matching"]["ambiguous"])

    def test_enrichment_failure_rolls_back_import(self):
        before = list(BankTransaction.objects.values())
        with (
            patch(
                "treasury.bank_import.assign_house", side_effect=RuntimeError("Error")
            ),
            self.assertRaises(RuntimeError),
        ):
            import_bank_zip(bank_zip())
        self.assertEqual(list(BankTransaction.objects.values()), before)

    def test_automatic_assignments_do_not_become_trusted_evidence(self):
        House.objects.create(number=6164)
        import_bank_zip(
            bank_zip(
                [
                    bank_row(
                        "100",
                        concept="Concepto del Pago: 6164 | Cuenta Ordenante: 000123 |",
                    ),
                    bank_row("101", concept="Cuenta Ordenante: 000123 |"),
                ]
            )
        )
        self.assertIsNone(BankTransaction.objects.get(bank_receipt="101").house_id)
        import_bank_zip(
            bank_zip([bank_row("102", concept="Cuenta Ordenante: 000123 |")])
        )
        self.assertIsNone(BankTransaction.objects.get(bank_receipt="102").house_id)

    def test_unknown_concepts_idempotency_duplicates_and_atomicity(self):
        rows = [
            bank_row("100", concept="Pago de Servicios", debit="10.00", credit=""),
            bank_row("101", concept="Otro abono"),
        ]
        self.assertEqual(import_bank_zip(bank_zip(rows))["inserted_count"], 2)
        self.assertEqual(import_bank_zip(bank_zip(rows))["skipped_count"], 2)
        before = list(BankTransaction.objects.order_by("pk").values())
        for invalid_rows in [
            [rows[0], rows[0]],
            [
                bank_row("001"),
                bank_row("100", concept="Pago de Servicios", debit="11.00", credit=""),
            ],
            [
                bank_row("001"),
                bank_row("102", concept="Pago de Servicios", debit="10.00"),
            ],
        ]:
            with self.subTest(rows=invalid_rows), self.assertRaises(BankImportError):
                import_bank_zip(bank_zip(invalid_rows))
            self.assertEqual(
                list(BankTransaction.objects.order_by("pk").values()), before
            )

    def test_new_idempotent_overlap_and_administration(self):
        self.assertEqual(
            import_bank_zip(bank_zip()),
            {"found_count": 1, "inserted_count": 1, "skipped_count": 0},
        )
        bank = BankTransaction.objects.get(bank_receipt="100")
        bank.house = House.objects.create(number=6101)
        bank.expense_type = ExpenseType.objects.create(name="Prueba")
        bank.description = "Nota manual"
        bank.is_reviewed = True
        bank.metadata = {"manual": True}
        bank.save()
        before = BankTransaction.objects.values().get(pk=bank.pk)
        self.assertEqual(import_bank_zip(bank_zip())["skipped_count"], 1)
        self.assertEqual(BankTransaction.objects.values().get(pk=bank.pk), before)
        result = import_bank_zip(bank_zip([bank_row(), bank_row("101")]))
        self.assertEqual(
            result, {"found_count": 2, "inserted_count": 1, "skipped_count": 1}
        )
        self.assertEqual(BankTransaction.objects.count(), 3)

    def test_each_bank_difference_conflicts_and_rolls_back(self):
        import_bank_zip(bank_zip())
        before = list(BankTransaction.objects.order_by("pk").values())
        for changes in [
            {1: "27-Oct-2026"},
            {6: "11.00"},
            {7: "101.00"},
            {4: "SPEI Recibido: diferente"},
        ]:
            row = bank_row()
            for key, value in changes.items():
                row[key] = value
            with (
                self.subTest(changes=changes),
                self.assertRaises(BankImportError) as error,
            ):
                import_bank_zip(bank_zip([bank_row("001"), row]))
            self.assertEqual(error.exception.status, 409)
            self.assertEqual(
                list(BankTransaction.objects.order_by("pk").values()), before
            )

    def test_reference_reimport_matches_initial_bank_source(self):
        BankTransaction.objects.all().delete()
        with (Path(settings.BASE_DIR) / "resources/movimientos_bancarios.csv").open(
            encoding="utf-8", newline=""
        ) as source:
            BankTransaction.objects.bulk_create(
                [
                    BankTransaction(
                        **{
                            **row,
                            "transaction_timestamp": datetime.fromisoformat(
                                row["transaction_timestamp"]
                            ).replace(tzinfo=UTC),
                        }
                    )
                    for row in csv.DictReader(source)
                ]
            )
        before = list(BankTransaction.objects.order_by("pk").values())
        with (Path(settings.BASE_DIR) / "resources/RCM1793293487232.zip").open(
            "rb"
        ) as upload:
            self.assertEqual(
                import_bank_zip(upload),
                {"found_count": 122, "inserted_count": 0, "skipped_count": 122},
            )
        self.assertEqual(list(BankTransaction.objects.order_by("pk").values()), before)

    def test_canonical_account_requires_one_existing_account(self):
        self.assertEqual(canonical_bank_account(), self.seed.bank_account)
        BankTransaction.objects.filter(pk=self.seed.pk).delete()
        with self.assertRaises(BankImportError):
            canonical_bank_account()

    def test_api_permissions_csrf_and_results(self):
        client = APIClient(enforce_csrf_checks=True)
        url = "/api/bank-transaction/import/"
        self.assertEqual(
            client.post(url, {"file": bank_zip()}, format="multipart").status_code, 403
        )
        user = get_user_model().objects.create_user(username="importer")
        client.force_login(user)
        client.get("/api/bank-transaction/")
        token = client.cookies["csrftoken"].value
        self.assertEqual(
            client.post(
                url, {"file": bank_zip()}, format="multipart", HTTP_X_CSRFTOKEN=token
            ).status_code,
            403,
        )
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="treasury", codename="add_banktransaction"
            )
        )
        self.assertEqual(
            client.post(url, {"file": bank_zip()}, format="multipart").status_code, 403
        )
        response = client.post(
            url, {"file": bank_zip()}, format="multipart", HTTP_X_CSRFTOKEN=token
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["inserted_count"], 1)
        conflict = bank_row(credit="11.00")
        response = client.post(
            url,
            {"file": bank_zip([bank_row("001"), conflict])},
            format="multipart",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["inserted_count"], 0)
        self.assertEqual(response.data["errors"][0]["fields"], ["amount"])
        self.assertFalse(BankTransaction.objects.filter(bank_receipt="001").exists())
        malformed = client.post(
            url,
            {"file": SimpleUploadedFile("fake.zip", b"invalid")},
            format="multipart",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(malformed.data["code"], "invalid_file")
        large = SimpleUploadedFile("large.zip", b"x" * (2 * 1024 * 1024 + 1))
        self.assertEqual(
            client.post(
                url, {"file": large}, format="multipart", HTTP_X_CSRFTOKEN=token
            ).status_code,
            413,
        )
        duplicate_uploads = client.post(
            url,
            {"file": [bank_zip(), bank_zip()]},
            format="multipart",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(duplicate_uploads.status_code, 400)
        self.assertEqual(
            client.post(
                "/api/bank-transaction/", {}, format="json", HTTP_X_CSRFTOKEN=token
            ).status_code,
            405,
        )
        summary = client.get("/api/bank-transaction/summary/").data
        self.assertTrue(summary["can_import"])
        self.assertIsNotNone(summary["latest_transaction_timestamp"])
        response = client.post(url, {}, format="multipart", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 400)


class ConcurrentBankImportTests(TransactionTestCase):
    def test_concurrent_identical_and_conflicting_uploads(self):
        self.assertEqual(connection.vendor, "postgresql")
        seed_bank()
        original = BankTransaction.objects.get_or_create
        for different in (False, True):
            barrier = Barrier(2)

            def synchronized(*args, barrier=barrier, **kwargs):
                if kwargs["bank_receipt"] == "001":
                    barrier.wait(timeout=10)
                return original(*args, **kwargs)

            def worker(amount):
                close_old_connections()
                try:
                    return import_bank_zip(
                        bank_zip([bank_row("001", credit=amount), bank_row("002")])
                    )
                except BankImportError as error:
                    return error.status
                finally:
                    close_old_connections()

            with (
                patch.object(
                    BankTransaction.objects, "get_or_create", side_effect=synchronized
                ),
                ThreadPoolExecutor(max_workers=2) as pool,
            ):
                futures = [
                    pool.submit(worker, amount)
                    for amount in ["10.00", "11.00" if different else "10.00"]
                ]
                results = [future.result(timeout=20) for future in futures]
            if different:
                self.assertEqual(sum(result == 409 for result in results), 1)
            else:
                self.assertEqual(
                    sorted(result["inserted_count"] for result in results), [0, 2]
                )
                self.assertEqual(
                    sorted(result["skipped_count"] for result in results), [0, 2]
                )
            self.assertEqual(BankTransaction.objects.count(), 3)
            BankTransaction.objects.exclude(bank_receipt="seed").delete()
