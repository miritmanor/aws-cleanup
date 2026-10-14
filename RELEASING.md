# Releasing

For the maintainer. A release publishes both images to ghcr.io and a GitHub
release with the files users deploy from (see
[`deploy/ecs/README.md`](deploy/ecs/README.md)). Pushing a tag `vX.Y.Z` starts
it; [`.github/workflows/release.yml`](.github/workflows/release.yml) does the
rest.

## What a release run does

1. Checks that `CHANGELOG.md` has a `## X.Y.Z` section; stops otherwise.
2. Builds both images for `linux/amd64` and `linux/arm64`, each on its own
   GitHub runner. The backend build runs pylint and the test suites; a failure
   stops the release before anything is published.
3. Publishes `ghcr.io/miritmanor/aws-cleanup-backend` and `-frontend`, each one
   image for both CPU types, tagged `X.Y.Z` and `latest`, with a provenance
   attestation.
4. Generates the app template with the images pinned by digest, lints both
   templates, and creates the GitHub release with `aws-cleanup-base.yaml`,
   `aws-cleanup-app.yaml`, `ecs-connect.sh`, `SHA256SUMS`, and that version's
   `CHANGELOG.md` section as its notes.

The workflow uses only the built-in `GITHUB_TOKEN`. No secrets to set up.

## One-time setup

1. **Workflow permissions:** Settings → Actions → General → Workflow
   permissions. Leave the default, "Read repository contents and packages
   permissions". The release workflow asks for what it needs itself.

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

1. **Pull request:** in `CHANGELOG.md`, rename `## Unreleased` to
   `## X.Y.Z` and add an empty `## Unreleased` above it. Merge.

2. **Tag the merged commit:**
   ```bash
   git checkout main
   git pull
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin vX.Y.Z
   ```

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

The first tagged release is `v0.2.0`: `0.1.0` names the initial public
release in `CHANGELOG.md` but was never tagged.

## When a run fails

- **Never reuse a version number.** The tag ruleset prevents it anyway.
- **Failed before publishing** (changelog check, tests, build): fix it through
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

Fork the repository, do the one-time setup in the fork, add a `## 0.0.1`
section to `CHANGELOG.md` there, and push the tag `v0.0.1` to the fork. The
images go to the fork owner's ghcr.io namespace, so nothing touches the real
packages.
