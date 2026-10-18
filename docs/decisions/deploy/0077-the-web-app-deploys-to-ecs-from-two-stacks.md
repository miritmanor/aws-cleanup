# 0077 - the web app deploys to ECS from two stacks

Status: accepted. Needs the images to run without the repo: each Compose
service has an `image:`, and nginx's upstream is `${BACKEND_HOST}`.
How to deploy and connect: `deploy/ecs/README.md`.

## Context

The web app ran only on a laptop under Docker Compose. It should also run on
AWS Fargate, set up and taken down as needed, by the maintainer and by anyone
who finds the repo, preferably without cloning or building anything.

## Decision

1. **Two CloudFormation stacks.** `<AppName>-base` holds the data (EFS) and is
   kept; `<AppName>-app` holds everything that bills by the hour and is created
   and deleted at will. A single stack would delete saved project names on
   every teardown, or leave a `Retain`ed file system behind untracked.
2. **Every resource is tagged `Project=<AppName>`** in the templates
   themselves, so the tag is there however the stack was created.
3. **Published images on ghcr.io lead; building from source stays.** A tag
   `vX.Y.Z` runs `.github/workflows/release.yml`: both CPU types built natively,
   tested, published with provenance attestations, templates pinned to the
   images by digest. `deploy/ecs/ecs-up.sh` builds from a clone into the
   user's own ECR for people who want to run only what they built.
4. **One generated app template, no nested stack.** `tools/build_ecs_template.py`
   copies the read-only policy from `iam/aws-audit-role.yaml` into
   `deploy/ecs/app.yaml`. A nested stack would need the template in S3; the
   copy is generated and tested, so the permission list keeps one source and
   `iam/aws-audit-role.yaml` is unchanged.
5. **The task role is the app's identity.** No keys anywhere; boto3 reads the
   task role's credentials, which `collect/session.py` already falls back to.
6. **Public IP for outbound calls, no inbound rules, no NAT gateway.** The task
   must reach AWS APIs in every region, the registry and the logs service. VPC
   endpoints cannot serve other regions or ghcr.io; a NAT gateway costs about
   ten times more and has a public IP itself.
7. **Access through an SSM port-forwarding tunnel** to `127.0.0.1` on the
   user's machine (`ecs-connect.sh`). Opening a port to the user's IP was
   rejected: plain HTTP, and anyone sharing that IP could open an app with no
   login.

## Consequences

- One task only, replaced stop-first on every deploy: a short outage, and never
  two backends writing `/data`.
- The base stack has termination protection; removing everything is a
  deliberate two-step act (`ecs-remove-all.sh` asks for the app name).
- The maintainer's GitHub account is part of the published path's trust chain:
  narrowed by a tag ruleset, digest-pinned templates and attestations.
- Neither container can use a read-only root filesystem (ECS Exec and nginx's
  template step both write to it).
