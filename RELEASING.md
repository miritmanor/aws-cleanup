# Releasing

For the maintainer. A release publishes both images to ghcr.io and a GitHub
release with the files users deploy from (see
[`deploy/ecs/README.md`](deploy/ecs/README.md)). Creating a release in
GitHub with a new tag `vX.Y.Z` starts it;
[`.github/workflows/release.yml`](.github/workflows/release.yml) does the rest,
including `CHANGELOG.md`.

## What a release run does

1. Checks that `CHANGELOG.md`, as of the tagged commit, has entries under
   `## Unreleased` (or a `## X.Y.Z` section); stops otherwise.
2. Builds both images for `linux/amd64` and `linux/arm64`, each on its own
   GitHub runner. The backend build runs pylint and the test suites; a failure
   stops the release before anything is published.
3. Publishes `ghcr.io/miritmanor/aws-cleanup-backend` and `-frontend`, each one
   image for both CPU types, tagged `X.Y.Z` and `latest`, with a provenance
   attestation.
4. Generates the app template with the images pinned by digest, lints both
   templates, and attaches `aws-cleanup-base.yaml`, `aws-cleanup-app.yaml`,
   `ecs-connect.sh` and `SHA256SUMS` to the release. If you left the release's
   notes empty, it fills them with the `## Unreleased` entries.
5. Commits to `main`: those entries moved from `## Unreleased` into a new
   `## X.Y.Z` section. An entry merged after the tag stays under Unreleased.

The workflow uses only the built-in `GITHUB_TOKEN`. No secrets to set up.

## One-time setup

1. **Workflow permissions:** Settings → Actions → General → Workflow
   permissions. Leave the default, "Read repository contents and packages
   permissions". The release workflow asks for what it needs itself.
   If a branch rule protects `main`, add GitHub Actions to its bypass list:
   the last step commits the `CHANGELOG.md` update there.

2. **Protect version tags:** Settings → Rules → Rulesets → New ruleset → New
   tag ruleset.
   - Name: `release tags`. Enforcement status: Active.
   - Target tags: Add target → Include by pattern → `v*`.
   - Rules: Restrict creations, Restrict updates, Restrict deletions.
   - Bypass list: Repository admin.

   Only you can push a version tag, and a pushed one cannot be moved or
   deleted. The release's templates point to that version's images, so a
   moved tag would make the release page wrong.

3. **After the first release run, make both images public.** A new ghcr.io
   package starts private, and Fargate could not pull it. For each of
   `aws-cleanup-backend` and `aws-cleanup-frontend`: your GitHub profile →
   Packages → the package → Package settings →
   - Danger Zone → Change visibility → Public. **This cannot be undone:**
     GitHub does not let a public package become private again.
   - Manage Actions access: this repository should be listed with Write. The
     workflow's first push adds it; add it if missing.

## Making a release

Keep `CHANGELOG.md`'s `## Unreleased` section up to date as pull requests
merge. That is the only changelog editing; never type a version into it.

1. **Create the release:** Releases → Draft a new release → Choose a tag →
   type `vX.Y.Z` → "Create new tag on publish", target `main`. Leave the
   notes empty to get the changelog entries, or write your own. Publish.
   (Pushing the tag from the command line works too: `git tag -a vX.Y.Z -m
   "vX.Y.Z" && git push origin vX.Y.Z`.)

2. **Do not save it as a draft.** A draft creates no tag, so nothing starts.

3. **Watch the run:** Actions → Release, or `gh run watch`. About 15 minutes.

4. **Check the result:**
   - The release page has the four files.
   - Logged out of ghcr.io (`docker logout ghcr.io`), this works:
     `docker pull ghcr.io/miritmanor/aws-cleanup-backend:X.Y.Z`
   - `gh attestation verify oci://ghcr.io/miritmanor/aws-cleanup-backend:X.Y.Z --repo miritmanor/aws-cleanup`
     succeeds, and the same for `aws-cleanup-frontend`.

5. **Deploy it once** in your own account from the release files
   (`deploy/ecs/README.md`, option A), open the app, then take the app stack
   down.

The next release is `v0.2.0`. `0.1.0` names the initial public release in
`CHANGELOG.md` but was never tagged; `v0.0.1` and `v0.0.2` were trial tags
whose runs published nothing.

## When a run fails

- **Never reuse a version number.** The tag ruleset prevents it anyway.
- **Failed before publishing** (empty Unreleased, tests, build): fix it through
  a pull request and release the next patch version, e.g. `v0.2.1`. The
  failed tag stays, with no release.
- **Images published, release step failed:** re-run the failed jobs from the
  run's page. Publishing again replaces the tags with identical images, and
  the release step updates the release if it already exists.

## Version numbers

`MAJOR.MINOR.PATCH`.

- **Patch:** fixes only.
- **Minor:** new features, or anything a user must act on after updating; the
  `CHANGELOG.md` line says what.
- **Major:** a change that stops an existing deployment from updating in
  place.

## Testing the workflow without a real release

Fork the repository, do the one-time setup in the fork, and create a release
`v0.0.1` there (with at least one entry under `## Unreleased`). The
images go to the fork owner's ghcr.io namespace, so nothing touches the real
packages.
