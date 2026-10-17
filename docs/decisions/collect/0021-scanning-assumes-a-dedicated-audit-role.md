# 21. Scanning assumes a dedicated audit role, and a profile is required

Status: accepted. How the web app receives credentials is decision 0027; how
the role names its actions is decision 0073.

## Context

Both front ends authenticated to AWS as whatever `--profile` resolved to,
directly - `boto3.Session(profile_name=args.profile)`, with no fallback
guardrail if `--profile` was omitted (the CLI) and a bind-mount of the user's
entire `~/.aws` into the container so the process had *something* to
authenticate with (the web app). Two things followed from that: a scan's
actual AWS permissions were whatever the chosen profile happened to carry -
commonly an everyday, broadly-privileged identity, since setting up a
narrower one took deliberate effort nobody was prompted to make - and the web
app's container could see every profile and SSO cache entry on the host, not
only the one it needed, because `~/.aws` doesn't come in narrower pieces.

Distributing this tool to somebody else's account made both worse: without a
required `--profile`, a first run could silently authenticate as whatever
`boto3`'s default chain found, and nothing described which AWS permissions a
scan needs in a form anyone could hand to `aws cloudformation deploy`.

## Decision

`audit_config.json` gains a standing setting, `assume_role` (default `false`)
plus `role_name` (default `"AWS-audit-role"`, overridable per-deployment with
`AWS_AUDIT_ROLE_NAME`) - see `settings.py`. This is a fact about an account's
setup, not a per-run choice, so it lives beside `output_dir` rather than as a
flag (the same reasoning already applied to detection
tuning).

`aws_resource_audit/collect/session.py` is new: `authenticated_session(args,
settings)` builds the base session from `--profile` exactly as before, and -
only when `assume_role` is on - trades it for a session built from
`sts:AssumeRole` credentials against `arn:aws:iam::<account>:role/<role_name>`
in the same account before returning. Everything downstream (every collector,
the cost lookup, `--tag-groups`) already just takes a `session`, so this one
seam is the whole change; `run.py` shrinks by the logic it moves out. It is
its own module rather than staying inline in `run.py` specifically so it can
be unit-tested against a mocked `boto3.Session` without touching AWS or the
rest of the pipeline - the failure modes (bad base credentials, a role with no
matching trust policy) are exactly the ones worth pinning down in isolation.

`iam/aws-audit-role.yaml` is a CloudFormation template for the role. It is
action-scoped (every action by exact name, decision 0073), not the AWS-managed
`ReadOnlyAccess` policy. `ReadOnlyAccess` is
correct but far broader than this tool needs (every AWS service, not the ~20
this one scans), and the point of a dedicated role is to not hand out more
than the thing running under it can use. `iam/README.md` covers deploying it
and wiring a profile up to assume it.

`--profile` is `required=True` on `aws_cleanup.py`. This is
independent of role assumption (an account may never turn `assume_role` on) but
serves the same goal: nothing about which AWS identity a scan uses should be
able to fall through to an implicit default.

The web app no longer mounts the user's whole `~/.aws`; it receives only the
base identity this tool needs (decision 0027).

## Consequences

- A fresh install behaves exactly as before except for the now-required
  `--profile`: `assume_role` defaults off, so nothing about the scan itself
  changes until someone deploys the role and turns the setting on.
- `--tag-groups --confirm` will fail with an access-denied error when run
  through the assumed role, because the role's policy grants no write action.
  This is deliberate, not an oversight - see `iam/README.md`'s last section -
  and it means the one flag that writes to AWS needs a separate grant or a
  non-role-assuming profile if anyone wants to use it this way.
- The web app's config editor (`ConfigResponse`/`ConfigUpdate`,
  `SettingsPage.tsx`) gained the two fields, following the parity rule: any
  setting the CLI can express, the web app's settings page can too.
