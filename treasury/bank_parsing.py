import re

LABELS = re.compile(
    r"(Instituci[oó]n contraparte|Cuenta Ordenante|RFC Ordenante|Clave de Rastreo|"
    r"Concepto del Pago|Ordenante|Referencia|Hora|Recibo #)\s*:?\s*",
    re.IGNORECASE,
)
HOUSE_NUMBER = re.compile(r"(?<!\d)6\d{3}(?!\d)")


def parse_bank_concept(concept, house_numbers):
    metadata = dict.fromkeys(
        [
            "house_number",
            "origin_bank",
            "ordering_party",
            "origin_account",
            "ordering_party_rfc",
            "tracking_key",
        ]
    )
    fields = {
        "institucion contraparte": "origin_bank",
        "institución contraparte": "origin_bank",
        "ordenante": "ordering_party",
        "cuenta ordenante": "origin_account",
        "rfc ordenante": "ordering_party_rfc",
        "clave de rastreo": "tracking_key",
    }
    segments = {}
    matches = list(LABELS.finditer(concept))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(concept)
        label = match[1].casefold()
        segments[label] = concept[match.end() : end].split("|", 1)[0].strip() or None
        if label in fields:
            metadata[fields[label]] = segments[label]
    existing = {int(number) for number in house_numbers}
    for text in [segments.get("concepto del pago") or "", concept]:
        candidates = sorted(
            {int(value) for value in HOUSE_NUMBER.findall(text)} & existing
        )
        if candidates:
            metadata["house_number"] = candidates[0] if len(candidates) == 1 else None
            if len(candidates) > 1:
                metadata["house_candidates"] = candidates
            break
    return metadata
