# Scanning AWS Config and the security services

## Problem and requirements

AWS Config, GuardDuty, Security Hub, Inspector, Macie and Detective bill from the
moment they are turned on, per region, and the scan did not look at any of them.

- **R1** AWS Config: one `ConfigRecorder` row for the customer-managed recorder (recording or not, scope, delivery bucket).
- **R2** One `ConfigRule` row per rule; a rule another service created (`CreatedBy`) says so.
- **R3** GuardDuty: one `GuardDutyDetector` row per detector (enabled or suspended, features).
- **R4** Security Hub: one `SecurityHubHub` row per region (subscribed date, enabled standards).
- **R5** Inspector: one `InspectorAccount` row per region where any scan type is enabled or suspended.
- **R6** Macie: one `MacieAccount` row per region (enabled or paused).
- **R7** Detective: one `DetectiveGraph` row per behavior graph.
- **R8** Not enabled → no row and a COMPLETE coverage entry; denied → an `ERROR` row.
- **R9** Every new call granted by exact name in `iam/aws-audit-role.yaml`.
- **R10** Billing, category and console-link tables for each type; all six services are supporting.
- **R11** Both front ends, through the shared core and `GET /api/service-meta`; no new flag, control or endpoint.
- **R12** README: one sentence on where to see what a scan covers.
- **R13** ROADMAP: the other unscanned services. CHANGELOG entry.

Non-goals: architecture-diagram icons, findings, Organizations member-account views,
links between these rows and other resources. Service-linked Config recorders: with no name
given, `DescribeConfigurationRecorders` returns only the customer-managed one, and listing the
others needs `ListConfigurationRecorders` plus one lookup each.

## Existing mechanisms reused

`safe_call`/`paged` and the coverage ledger, `new_row`/`error_row`,
the `cost_hint` → `compute_risk(extra_hint=)` seam, `CE_SERVICE_MAP`/`BILLING_COVERAGE`
(decision 0037), `SERVICE_META`, `CONSOLE_URLS`.

## Options considered

**Verdict.** The user chose "flag enabled services for review". A `REVIEW` flag prefix
was rejected: `staleness.usage_state` derives used/unused/unknown from the flag's first
word, and the flag states evidence, not advice. Chosen: the flag states the fact
(`ACTIVE (enabled - bills while on)`, `STALE (suspended - not billing)`), and the advice
goes in the risk column through a new `cost_hint` value, `bills_while_enabled`.
A suspended service therefore reads as unused (STALE), which is accurate: it is off.

**"Not enabled" errors.** Security Hub answers `InvalidAccessException` and Macie
`AccessDeniedException` ("Macie is not enabled") when the service is off. Matching these
globally in `coverage.classify_error` cannot work for Macie, whose code also means a real
denial. Chosen: an `absent_when=` keyword on `safe_call` naming (code, message fragment)
pairs that mean "none" for that one call. They are recorded as COMPLETE, so the coverage
section never asks for a grant that would change nothing.

**Placement.** `collect/resources/security_services.py` (five services) and
`collect/resources/aws_config.py` (not `config.py`, which would shadow the package's).

## Design

- `collect/calls.py`: `safe_call(..., absent_when=())`; a match sets `kind = COMPLETE`.
- `collect/resources/aws_config.py`: `collect_config_recorders`, `collect_config_rules`.
- `collect/resources/security_services.py`: `collect_guardduty_detectors`,
  `collect_security_hub`, `collect_inspector`, `collect_macie`, `collect_detective_graphs`.
  Singletons use a fixed readable ID (`inspector`, `macie`; the row's account and region
  make its key unique, and Inspector's account comes from the response) or the hub ARN.
- `analyze/risk.py`: `bills_while_enabled` on an ACTIVE row →
  `MEDIUM - bills while enabled; ...`.
- Tables: `SERVICE_BILLING`, `SUPPORTING_SERVICES`, `CE_SERVICE_MAP`, `BILLING_COVERAGE`,
  `SERVICE_SLUGS`/`SERVICE_LABELS`, `SERVICE_META`, `CONSOLE_URLS`, coverage CSV.
- Cost: the "AWS Config" bill is shared evenly across a region's recorder and rules, as
  decision 0037's "estimated" state does for any bill with several resources. Putting it
  on the recorder alone was considered and rejected: `analyze/cost.py` pays every type in
  `BILLING_COVERAGE`, so it would need a Config-only special case there.

## Errors and logging

A denied call → `error_row` (logs WARNING) and a DENIED ledger entry. "Not enabled" →
DEBUG from `safe_call`, COMPLETE entry, no row. A failed detail call (`GetDetector`,
`GetEnabledStandards`, `DescribeConfigurationRecorderStatus`, `DescribeDeliveryChannels`) keeps the
row, says in its notes that the detail could not be read, and logs a WARNING naming the
region (and the detector, for GuardDuty).

## Security, privacy and IAM

New read actions: `config:DescribeConfigurationRecorders`,
`config:DescribeConfigurationRecorderStatus`, `config:DescribeDeliveryChannels`,
`config:DescribeConfigRules`, `guardduty:ListDetectors`, `guardduty:GetDetector`,
`securityhub:DescribeHub`, `securityhub:GetEnabledStandards`,
`inspector2:BatchGetAccountStatus`, `macie2:GetMacieSession`, `detective:ListGraphs`.
Rows reach the agent through the existing masking path; no new input.

## Scale

About 11 calls per region, plus one `GetDetector` per detector. No Cost Explorer requests.

## Test approach

One collector test per type (enabled, suspended, not enabled, denied), the denial case
list, `absent_when` unit tests, the risk hint, the IAM guard, the golden corpus.
