// What AWS billed for, next to what this scan looked at - above the table, since it
// changes how the rows read. Every verdict and sentence comes from the backend.

import { useState } from "react";
import type { BilledService, BillingSummary } from "../api/types";

const SECTIONS: { state: BilledService["state"]; heading: string; blurb: string }[] = [
  {
    state: "unsupported",
    heading: "Charged for, and this tool does not scan it at all",
    blurb: "",
  },
  {
    state: "blocked",
    heading: "Charged for, and the scan was not allowed to look",
    blurb: "Resources you pay for may be missing. Grant the permission " +
      "and scan again.",
  },
  {
    state: "not_found",
    heading: "Charged for, but the scan found none of them",
    blurb: "Scanned, none found. It may be deleted, in a region this scan " +
      "skipped, or a type the collector misses.",
  },
  {
    state: "partial",
    heading: "Charged for more than this tool collects under that name",
    blurb: "AWS bills several resource types under one name and this tool " +
      "collects some of them. Lines marked NOT SCANNED have no resource here.",
  },
];

export function BillingBanner({ billing }: { billing: BillingSummary | null }) {
  const [open, setOpen] = useState(false);
  if (!billing) return null;

  // The backend already filtered and worded the findings; only group by state here.
  const findings = SECTIONS.map((section) => ({
    ...section,
    entries: billing.services.filter((s) => s.state === section.state),
  })).filter((section) => section.entries.length > 0);
  const unaccounted = findings
    .flatMap((section) => section.entries)
    .reduce((sum, entry) => sum + Number(entry.amount), 0);

  // Not queried is still worth a line: a report with no cost figures must not
  // read as a report of an account that costs nothing.
  if (!billing.queried) {
    return <div className="banner">{billing.period_note}</div>;
  }
  if (findings.length === 0 && billing.unallocated === "0") return null;

  // Always two decimals; AWS returns figures like "4.7621307703".
  const money = (amount: string | number) =>
    `${Number(amount).toFixed(2)} ${billing.currency || "USD"}`;

  return (
    <div className="banner warn billing-banner">
      <button className="linkish" onClick={() => setOpen(!open)}>
        {open ? "▾" : "▸"} What you are paying for that this report does not cover
      </button>{" "}
      {findings.length > 0
        ? `${money(unaccounted)} of ${money(billing.total)} has no resource beside it here`
        : `nothing — every charged service was scanned and found`}
      {open && (
        <div className="billing-detail">
          <p>{billing.period_note}</p>
          {findings.map((section) => (
            <div key={section.state}>
              <b>{section.heading}</b>
              {section.blurb && <p>{section.blurb}</p>}
              <ul>
                {section.entries.map((entry) => (
                  <li key={entry.service}>
                    {entry.lines[0]}
                    {entry.lines.length > 1 && (
                      <ul>
                        {entry.lines.slice(1).map((line) => (
                          <li key={line}>{line}</li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {billing.below_threshold_note && <p>{billing.below_threshold_note}</p>}
          {billing.allocation_note && <p>{billing.allocation_note}</p>}
          {!billing.reconciles && (
            <p>
              <b>Attributed and unallocated do not add up to the total.</b> Treat
              every figure in the table as suspect until a re-scan says otherwise.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
