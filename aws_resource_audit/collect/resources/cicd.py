"""CodeBuild projects and CodePipeline pipelines: deployment machinery, never part
of the running application. The last build or execution is a genuine usage signal."""

from ... import coverage
from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.arns import probable_resource_id_from_arn
from ...collect.calls import paged, safe_call, tags_to_dict
from ...rows import add_edge, error_row, new_row
from ...staleness import days_ago, flag_stale


def _usage_row(service, region, rid, created, last, notes, tags, description, arn, raw):
    row = new_row(
        service, region, rid, rid, created, last, days_ago(last) if last else days_ago(created),
        last is None, flag_stale(days_ago(last) if last else None, days_ago(created), last is None),
        notes, tags=tags, description=description, arn=arn)
    raw_capture.record(service, region, rid, raw)
    return row


def _last_build(client, name):
    ids = safe_call(client.list_builds_for_project, capability=coverage.METRICS,
                    projectName=name, sortOrder="DESCENDING")
    if "__error__" in ids or not ids.get("ids"):
        return None
    builds = safe_call(client.batch_get_builds, capability=coverage.METRICS, ids=ids["ids"][:1])
    build = ({} if "__error__" in builds else (builds.get("builds") or [{}])[0])
    return build.get("endTime") or build.get("startTime")


def collect_codebuild_projects(session, region):
    client = session.client("codebuild", region_name=region, config=RETRY_CONFIG)
    names, page_error = paged(client, "list_projects", "projects", service="CodeBuildProject")
    if page_error and not names:
        return [error_row("CodeBuildProject", region, "ERROR", page_error)]
    rows = []
    for start in range(0, len(names), 100):   # BatchGetProjects takes at most 100
        resp = safe_call(client.batch_get_projects, names=names[start:start + 100])
        for project in ([] if "__error__" in resp else resp.get("projects", [])):
            name = project["name"]
            row = _usage_row(
                "CodeBuildProject", region, name, project.get("created"), _last_build(client, name),
                "Billed per build minute; an unused project costs nothing. ",
                tags_to_dict(project.get("tags", []), "key", "value"),
                (project.get("source") or {}).get("type", ""), project.get("arn", ""), project)
            role, _svc = probable_resource_id_from_arn(project.get("serviceRole") or "")
            add_edge(row, role, "runs as", "project serviceRole",
                     conn_type="codebuild.iamrole.service-role", target_service="IAMRole")
            rows.append(row)
    return rows


def collect_codepipelines(session, region):
    client = session.client("codepipeline", region_name=region, config=RETRY_CONFIG)
    pipelines, page_error = paged(client, "list_pipelines", "pipelines", service="CodePipeline")
    if page_error and not pipelines:
        return [error_row("CodePipeline", region, "ERROR", page_error)]
    rows = []
    for summary in pipelines:
        name = summary["name"]
        runs = safe_call(client.list_pipeline_executions, capability=coverage.METRICS,
                         pipelineName=name, maxResults=1)
        last = None if "__error__" in runs else (
            (runs.get("pipelineExecutionSummaries") or [{}])[0].get("startTime"))
        detail = safe_call(client.get_pipeline, name=name)
        pipeline = {} if "__error__" in detail else detail.get("pipeline", {})
        row = _usage_row(
            "CodePipeline", region, name, summary.get("created"), last,
            "V1 pipelines bill about $1/month while active; V2 per action minute. ", {},
            summary.get("pipelineType", ""), "", pipeline or summary)
        role, _svc = probable_resource_id_from_arn(pipeline.get("roleArn") or "")
        add_edge(row, role, "runs as", "pipeline roleArn",
                 conn_type="codepipeline.iamrole.role", target_service="IAMRole")
        add_edge(row, (pipeline.get("artifactStore") or {}).get("location"), "stores artifacts in",
                 "pipeline artifactStore.location", conn_type="codepipeline.s3bucket.artifact-store",
                 target_service="S3Bucket")
        for stage in pipeline.get("stages", []):
            for action in stage.get("actions", []):
                if (action.get("actionTypeId") or {}).get("provider") == "CodeBuild":
                    add_edge(row, (action.get("configuration") or {}).get("ProjectName"),
                             "runs build project", f"stage {stage.get('name', '')} action",
                             conn_type="codepipeline.codebuild.action",
                             target_service="CodeBuildProject")
        rows.append(row)
    return rows
