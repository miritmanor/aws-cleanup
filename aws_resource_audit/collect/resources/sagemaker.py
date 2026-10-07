"""SageMaker endpoints, notebook instances and Studio apps - all bill per
instance-hour while in service, used or not - and recent training jobs."""

from datetime import timedelta

from ... import coverage
from ...config import COST_LOOKBACK_DAYS, RETRY_CONFIG, now
from ...collect import raw_capture
from ...collect.arns import probable_resource_id_from_arn
from ...collect.calls import paged, safe_call
from ...collect.cloudwatch import activity_note, cw_activity
from ...rows import add_edge, error_row, new_row
from ...staleness import days_ago, flag_from_activity, flag_stale
from ...text import join_nonempty


def _tags(client, arn):
    resp = safe_call(client.list_tags, capability=coverage.TAGS, ResourceArn=arn) if arn else {}
    return {} if "__error__" in resp else {t["Key"]: t["Value"] for t in resp.get("Tags", [])}


def collect_sagemaker_endpoints(session, region):
    """Activity is Invocations on the first production variant: published per
    request, so silence over the window means nobody called it."""
    client = session.client("sagemaker", region_name=region, config=RETRY_CONFIG)
    endpoints, page_error = paged(client, "list_endpoints", "Endpoints", service="SageMakerEndpoint")
    if page_error and not endpoints:
        return [error_row("SageMakerEndpoint", region, "ERROR", page_error)]
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    for endpoint in endpoints:
        name = endpoint["EndpointName"]
        detail = safe_call(client.describe_endpoint, EndpointName=name)
        variants = [] if "__error__" in detail else detail.get("ProductionVariants", [])
        variant = variants[0].get("VariantName", "AllTraffic") if variants else "AllTraffic"
        evidence = cw_activity(cw, "AWS/SageMaker", "Invocations", [
            {"Name": "EndpointName", "Value": name}, {"Name": "VariantName", "Value": variant}],
            stat="Sum")
        last_used = evidence.last_activity
        created = endpoint.get("CreationTime")
        serverless = any(v.get("CurrentServerlessConfig") for v in variants)
        row = new_row(
            "SageMakerEndpoint", region, name, name, created, last_used,
            days_ago(last_used) if last_used else days_ago(created), not evidence.used,
            flag_from_activity(evidence, days_ago(created)),
            activity_note(evidence, "Invocations CloudWatch metric used as activity proxy")
            + (" Serverless: billed per request." if serverless
               else " Billed per instance-hour while in service, called or not."),
            tags=_tags(client, endpoint.get("EndpointArn")),
            description=join_nonempty([endpoint.get("EndpointStatus", ""),
                                       f"{len(variants)} variant(s)"], ", "),
            arn=endpoint.get("EndpointArn", ""), activity=evidence)
        raw_capture.record("SageMakerEndpoint", region, name, endpoint)
        rows.append(row)
    return rows


def collect_sagemaker_notebooks(session, region):
    """No usage metric exists: in service is billed and unknown, stopped still
    bills its storage volume."""
    client = session.client("sagemaker", region_name=region, config=RETRY_CONFIG)
    notebooks, page_error = paged(client, "list_notebook_instances", "NotebookInstances",
                                  service="SageMakerNotebook")
    if page_error and not notebooks:
        return [error_row("SageMakerNotebook", region, "ERROR", page_error)]
    rows = []
    for notebook in notebooks:
        name = notebook["NotebookInstanceName"]
        status = notebook.get("NotebookInstanceStatus", "")
        detail = safe_call(client.describe_notebook_instance, NotebookInstanceName=name)
        detail = {} if "__error__" in detail else detail
        created = notebook.get("CreationTime")
        flag = ("STALE (stopped - its storage volume still bills)" if status == "Stopped"
                else f"UNKNOWN ({status.lower() or 'status unknown'} - billed per hour, no usage metric)")
        row = new_row(
            "SageMakerNotebook", region, name, name, created, None, days_ago(created), True, flag,
            "A notebook instance bills per hour while in service, whether or not anyone has it "
            "open. Stop it when idle, delete it when done. ",
            tags=_tags(client, notebook.get("NotebookInstanceArn")),
            description=join_nonempty([notebook.get("InstanceType", ""), status], ", "),
            arn=notebook.get("NotebookInstanceArn", ""))
        raw_capture.record("SageMakerNotebook", region, name, detail or notebook)
        add_edge(row, detail.get("SubnetId"), "runs in subnet", "notebook SubnetId",
                 conn_type="sagemakernotebook.subnet.placement", target_service="Subnet")
        role, _svc = probable_resource_id_from_arn(detail.get("RoleArn") or "")
        add_edge(row, role, "runs as", "notebook RoleArn",
                 conn_type="sagemakernotebook.iamrole.role", target_service="IAMRole")
        rows.append(row)
    return rows


# Apps that are not running bill nothing; AWS keeps deleted ones listed for a while.
_GONE_APPS = ("Deleted", "Deleting", "Failed")


def collect_sagemaker_studio_apps(session, region):
    """Studio apps: one on an instance bills per hour while in service, open or
    not. DescribeApp's last user activity is the usage signal."""
    client = session.client("sagemaker", region_name=region, config=RETRY_CONFIG)
    apps, page_error = paged(client, "list_apps", "Apps", service="SageMakerStudioApp")
    if page_error and not apps:
        return [error_row("SageMakerStudioApp", region, "ERROR", page_error)]
    rows = []
    for app in apps:
        if app.get("Status") in _GONE_APPS:
            continue
        owner = app.get("UserProfileName") or app.get("SpaceName") or "?"
        owner_key = {"UserProfileName": app["UserProfileName"]} if app.get("UserProfileName") \
            else {"SpaceName": app.get("SpaceName", "")}
        detail = safe_call(client.describe_app, DomainId=app.get("DomainId", ""), AppType=app.get("AppType", ""),
                           AppName=app.get("AppName", ""), **owner_key)
        detail = {} if "__error__" in detail else detail
        size = (detail.get("ResourceSpec") or app.get("ResourceSpec") or {}).get("InstanceType", "")
        last_used, created = detail.get("LastUserActivityTimestamp"), app.get("CreationTime")
        free = size in ("", "system")
        rid = f"{app.get('DomainId')}/{owner}/{app.get('AppType')}/{app.get('AppName')}"
        flag = ("ACTIVE (system size - free)" if free
                else flag_stale(days_ago(last_used), None, False) if last_used
                else f"UNKNOWN ({app.get('Status', '').lower()} on {size} - no user activity recorded)")
        row = new_row(
            "SageMakerStudioApp", region, rid, f"{owner} {app.get('AppType', '')}", created, last_used,
            days_ago(last_used), False, flag,
            "Free on the system size; on an instance it bills per hour while in service, whether or "
            "not anyone has it open. Delete the app (not the user) to stop it. ",
            tags=_tags(client, detail.get("AppArn")),
            description=join_nonempty([app.get("AppType", ""), size, app.get("Status", "")], ", "),
            arn=detail.get("AppArn", ""))
        raw_capture.record("SageMakerStudioApp", region, rid, detail or app)
        rows.append(row)
    return rows


def collect_sagemaker_training_jobs(session, region):
    """Jobs from the cost window only: a finished job bills nothing more, so it is
    history that explains the bill, not something to clean up."""
    client = session.client("sagemaker", region_name=region, config=RETRY_CONFIG)
    jobs, page_error = paged(client, "list_training_jobs", "TrainingJobSummaries",
                             service="SageMakerTrainingJob",
                             CreationTimeAfter=now() - timedelta(days=COST_LOOKBACK_DAYS))
    if page_error and not jobs:
        return [error_row("SageMakerTrainingJob", region, "ERROR", page_error)]
    rows = []
    for job in jobs:
        name, status = job["TrainingJobName"], job.get("TrainingJobStatus", "")
        created, ended = job.get("CreationTime"), job.get("TrainingEndTime")
        running = status in ("InProgress", "Stopping")
        row = new_row(
            "SageMakerTrainingJob", region, name, name, created, ended or created,
            days_ago(ended or created), False,
            f"ACTIVE ({status.lower()})" if running else f"HISTORY ({status.lower()} - billed while it ran)",
            "A training job bills per instance-second while it runs and nothing once it has "
            "finished; its model artifacts bill as S3 storage. ",
            tags=_tags(client, job.get("TrainingJobArn")), description=status,
            arn=job.get("TrainingJobArn", ""))
        raw_capture.record("SageMakerTrainingJob", region, name, job)
        rows.append(row)
    return rows
