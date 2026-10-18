# 0037 - A cost figure is an attribution, not a division

Status: accepted

## Context

`allocate_costs` took the Cost Explorer total for a service and divided it
evenly across whatever rows of that service the scan happened to hold. Both
words in "happened to hold" were load-bearing, and both were silent:

* **Region.** The query was account-wide. Scan `us-east-1` only, and every
  other region's spending on that service landed on the `us-east-1` rows.
* **Time.** The bill covers the last 30 days; the rows are what exists now.
  Delete a resource and the money it incurred was redistributed to whatever
  remained, making the survivors look more expensive than they were.
* **Bucket.** `EC2 - Other` bills NAT gateways, VPC endpoints and inter-AZ
  transfer alongside EBS volumes and Elastic IPs. The split handed all of it
  to the EBS volumes and Elastic IPs the scan found, because they were what it
  had.

None of these produces an error. They produce a number beside a resource, in a
column called `est_monthly_cost_usd`, and someone deletes something on the
strength of it. The column name was the fourth problem: a 30-day lookback of
charges already incurred is not a monthly forecast and is not a saving.

A fifth, one level up: `{service: amount}` was never compared against what the
scan looked at. A service AWS billed for and no collector covers appeared
nowhere — not as a gap, not as an unknown, not at all.

## Decision

**A row gets a figure only when the scan can say where the figure came from.
Otherwise it gets a sentence.**

Every row carries a `CostAttribution` with a state, and only `estimated` (and
the reserved, unproduced `direct`) may carry an amount:

| state | what it means |
|---|---|
| `direct` | measured per-resource cost. **Reserved; produced by nothing.** It needs a data export this tool does not create, and it exists so no report can imply a division was a measurement. |
| `estimated` | an even share of one (service, region) cell of the bill, across the resources billing into it that this scan enumerated *completely* there. |
| `shared_unallocated` | real money the scan will not divide, and why. |
| `unavailable` | nobody knows: no mapping, a failed lookup, or an incomplete response. |
| `not_queried` | `--no-cost`. Never rendered as zero. |

A bucket is divided only when the charge is attributed to a region, the
coverage ledger records no refusal on any type billing into it there, the
bucket covers no resource type this tool does not collect, resources of it were
actually found, and the amount is positive. Shares are rounded down, so the parts can never exceed the
whole; the remainder stays unallocated. Attributed plus unallocated reconciles
to the total by construction, and `CostReport.reconciles()` says so out loud.

Buckets are first-class and survive having no rows: spending on a service the
scan found nothing for stays visible, which the old model could not do.

`config.BILLING_COVERAGE` reads the relation bill-first — each billed service,
the resource types this tool collects for it, and the types it bills for that
this tool does not — and `analyze/billing_coverage.py` walks the bill as a
checklist against the coverage ledger and the inventory. The result is the
**"What you are paying for that this report does not cover"** section in
`grouping_audit.md`, the HTML report and the web app.

The findings are named for situations in the *account*, not in this tool's
plumbing. The first version of that section had a heading reading "collected by
something that did not finish", which describes an internal ledger state to a
reader asking about their own bill; it was also wrong, see below.

**Silence in the coverage ledger is not a failure.** The ledger records a
resource type when a collector produced a row of it or failed on it, so a
collector that ran cleanly and found none of a type records nothing. Reading
that absence as "did not finish" classified healthy collectors as broken — on
a real account, every type the account happens not to have. A missing entry now
means "found none", which is its own finding (*charged for, but the scan found
none of them*) when money is attached to it, and nothing at all when there is
not. Denials are recorded explicitly, so absence is safe to read this way.

`est_monthly_cost_usd` keeps its name, for the exports and scripts that read
it, and is derived at render time from the attribution along with the new
`cost` column beside it. Every label that showed it now says the window instead
of "monthly".

## Consequences

* Zero-cost lines stay in the data — a service that billed nothing this window
  still exists — but they are not findings. A reader asking what they are
  paying for is not helped by a list of services costing 0.00, so those are
  counted in a footnote instead.
* **Most rows no longer carry a number, and that is the point.** On a typical
  account, EC2 instances get a share and much of the rest reads
  "unallocated (shared bill)" with the reason. A reader who wants the missing
  detail is pointed at the Billing console, which has it.
* Older snapshots are upgraded rather than refused, and their stored figures
  are *not* carried forward: the upgrade marks them `unavailable` and says to
  re-scan. Relabelling the old division as a new conclusion would have been the
  worst available option.
* The billing query now groups by service **and** region, which costs nothing
  extra and is what makes regional honesty possible at all. A second
  request, grouped by usage type, is made only when a service this tool
  refuses to divide spent something (`collect/cost.py`).
