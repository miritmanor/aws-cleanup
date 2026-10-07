# A dedicated, read-only role for scans

Both front ends can trade a broad "whatever this profile can do" identity for
a narrow one: authenticate as some **base identity**, then `sts:AssumeRole`
into a role that can only read the account, before scanning anything. This
directory has the CloudFormation template that defines that role
(`aws-audit-role.yaml`) and this file, which walks through setting it up.

Two identities are involved, and it is easy to mix them up:

- **The base identity** — whatever `--profile` (CLI) or the mounted/exported
  credentials (web app) resolve to. It needs exactly one permission of its
  own: `sts:AssumeRole` on the role below. It does **not** need any of the
  read permissions a scan uses — those belong to the role, not to it.
- **The audit role** (`AWS-audit-role` by default) — created by the template
  below, holding exactly the permissions `aws_resource_audit` needs. Nothing
  else can assume it except the base identity you name when deploying.

## 1. Deploy the role

```bash
aws cloudformation deploy \
  --template-file iam/aws-audit-role.yaml \
  --stack-name aws-audit-role \
  --parameter-overrides TrustedPrincipalArn=arn:aws:iam::123456789012:user/aws-audit \
  --capabilities CAPABILITY_NAMED_IAM
```

`TrustedPrincipalArn` is the base identity's ARN (an IAM user you'll create in
step 2, an existing user, or an SSO permission-set role — anything that can
authenticate as itself). There is no default; the deploy fails without it.

To use a role name other than `AWS-audit-role`, also pass
`RoleName=<your name>` and set the matching `role_name` in `audit_config.json`
(or the `AWS_AUDIT_ROLE_NAME` env var — see below).

## 2. Give the base identity permission to assume it

The template's trust policy says *who may* assume the role; it does not grant
the base identity `sts:AssumeRole` itself. Attach a small policy to whichever
IAM user or role you passed as `TrustedPrincipalArn`:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "sts:AssumeRole",
    "Resource": "arn:aws:iam::123456789012:role/AWS-audit-role"
  }]
}
```

If you don't already have a dedicated IAM user for this, create one now with
no permissions beyond that policy — it never needs anything else, since every
actual read happens through the assumed role.

## 3. Turn role assumption on

In `audit_config.json` (CLI: current directory; web app: the mounted data
volume, or the Settings page):

```json
{
  "assume_role": true,
  "role_name": "AWS-audit-role"
}
```

`role_name` only needs setting if you deployed under a different name;
`AWS_AUDIT_ROLE_NAME` overrides it per-deployment without editing the file.

## 4. Point a profile at the base identity

### CLI

`--profile` is required on every scan. A profile dedicated to this tool is
recommended over reusing whatever you use day-to-day — create one in
`~/.aws/credentials` (or `~/.aws/config`, for a source-profile chain) that
resolves to the base identity from step 2:

```ini
[aws-audit]
aws_access_key_id = ...
aws_secret_access_key = ...
```

```bash
python3 aws_resource_audit.py --all-regions --profile aws-audit
```

### Web app

The container never sees your real `~/.aws`, and it never sees your
credentials as environment variables either. They arrive as a **Docker
secret**: one read-only file, mounted at `/run/secrets/aws_credentials`, which
`docker-compose.yml` points boto3 at with `AWS_SHARED_CREDENTIALS_FILE`.
Create it on the host:

```bash
mkdir -p ~/.aws-audit && chmod 700 ~/.aws-audit
cat > ~/.aws-audit/credentials <<'EOF'
[default]
aws_access_key_id = ...
aws_secret_access_key = ...
EOF
docker-compose up --build
```

The `[default]` section is what a scan uses unless you type a name into the
Scan panel's **Profile** box, so a file with several sections works without
any change to the compose file.

`AUDIT_AWS_CREDENTIALS_FILE` points the secret at a different file:

```bash
AUDIT_AWS_CREDENTIALS_FILE=/path/to/credentials docker-compose up
```

**Editing the credentials later needs `--force-recreate`.** The secret is a
bind mount established when the container starts, and most editors replace the
file rather than rewriting it in place, so a running container goes on reading
the old one. A plain `docker-compose up` sees no configuration change and
reports `Running`, which reads like success:

```bash
docker-compose up -d --force-recreate backend
```

**The file has to exist before you start the app.** Compose bind-mounts the
secret's source, and there is no `required: false` for secrets, so a missing
file is a startup error - `bind source path does not exist` - rather than a
container that comes up unable to scan. That is deliberate.

**On native Linux, check the file is readable by the container's user.**
A secret's `uid`, `gid` and `mode` options are Swarm-only - Compose accepts
them and ignores them - so the file keeps its host ownership, and the
container runs as uid 10001. `chmod 644` on the file inside a `chmod 700`
directory is the simple answer; running the service as your own uid is the
other. Docker Desktop and colima do not enforce this, so it will not show up
on a Mac.

If your base identity is short-lived (SSO, an assumed role in your own shell),
write the session's credentials into that same file rather than exporting
them - `aws configure export-credentials --profile you --format env` gives you
the three values, and the `credentials` file takes `aws_session_token`
alongside the key and secret. They expire on their own schedule, and the next
`docker-compose up --force-recreate` picks up whatever the file says.

## What the role can't do

The template grants read-only actions only — exactly the actions the
collectors call, and nothing else. In particular it does **not** include
`resourcegroupstaggingapi:TagResources`, which means `--tag-groups --confirm`
(the one thing this tool can write to AWS) will fail with an access-denied
error through this role. That's deliberate: the role backing routine scans
should not also be able to write. If you want to use `--tag-groups`, either
add that one action to the role's policy yourself, or run that particular
scan with a profile that isn't going through role assumption.
