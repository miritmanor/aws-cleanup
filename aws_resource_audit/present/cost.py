"""The cost columns and the coverage-gaps section, derived at render time from each row's
attribution. The figure is recorded spend over COST_LOOKBACK_DAYS, not a monthly forecast."""

from ..billing import (
    COVERAGE_BLOCKED as BLOCKED,
    COVERAGE_COVERED as COVERED,
    COVERAGE_NOT_A_RESOURCE as NOT_A_RESOURCE,
    COVERAGE_NOT_FOUND as NOT_FOUND,
    COVERAGE_PARTIAL as PARTIAL,
    COVERAGE_UNSUPPORTED as UNSUPPORTED,
    DIRECT, ESTIMATED, NOT_QUERIED, SHARED_UNALLOCATED, UNAVAILABLE, ZERO,
    restore_attribution,
)
from ..config import BILLING_FINDING_MIN_AMOUNT, COST_LOOKBACK_DAYS

# What the window actually is, in one place, so no column header can claim a
# month. Read by the HTML template and the web app rather than restated.
COST_COLUMN_LABEL = f"Cost, last {COST_LOOKBACK_DAYS}d (USD)"
COST_STATE_LABEL = "How that figure was arrived at"

# One word per state, for the column that says what kind of number this is
# before the number is read.
_STATE_WORDING = {
    DIRECT: "measured",
    ESTIMATED: "estimated share",
    SHARED_UNALLOCATED: "unallocated (shared bill)",
    UNAVAILABLE: "unknown",
    NOT_QUERIED: "not queried",
}

# States that may show a figure at all. The rest show a blank and a sentence,
# which is the whole design: a number no evidence supports is worse than none.
_NUMERIC_STATES = (DIRECT, ESTIMATED)


def fmt_amount(amount):
    return "" if amount is None else f"{amount:.2f}"


def cost_state_word(state):
    return _STATE_WORDING.get(state, state or "")


def cost_columns(row):
    """The three cost cells for one row, from its stored attribution."""
    attribution = restore_attribution(row.get("cost"))
    figure = (fmt_amount(attribution.amount)
              if attribution.state in _NUMERIC_STATES else "")
    return {
        "est_monthly_cost_usd": figure,
        "cost": cost_state_word(attribution.state),
        "cost_notes": attribution.explanation,
    }


def period_note(report):
    """The one sentence that dates every figure in a report."""
    if not report.queried:
        return ("Cost Explorer was not queried (--no-cost). A blank cost does "
                "not mean free.")
    window = (f"{report.period_start} to {report.period_end}"
              if report.period_start else f"the last {COST_LOOKBACK_DAYS} days")
    said = f"Recorded spend for {window}, in {report.currency or 'USD'}"
    if report.observed_at:
        said += f", observed {report.observed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
    if report.estimated:
        said += ". AWS still calls this period estimated, so figures may change"
    if not report.complete:
        said += (". The billing response was INCOMPLETE, so this is not the "
                 "account's full total")
    return said + "."


# What each money finding means, in a word, for the sub-cent sentence.
_COVERAGE_WORDS = {UNSUPPORTED: "not scanned", BLOCKED: "lookup denied",
                   NOT_FOUND: "none found", PARTIAL: "partly scanned"}


def below_threshold_note(report):
    """One sentence naming the sub-cent findings, or "" when there are none."""
    small = report.below_threshold()
    if not small:
        return ""
    names = "; ".join(f"{s.service} ({s.amount.normalize():f} {report.currency}, "
                      f"{_COVERAGE_WORDS.get(s.state, s.state)})" for s in small)
    return (f"{len(small)} further service(s) cost less than "
            f"{BILLING_FINDING_MIN_AMOUNT} {report.currency} each and are not listed "
            f"above: {names}.")


def allocation_note(cost_report):
    """How much of the bill this report puts against individual resources."""
    if cost_report is None or cost_report.unallocated <= 0:
        return ""
    currency = cost_report.billing.currency or "USD"
    total, allocated = cost_report.total, cost_report.allocated
    if allocated <= 0:
        return (f"None of the {total:.2f} {currency} could be traced to a "
                "single resource.")
    return (f"Of the {total:.2f} {currency}, {allocated:.2f} is placed on "
            f"individual resources and {cost_report.unallocated:.2f} is not.")


# "What am I paying for that is not in this report?" Every heading names a situation
# in the account, and every entry says what to do about it.

SECTION_HEADING = "What you are paying for that this report does not cover"

_SECTIONS = (
    (UNSUPPORTED,
     "Charged for, and this tool does not scan it at all",
     ""),
    (BLOCKED,
     "Charged for, and the scan was not allowed to look",
     "Resources you pay for may be missing. Grant the permission "
     "(iam/aws-audit-role.yaml) and scan again."),
    (NOT_FOUND,
     "Charged for, but the scan found none of them",
     "Scanned, none found. It may be deleted, in a region this scan "
     "skipped, or a type the collector misses."),
    (PARTIAL,
     "Charged for more than this tool collects under that name",
     "AWS bills several resource types under one name and this tool "
     "collects some of them. Lines marked NOT SCANNED have no resource "
     "here."),
)

# The headings alone, shared so every renderer names a finding the same way.
BILLING_SECTION_HEADINGS = tuple((state, heading) for state, heading, _ in _SECTIONS)


def found_summary(entry):
    """What this report holds for one billed service, as counts per type."""
    if not entry.collected:
        return ""
    return ", ".join(f"{entry.found.get(name, 0)} {name}"
                     for name in sorted(entry.collected))


def breakdown_lines(entry, currency):
    """AWS's own split of the charge, marked by whether this report holds it."""
    return [(label, amount,
             "in the table above" if collected else "NOT SCANNED")
            for label, amount, collected in entry.breakdown]


def service_entry_lines(entry, currency):
    """One finding, as a list of lines: the money, then what is behind it."""
    unit = currency or "USD"
    lines = [f"**{entry.service}** - {entry.amount:.2f} {unit}"]
    summary = found_summary(entry)
    if summary:
        lines.append(f"this report holds {summary}")
    for name, status in entry.blocked_types:
        lines.append(f"{name} lookup: {status}")
    if entry.breakdown:
        detail = "; ".join(f"{label} {amount:.2f} ({note})"
                           for label, amount, note in breakdown_lines(entry, unit))
        lines.append(f"AWS splits the charge as: {detail}")
        if entry.unexplained > 0:
            lines.append(f"{entry.unexplained:.2f} {unit} of it has no "
                         "resource here")
    elif entry.gaps:
        lines.append("AWS also bills " + ", ".join(entry.gaps)
                     + " here, which this tool does not collect")
    return lines


def render_billing_coverage(report, billing=None):
    """The Markdown section. Always says something, including "nothing here"."""
    heading = f"## {SECTION_HEADING}\n\n"
    if not report.queried:
        return (heading + period_note(report) + "\n\n"
                "With no billing query there is nothing to compare the "
                "inventory against.\n\n")
    if report.error and not report.services:
        return (heading + f"The billing query failed: {report.error}. This "
                "scan cannot check the bill against the inventory.\n\n")

    currency = report.currency or "USD"
    lines = [heading]
    lines.append(f"AWS charged this account **{report.total:.2f} {currency}**. "
                 "Anything listed below is money with no resource beside it in "
                 "this report.\n\n")
    lines.append(period_note(report) + "\n\n")
    if not report.has_findings:
        lines.append("**Nothing to report.** Every charged service was "
                     "scanned and found.\n\n")
    else:
        lines.append(f"**{report.unaccounted:.2f} {currency} of that is "
                     "listed below.**\n\n")

    for state, section_heading, blurb in _SECTIONS:
        entries = report.in_state(state)
        if not entries:
            continue
        lines.append(f"### {section_heading} ({len(entries)})\n\n")
        if blurb:
            lines.append(f"{blurb}\n\n")
        for entry in entries:
            said = service_entry_lines(entry, currency)
            lines.append(f"- {said[0]}\n")
            for line in said[1:]:
                lines.append(f"  - {line}\n")
        lines.append("\n")

    small = below_threshold_note(report)
    if small:
        lines.append(f"{small}\n\n")

    covered = report.in_state(COVERED)
    if covered:
        lines.append("### Accounted for\n\nScanned and found.\n\n")
        for entry in covered:
            lines.append(f"- **{entry.service}** - {entry.amount:.2f} {currency}"
                         + (f"; this report holds {found_summary(entry)}"
                            if entry.collected else "") + "\n")
            # Show AWS's own breakdown: the only evidence there is no hidden NAT gateway.
            if entry.gaps and entry.breakdown:
                detail = "; ".join(f"{label} {amount:.2f}"
                                   for label, amount, _note in
                                   breakdown_lines(entry, currency))
                lines.append(f"  - AWS splits the charge as: {detail} - all "
                             "of it scanned, so no sign of "
                             f"{', '.join(entry.gaps)} here\n")
        lines.append("\n")
    non_resource = report.all_in_state(NOT_A_RESOURCE)
    if non_resource:
        lines.append("**Not resources**: "
                     + ", ".join(f"{e.service} ({e.amount:.2f})" for e in non_resource)
                     + " - tax, support and credits. Nothing to scan.\n\n")
    return "".join(lines)

def render_unallocated(cost_report):
    """The buckets deliberately not divided, as Markdown. Rounding leftovers of divided
    buckets go into the totals line instead."""
    # Only buckets with rows here; the rest are already findings above.
    refused = [b for b in cost_report.buckets
               if b.state == SHARED_UNALLOCATED and b.unallocated > 0
               and b.row_count > 0]
    if not refused and cost_report.unallocated <= 0:
        return ""
    currency = cost_report.billing.currency or "USD"
    lines = ["## Why some resources show no cost\n\n",
             "These resources are in the table above. The charges below are "
             "theirs together, with no defensible way to say how much belongs "
             "to which, so their cost column is blank.\n\n"]
    for bucket in sorted(refused, key=lambda b: -b.unallocated):
        where = f" in {bucket.region}" if bucket.region else ""
        lines.append(f"- **{bucket.service}{where}**: "
                     f"{bucket.unallocated:.2f} {currency} - {bucket.explanation}\n")
    # Subtract every refused bucket, listed or not, to get the true rounding.
    rounding = cost_report.unallocated - sum(
        (b.unallocated for b in cost_report.buckets
         if b.state == SHARED_UNALLOCATED), ZERO)
    if rounding > 0:
        lines.append(f"- rounding: {rounding:.2f} {currency} left over from "
                     "the buckets that were divided - shares are "
                     "rounded down so they can never exceed the bill\n")
    lines.append("\n" + allocation_note(cost_report) + "\n\n")
    return "".join(lines)
