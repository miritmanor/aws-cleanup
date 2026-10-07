"""ECR repositories: billed per GB of images stored, and untagged layers pile up
silently. The newest pull (or push) across a repository's images is its usage."""

from ... import coverage
from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.calls import paged, safe_call, tags_to_dict
from ...rows import error_row, new_row
from ...staleness import days_ago, flag_stale
from ...text import join_nonempty


def _image_summary(images):
    """(newest pull or push, total GiB, untagged count) across a repository's images."""
    stamps = [d for img in images
              for d in (img.get("lastRecordedPullTime"), img.get("imagePushedAt")) if d]
    size = sum(img.get("imageSizeInBytes", 0) for img in images) / 1024 ** 3
    untagged = sum(1 for img in images if not img.get("imageTags"))
    return (max(stamps) if stamps else None), size, untagged


def collect_ecr_repositories(session, region):
    ecr = session.client("ecr", region_name=region, config=RETRY_CONFIG)
    repos, page_error = paged(ecr, "describe_repositories", "repositories", service="ECRRepository")
    if page_error and not repos:
        return [error_row("ECRRepository", region, "ERROR", page_error)]
    rows = []
    for repo in repos:
        name = repo["repositoryName"]
        created = repo.get("createdAt")
        images, _error = paged(ecr, "describe_images", "imageDetails", service="ECRRepository",
                               repositoryName=name)
        last_used, size, untagged = _image_summary(images)
        tags_resp = safe_call(ecr.list_tags_for_resource, capability=coverage.TAGS,
                              resourceArn=repo.get("repositoryArn", ""))
        tags = {} if "__error__" in tags_resp else tags_to_dict(tags_resp.get("tags", []))
        notes = "Storage is billed per GB-month. Usage is the newest image pull, or push if none was pulled. "
        if untagged:
            notes += f"{untagged} untagged image(s) - a lifecycle policy would expire them. "
        if not images:
            notes += "Empty repository: free, but clutter. "
        row = new_row(
            "ECRRepository", region, name, name, created, last_used,
            days_ago(last_used) if last_used else days_ago(created), last_used is None,
            flag_stale(days_ago(last_used) if last_used else None, days_ago(created), last_used is None),
            notes, tags=tags,
            description=join_nonempty([f"{len(images)} image(s)", f"{size:.2f}GiB"], ", "),
            arn=repo.get("repositoryArn", ""))
        raw_capture.record("ECRRepository", region, name, repo)
        rows.append(row)
    return rows
