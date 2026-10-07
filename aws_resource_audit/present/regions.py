"""The scan log's region summary, worded from an ActiveRegions."""


def region_summary_lines(active, currency="USD"):
    """Lines for the console. The first is always present."""
    found = ", ".join(f"{r} ({n})" for r, n in sorted(active.resources.items()))
    lines = ["Regions with resources: " + (found or "none")]
    if active.billed:
        scanned = [r for r in sorted(active.billed) if r in active.scanned]
        if scanned:
            lines.append("  billed, but nothing found: " + ", ".join(
                f"{r} ({active.billed[r]:.2f} {currency})" for r in scanned))
    if active.billed_not_scanned:
        lines.append("  billed in regions this scan did not cover: "
                     + ", ".join(active.billed_not_scanned)
                     + " - they are added to the saved list")
    if active.unknown:
        lines.append("  a lookup failed, so kept in the list: " + ", ".join(active.unknown))
    if active.empty_scanned:
        lines.append(f"  nothing found in {len(active.empty_scanned)} other scanned region(s)")
    return lines
