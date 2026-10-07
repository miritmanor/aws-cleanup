"""AWS Glue jobs and crawlers. Both bill per DPU-hour only while they run.
Dev endpoints are not scanned: AWS retired them."""

from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.arns import probable_resource_id_from_arn
from ...collect.calls import paged, safe_call
from ...rows import add_edge, error_row, new_row
from ...staleness import days_ago, flag_stale
from ...text import join_nonempty


def _role(value):
    """Glue takes a role as a bare name or as an ARN."""
    return probable_resource_id_from_arn(value)[0] if (value or "").startswith("arn:") else value


def _bucket(path):
    """The bucket of an s3:// path."""
    return path[len("s3://"):].split("/")[0] if (path or "").startswith("s3://") else None


def _last_run(client, name):
    resp = safe_call(client.get_job_runs, JobName=name, MaxResults=1)
    runs = [] if "__error__" in resp else resp.get("JobRuns", [])
    return (runs[0].get("StartedOn"), runs[0].get("JobRunState", "")) if runs else (None, "")


def collect_glue_jobs(session, region):
    client = session.client("glue", region_name=region, config=RETRY_CONFIG)
    jobs, page_error = paged(client, "get_jobs", "Jobs", service="GlueJob")
    if page_error and not jobs:
        return [error_row("GlueJob", region, "ERROR", page_error)]
    rows = []
    for job in jobs:
        name, created = job["Name"], job.get("CreatedOn")
        last_run, state = _last_run(client, name)
        row = new_row(
            "GlueJob", region, name, name, created, last_run,
            days_ago(last_run) if last_run else days_ago(created), False,
            flag_stale(days_ago(last_run), days_ago(created), False) if last_run
            else "STALE (never run)" if (days_ago(created) or 0) > 30 else "UNKNOWN (not run yet)",
            "Billed per DPU-hour while a run is in progress; an idle job costs nothing. ",
            description=join_nonempty([(job.get("Command") or {}).get("Name", ""),
                                       job.get("GlueVersion", ""),
                                       f"last run {state.lower()}" if state else ""], ", "))
        raw_capture.record("GlueJob", region, name, job)
        add_edge(row, _role(job.get("Role")), "runs as", "job Role",
                 conn_type="gluejob.iamrole.role", target_service="IAMRole")
        add_edge(row, _bucket((job.get("Command") or {}).get("ScriptLocation")), "loads its script from",
                 "job Command.ScriptLocation", conn_type="gluejob.s3bucket.script", target_service="S3Bucket")
        rows.append(row)
    return rows


def collect_glue_crawlers(session, region):
    client = session.client("glue", region_name=region, config=RETRY_CONFIG)
    crawlers, page_error = paged(client, "get_crawlers", "Crawlers", service="GlueCrawler")
    if page_error and not crawlers:
        return [error_row("GlueCrawler", region, "ERROR", page_error)]
    rows = []
    for crawler in crawlers:
        name, created = crawler["Name"], crawler.get("CreationTime")
        last = crawler.get("LastCrawl") or {}
        last_run = last.get("StartTime")
        schedule = crawler.get("Schedule") or {}
        scheduled = schedule.get("State") == "SCHEDULED"
        row = new_row(
            "GlueCrawler", region, name, name, created, last_run,
            days_ago(last_run) if last_run else days_ago(created), False,
            flag_stale(days_ago(last_run), days_ago(created), False) if last_run else "UNKNOWN (never crawled)",
            "Billed per DPU-hour while it crawls"
            + (f" - scheduled ({schedule.get('ScheduleExpression', '')}), so it bills on every run. "
               if scheduled else "; an unscheduled crawler costs nothing between runs. "),
            description=join_nonempty([f"database {crawler.get('DatabaseName', '?')}",
                                       "scheduled" if scheduled else "on demand",
                                       f"last crawl {last.get('Status', '').lower()}" if last else ""], ", "))
        raw_capture.record("GlueCrawler", region, name, crawler)
        add_edge(row, _role(crawler.get("Role")), "runs as", "crawler Role",
                 conn_type="gluecrawler.iamrole.role", target_service="IAMRole")
        for target in (crawler.get("Targets") or {}).get("S3Targets", []):
            add_edge(row, _bucket(target.get("Path")), "crawls", "crawler Targets.S3Targets",
                     conn_type="gluecrawler.s3bucket.target", target_service="S3Bucket")
        rows.append(row)
    return rows

