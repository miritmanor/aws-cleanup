# 27. The web app's credentials arrive as a Docker secret

Date: 2026-09-13

## Context

Decision 0021 narrowed the container's view of the host's credentials: instead
of mounting `~/.aws`, `docker-compose.yml` mounted `${AUDIT_AWS_DIR:-~/.aws-audit}`
— a directory holding only the base profile this tool needs — and documented a
commented-out `environment:` block as the alternative for short-lived
identities.

Two things were wrong with that pair.

A **directory** mount is a container of unknown contents. It hands over
whatever happens to be in it: a stray `config` with an SSO cache reference, an
old profile, a file someone dropped there. The narrowing was by convention,
not by construction.

**Environment variables** are the wider of the two, not the narrower. Anything
that can reach the Docker socket reads them back with `docker inspect`, and
anything inside the container reads them from `/proc/1/environ`; they are
copied into every child process and turn up in crash dumps. The README also
framed them as being *for* short-lived credentials, which was never true —
boto3 reads permanent keys from the environment perfectly well. The real
asymmetry is refresh cadence: env vars are re-read from the shell on every
`up`, which suits a credential that expires hourly, while a file is written
once, which suits one that does not.

## Decision

Credentials arrive as a Docker secret: exactly one file, read-only, at
`/run/secrets/aws_credentials`, with `AWS_SHARED_CREDENTIALS_FILE` in the
`environment:` block pointing boto3 at it. The directory mount and the
commented environment block are both gone.

The secret's source is `${AUDIT_AWS_CREDENTIALS_FILE:-${HOME}/.aws-audit/credentials}`
— still outside the git working copy, for the reasons `.gitignore`'s own
comments give: this repository has already had account data committed and
scrubbed with `git-filter-repo`, and a credentials file inside a checkout is
one `git add -A` from the same accident, except that a leaked key has to be
rotated rather than merely rewritten out of history.

**The file must exist before `docker-compose up`.** Compose bind-mounts a
secret's source, and the spec has no `required: false` for secrets (Compose
v5.3.1 rejects the key outright), so a missing file stops the container
starting: `bind source path does not exist`. This was chosen over the
alternative — defaulting the secret at a committed placeholder file so the app
always boots — because a placeholder is a credentials-shaped file in the
repository, and that is the object this whole decision is trying not to have.

`collect/credentials.py` recognises a `/run/secrets/` path and words the
failure accordingly: the file is named as a secret, and the fix is given as an
instruction to edit the host file and restart, never as "write a section into
the path we just checked" — which, for a secret, names the one file in the
container that cannot be written.

## Consequences

- **The app no longer starts without credentials.** It used to: the directory
  mount auto-created an empty directory, the UI came up, and the last
  snapshot was browsable — reading files, renaming groups — with scanning the
  only thing that failed. That is now a compose-level startup error. An empty
  file (`touch ~/.aws-audit/credentials`) restores the old behaviour for
  anyone who wants to browse without credentials, and the scan then fails with
  the message described above.
- **`uid`, `gid` and `mode` on a secret are Swarm-only.** Compose accepts and
  silently ignores them (verified against v5.3.1), so the file keeps its host
  ownership while the container runs as uid 10001. On native Linux a `0600`
  file owned by the host user is unreadable to it; Docker Desktop and colima
  do not enforce this, so the failure appears only on Linux. `iam/README.md`
  says so, and says what to do about it.
- **`AUDIT_AWS_DIR` is gone**, replaced by `AUDIT_AWS_CREDENTIALS_FILE`, which
  names a file rather than a directory. Anyone with the old variable exported
  gets the default path instead, and the credentials error names the path it
  actually read.
- **Changing the credentials needs `--force-recreate`**, not a plain `up`.
  The secret is a bind mount fixed at container start and an edit usually
  replaces the file's inode; `docker-compose up` sees no configuration change,
  reports `Running`, and leaves the old credentials in place — verified, and
  the reason the error message names the flag rather than saying "restart".
- Short-lived credentials now go **into the file** rather than into the
  environment (`aws configure export-credentials` writes the three values,
  and a `credentials` section takes `aws_session_token` alongside the key and
  secret). One mechanism instead of two.
- The CLI is untouched. It reads `~/.aws` through boto3 exactly as before;
  this is a property of how the container is handed credentials, not of how
  the package finds them.
- **The agent's LLM API key uses the same mechanism**, as a second secret,
  `llm_api_key`, with one difference: its source defaults to `/dev/null`.
  Compose cannot make a secret optional, and the app must start for anyone
  who never wanted the agent; an empty key hides the agent in the UI.
