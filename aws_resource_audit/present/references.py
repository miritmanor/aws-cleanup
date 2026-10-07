"""The connections column: every reference a resource has, in words, from either end
(incoming links are worded from the target's side)."""


def _confidence_suffix(reference):
    """What follows the evidence: nothing for authoritative links; report-only links
    say so, explaining why they did not group."""
    suffix = ("" if reference["confidence"] == "authoritative"
              else f", {reference['confidence']}")
    if not reference["grouping"] and reference["kind"] in ("resolved", "incoming"):
        suffix += ", report-only: shown but not used for project grouping"
    return suffix


def render_reference(reference):
    kind = reference["kind"]
    conf = _confidence_suffix(reference)

    if kind == "resolved":
        return (f"{reference['rel']} {reference['target_service']}:{reference['target_id']} "
                f"[via {reference['evidence']}{conf}]")

    if kind == "incoming":
        return (f"used by {reference['source_service']}:{reference['source_id']} "
                f"({reference['rel']}) [via {reference['evidence']}{conf}]")

    if kind == "dangling":
        # The state says which of the four situations this is; the old text
        # guessed at all of them in one sentence ("may be deleted, or ...").
        because = {
            "out_of_scope": "its region/account was not scanned, so nothing is known about it",
            "lookup_failed": "that resource type could not be listed here (denied or failed), "
                             "so its absence proves nothing",
            "confirmed_missing": "this scan listed every resource of that type in that scope and it was not among them, so it did not exist when the scan ran",
        }.get(reference.get("state"),
              "it was not found, and this scan cannot confirm whether it is gone")
        return (f"DANGLING -> {reference['target_id']} ({reference['rel']}) NOT FOUND in this scan "
                f"[via {reference['evidence']}] - {because}")

    if kind == "ambiguous":
        return (f"AMBIGUOUS -> '{reference['target_id']}' ({reference['rel']}) matches "
                f"{len(reference['candidates'])} resources and the reference does not say "
                f"which [via {reference['evidence']}] - candidates: "
                f"{', '.join(reference['candidates'])}")

    if kind == "collision":
        wrong = ", ".join(f"{service}:{reference['target_id']}"
                          for service in reference["matched"])
        return (f"NOTE: '{reference['target_id']}' matched {wrong} but expected a "
                f"{reference['expected_service']} - treating as coincidental name "
                f"collision, not a real {reference['rel']} link "
                f"[via {reference['evidence']}]")

    if kind == "prefix-refused":
        return (f"NOTE: '{reference['target_id']}*' matches {reference['match_count']} scanned "
                f"{reference['target_service']} rows (over the {reference['limit']} limit) - "
                f"treating as a blanket grant over a naming convention rather than "
                f"a {reference['rel']} link to each [via {reference['evidence']}]")

    raise ValueError(f"unknown reference kind: {kind!r}")


def render_connections(row):
    """The whole column: the collector's own prose, then every reference."""
    parts = [row.get("connections", "")]
    parts += [render_reference(r) for r in row.get("_references", ())]
    return " | ".join(part for part in parts if part)
