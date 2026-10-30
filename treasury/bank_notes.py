import re

START = "<!-- treasury:notes:start -->"
END = "<!-- treasury:notes:end -->"
BLOCK = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)


def update_automatic_notes(description, metadata):
    matching = metadata.get("matching", {})
    candidates = matching.get("candidate_houses", [])
    notes = []
    if matching.get("ambiguous"):
        houses = " y ".join(str(number) for number in candidates)
        source = {
            "origin_account": "La cuenta ordenante aparece vinculada",
            "ordering_party": "El ordenante aparece vinculado",
            "concept": "El concepto hace referencia",
        }[matching["source"]]
        notes = [
            f"{source} a las casas {houses}.",
            "La identificación automática es ambigua.",
        ]
    elif matching.get("source") == "ordering_party" and candidates:
        notes = [
            f"El ordenante sugiere la casa {candidates[0]}.",
            "La sugerencia por nombre requiere revisión humana.",
        ]
    elif "matching" in metadata and not candidates:
        notes = ["No se encontró una casa confiable para este ingreso."]
    block = (
        START
        + "\nNotas para el tesorero:\n"
        + "\n".join(f"- {note}" for note in notes)
        + "\n"
        + END
        if notes
        else ""
    )
    if BLOCK.search(description):
        first = True

        def replace(match):
            nonlocal first
            replacement = block if first else ""
            first = False
            return replacement

        return BLOCK.sub(replace, description)
    if block:
        separator = "\n" if description and not description.endswith("\n") else ""
        return description + separator + block
    return description
