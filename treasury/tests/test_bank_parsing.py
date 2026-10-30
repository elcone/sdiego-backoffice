from django.test import SimpleTestCase

from treasury.bank_parsing import parse_bank_concept


class BankParsingTests(SimpleTestCase):
    def test_all_fields_and_leading_zeroes(self):
        concept = (
            "SPEI Recibido: | Institucion contraparte: BBVA Ordenante: JUAN PEREZ "
            "Cuenta Ordenante: 00123456789 | RFC Ordenante: ABC123 "
            "| Referencia: 6165 | Hora: 10:00:00 | Clave de Rastreo: 000KEY "
            "Concepto del Pago: Fee 6164 | Recibo # 6165"
        )
        self.assertEqual(
            parse_bank_concept(concept, [6164, 6165]),
            {
                "house_number": 6164,
                "origin_bank": "BBVA",
                "ordering_party": "JUAN PEREZ",
                "origin_account": "00123456789",
                "ordering_party_rfc": "ABC123",
                "tracking_key": "000KEY",
            },
        )

    def test_missing_and_incomplete_values(self):
        self.assertEqual(set(parse_bank_concept("", [6164]).values()), {None})
        metadata = parse_bank_concept("Ordenante: JUAN PEREZ Cuenta Ordenante:", [6164])
        self.assertEqual(metadata["ordering_party"], "JUAN PEREZ")
        self.assertIsNone(metadata["origin_account"])
        self.assertIsNone(
            parse_bank_concept("Cuenta Ordenante: 000123", [6164])["ordering_party"]
        )

    def test_catalog_and_numeric_boundaries(self):
        for text in ["Fee 6999", "061640", "1236164123", "61645"]:
            with self.subTest(text=text):
                self.assertIsNone(parse_bank_concept(text, [6164])["house_number"])
        self.assertEqual(parse_bank_concept("Fee (6164)", [6164])["house_number"], 6164)

    def test_segment_priority_and_fallback(self):
        self.assertEqual(
            parse_bank_concept("6165 Concepto del Pago: Fee 6164 |", [6164, 6165])[
                "house_number"
            ],
            6164,
        )
        self.assertEqual(
            parse_bank_concept("6165 Concepto del Pago: Fee 6999 |", [6164, 6165])[
                "house_number"
            ],
            6165,
        )

    def test_ambiguity_and_repeated_same_house(self):
        for text in ["6164 6165", "Concepto del Pago: 6164 6165 | 6164"]:
            metadata = parse_bank_concept(text, [6164, 6165])
            self.assertIsNone(metadata["house_number"])
            self.assertEqual(metadata["house_candidates"], [6164, 6165])
        self.assertEqual(parse_bank_concept("6164 6164", [6164])["house_number"], 6164)
