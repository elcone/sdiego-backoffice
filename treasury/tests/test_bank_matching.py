from types import SimpleNamespace

from django.test import SimpleTestCase

from treasury.bank_matching import build_trusted_evidence, identify_house
from treasury.bank_notes import END, START, update_automatic_notes


class BankMatchingTests(SimpleTestCase):
    def bank(self, number=6164, reviewed=False, historical=False, line_type="CREDIT"):
        return SimpleNamespace(
            house_id=number,
            house=SimpleNamespace(number=number),
            line_type=line_type,
            is_reviewed=reviewed,
            metadata={
                "origin_account": "001",
                "ordering_party": "JUAN PEREZ",
                "assignment_source": "historical_core" if historical else None,
            },
        )

    def test_explicit_house_priority(self):
        result = identify_house(
            {"house_number": 6164, "origin_account": "001"}, ({"001": {6165}}, {})
        )
        self.assertEqual(result["candidate_houses"], [6164])
        self.assertEqual(result["source"], "concept")

    def test_account_single_and_multiple_houses(self):
        for candidates in [{6164}, {6164, 6165}]:
            result = identify_house(
                {"origin_account": "001"}, ({"001": candidates}, {})
            )
            self.assertEqual(result["candidate_houses"], sorted(candidates))
            self.assertEqual(result["ambiguous"], len(candidates) > 1)
            self.assertEqual(result["source"], "origin_account")

    def test_name_normalization_and_no_partial_matching(self):
        evidence = ({}, {"juan perez": {6164}})
        self.assertEqual(
            identify_house({"ordering_party": " Juan  PEREZ "}, evidence)[
                "candidate_houses"
            ],
            [6164],
        )
        for name in ["JUAN", "JUAN PEREZ LOPEZ", "JUAN PÉREZ"]:
            self.assertEqual(
                identify_house({"ordering_party": name}, evidence)["candidate_houses"],
                [],
            )

    def test_name_ambiguity_and_no_evidence(self):
        result = identify_house(
            {"ordering_party": "JUAN"}, ({}, {"juan": {6164, 6165}})
        )
        self.assertTrue(result["ambiguous"])
        self.assertEqual(result["source"], "ordering_party")
        self.assertEqual(
            identify_house({}, ({}, {})),
            {"source": None, "candidate_houses": [], "ambiguous": False},
        )

    def test_only_trusted_assigned_credits_are_evidence(self):
        unreviewed = self.bank(number=6165)
        debit = self.bank(number=6166, reviewed=True, line_type="DEBIT")
        pending = self.bank(number=None, reviewed=True)
        accounts, parties = build_trusted_evidence(
            [self.bank(historical=True), unreviewed, debit, pending]
        )
        self.assertEqual(accounts, {"001": {6164}})
        self.assertEqual(parties, {"juan perez": {6164}})
        accounts, _ = build_trusted_evidence([self.bank(reviewed=True)])
        self.assertEqual(accounts, {"001": {6164}})

    def test_explicit_ambiguity_does_not_fall_back_to_account(self):
        result = identify_house(
            {"house_candidates": [6164, 6165], "origin_account": "001"},
            ({"001": {6164}}, {}),
        )
        self.assertTrue(result["ambiguous"])
        self.assertEqual(result["source"], "concept")


class BankNotesTests(SimpleTestCase):
    def metadata(self, source="origin_account", candidates=None):
        return {
            "matching": {
                "source": source,
                "candidate_houses": candidates or [6164, 6165],
                "ambiguous": True,
            }
        }

    def test_append_and_idempotency(self):
        notes = update_automatic_notes("Manual note", self.metadata())
        self.assertTrue(notes.startswith("Manual note\n"))
        self.assertIn("6164 y 6165", notes)
        self.assertEqual(notes.count(START), 1)
        self.assertEqual(update_automatic_notes(notes, self.metadata()), notes)

    def test_replace_and_preserve_outside_text(self):
        original = f"  Before\n{START}\nOld notes\n{END}\nAfter  "
        updated = update_automatic_notes(original, self.metadata())
        self.assertTrue(updated.startswith("  Before\n"))
        self.assertTrue(updated.endswith("\nAfter  "))
        self.assertNotIn("Old notes", updated)
        self.assertEqual(updated.count(START), 1)

    def test_remove_only_the_block_and_collapse_duplicates(self):
        original = f"Before{START}old{END}After"
        self.assertEqual(update_automatic_notes(original, {}), "BeforeAfter")
        doubled = original + START + "duplicate" + END
        self.assertEqual(
            update_automatic_notes(doubled, self.metadata()).count(START), 1
        )

    def test_suggestion_and_no_evidence(self):
        metadata = {
            "matching": {
                "source": "ordering_party",
                "candidate_houses": [6164],
                "ambiguous": False,
            }
        }
        self.assertIn("revisión humana", update_automatic_notes("", metadata))
        metadata["matching"] = {
            "source": None,
            "candidate_houses": [],
            "ambiguous": False,
        }
        self.assertIn("No se encontró", update_automatic_notes("", metadata))
