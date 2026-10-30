from collections import defaultdict


def normalize_ordering_party(value):
    return " ".join((value or "").split()).casefold()


def build_trusted_evidence(transactions):
    accounts = defaultdict(set)
    parties = defaultdict(set)
    for bank in transactions:
        if bank.line_type != "CREDIT" or bank.house_id is None:
            continue
        if not (
            bank.is_reviewed
            or bank.metadata.get("assignment_source") == "historical_core"
        ):
            continue
        number = bank.house.number
        account = (bank.metadata.get("origin_account") or "").strip()
        party = normalize_ordering_party(bank.metadata.get("ordering_party"))
        if account:
            accounts[account].add(number)
        if party:
            parties[party].add(number)
    return accounts, parties


def identify_house(metadata, evidence):
    accounts, parties = evidence
    candidates = metadata.get("house_candidates", [])
    if candidates:
        return {"source": "concept", "candidate_houses": candidates, "ambiguous": True}
    if metadata.get("house_number") is not None:
        return {
            "source": "concept",
            "candidate_houses": [metadata["house_number"]],
            "ambiguous": False,
        }
    account = (metadata.get("origin_account") or "").strip()
    candidates = sorted(accounts.get(account, set()))
    if candidates:
        return {
            "source": "origin_account",
            "candidate_houses": candidates,
            "ambiguous": len(candidates) > 1,
        }
    party = normalize_ordering_party(metadata.get("ordering_party"))
    candidates = sorted(parties.get(party, set()))
    return {
        "source": "ordering_party" if candidates else None,
        "candidate_houses": candidates,
        "ambiguous": len(candidates) > 1,
    }
