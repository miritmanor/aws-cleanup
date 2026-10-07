"""Deployment wrappers: Amplify apps, CloudFormation stacks and Elastic Beanstalk
applications and environments, each walked to find what it provisioned."""

from botocore.exceptions import BotoCoreError, ClientError

from ... import coverage
from ...config import RETRY_CONFIG
from ...staleness import days_ago, flag_stale
from ...collect.calls import paged, safe_call, tags_to_dict
from ...rows import add_edge, error_row, new_row
from ...collect import raw_capture


def collect_amplify_apps(session, region):
    amp = session.client("amplify", region_name=region, config=RETRY_CONFIG)
    rows = []
    try:
        paginator = amp.get_paginator("list_apps")
        for page in paginator.paginate():
            for app in page["apps"]:
                app_id = app["appId"]
                name = app.get("name", "")
                created = app.get("createTime")
                tags = app.get("tags", {}) or {}
                candidates = [t for t in [app.get("updateTime"), app.get("productionBranch", {}).get("lastDeployTime")] if t]
                branches_resp = safe_call(amp.list_branches, appId=app_id)
                if "__error__" not in branches_resp:
                    candidates.extend(b.get("updateTime") for b in branches_resp.get("branches", []) if b.get("updateTime"))
                last_deploy = max(candidates) if candidates else None
                ref_days = days_ago(last_deploy) if last_deploy else days_ago(created)
                rows.append(new_row(
                    "AmplifyApp", region, app_id, name,
                    created, last_deploy, ref_days, last_deploy is None,
                    flag_stale(ref_days if last_deploy else None, days_ago(created), last_deploy is None),
                    "Most recent branch/production updateTime used as activity proxy (last deploy or config change)",
                    tags=tags, description=app.get("description", ""),
                ))
                raw_capture.record("AmplifyApp", region, app_id, app)
    except (ClientError, BotoCoreError) as e:
        rows.append(error_row("AmplifyApp", region, "ERROR", str(e)))
    return rows


def collect_cloudformation_stacks(session, region):
    cfn = session.client("cloudformation", region_name=region, config=RETRY_CONFIG)
    rows = []
    try:
        paginator = cfn.get_paginator("list_stacks")
        for page in paginator.paginate():
            for stack in page["StackSummaries"]:
                if stack["StackStatus"] == "DELETE_COMPLETE":
                    continue
                name = stack["StackName"]
                created = stack.get("CreationTime")
                updated = stack.get("LastUpdatedTime")
                ref_days = days_ago(updated) if updated else days_ago(created)
                row = new_row(
                    "CloudFormationStack", region, stack.get("StackId", name), name,
                    created, updated, ref_days, True,
                    "STALE (FAILED STATE)" if "FAILED" in stack["StackStatus"] else flag_stale(ref_days, None, True),
                    "LastUpdatedTime used as activity proxy - reflects deployments, not runtime usage of the stack's resources",
                    description=stack.get("TemplateDescription", ""),
                    arn=stack.get("StackId", ""),
                )
                raw_capture.record("CloudFormationStack", region,
                                   stack.get("StackId", name), stack)
                # A nested stack names its parent, so the root is not left looking orphaned.
                if stack.get("ParentId"):
                    add_edge(row, stack["ParentId"], "nested under",
                             "CloudFormation ParentId (list_stacks)",
                             conn_type="cfnstack.cfnstack.parent",
                             target_service="CloudFormationStack")
                rows.append(row)
    except (ClientError, BotoCoreError) as e:
        rows.append(error_row("CloudFormationStack", region, "ERROR", str(e)))
    return rows


def collect_elastic_beanstalk_environments(session, region):
    """Elastic Beanstalk environments and what they own (instances, load balancer, source
    bucket). No traffic signal, so last deploy/update is the activity proxy."""
    eb = session.client("elasticbeanstalk", region_name=region, config=RETRY_CONFIG)
    rows = []
    environments, page_error = paged(eb, "describe_environments", "Environments",
                                     service="ElasticBeanstalkEnvironment")
    if page_error and not environments:
        return [error_row("ElasticBeanstalkEnvironment", region, "ERROR", page_error)]

    # ApplicationName+VersionLabel -> deployment bucket, so two environments
    # on the same version (common for a blue/green pair) cost one lookup.
    version_bucket_cache = {}

    for env in environments:
        env_id = env["EnvironmentId"]
        name = env.get("EnvironmentName", env_id)
        app_name = env.get("ApplicationName", "")
        version_label = env.get("VersionLabel")
        created = env.get("DateCreated")
        updated = env.get("DateUpdated")
        status = env.get("Status", "")
        health = env.get("Health", "")
        ref_days = days_ago(updated) if updated else days_ago(created)

        tags = {}
        env_arn = env.get("EnvironmentArn")
        if env_arn:
            tags_resp = safe_call(eb.list_tags_for_resource, capability=coverage.TAGS, ResourceArn=env_arn)
            if "__error__" not in tags_resp:
                tags = tags_to_dict(tags_resp.get("ResourceTags", []))

        notes = ("DateUpdated used as activity proxy - reflects deployments, not "
                 "runtime traffic served by the environment. ")
        flag = "STALE (TERMINATING)" if status == "Terminating" else flag_stale(ref_days, None, True)
        if status and status not in ("Ready", "Terminating"):
            notes += f"Environment status: {status}. "
        if health and health != "Green":
            notes += f"Health: {health} ({env.get('HealthStatus', '')}). "

        resources_resp = safe_call(eb.describe_environment_resources, EnvironmentId=env_id)
        if "__error__" in resources_resp:
            notes += f"Could not read environment resources ({resources_resp['__error__']}). "
            env_resources = {}
        else:
            env_resources = resources_resp.get("EnvironmentResources", {})

        bucket = None
        if app_name and version_label:
            cache_key = (app_name, version_label)
            if cache_key not in version_bucket_cache:
                version_resp = safe_call(
                    eb.describe_application_versions,
                    ApplicationName=app_name, VersionLabels=[version_label],
                )
                found = None
                if "__error__" not in version_resp:
                    versions = version_resp.get("ApplicationVersions", [])
                    if versions:
                        found = versions[0].get("SourceBundle", {}).get("S3Bucket")
                version_bucket_cache[cache_key] = found
            bucket = version_bucket_cache[cache_key]

        row = new_row(
            "ElasticBeanstalkEnvironment", region, env_id, name,
            created, updated, ref_days, True, flag, notes,
            tags=tags, description=env.get("Description", ""),
        )
        raw_capture.record("ElasticBeanstalkEnvironment", region, env_id, env)

        for instance in env_resources.get("Instances", []):
            add_edge(row, instance.get("Id"), "runs on",
                     "describe_environment_resources Instances",
                     conn_type="elasticbeanstalk.ec2instance.environment-resource",
                     target_service="EC2Instance")
        for lb in env_resources.get("LoadBalancers", []):
            add_edge(row, lb.get("Name"), "fronted by",
                     "describe_environment_resources LoadBalancers",
                     conn_type="elasticbeanstalk.loadbalancer.environment-resource",
                     target_service="LoadBalancer")
        if bucket:
            add_edge(row, bucket, "deploys from",
                     "describe_application_versions SourceBundle.S3Bucket",
                     conn_type="elasticbeanstalk.s3bucket.deployment-artifact",
                     target_service="S3Bucket")
        # The environment names its application, so this costs no extra call.
        add_edge(row, app_name, "deploys",
                 "describe_environments ApplicationName",
                 conn_type="elasticbeanstalk.ebapplication.application-name",
                 target_service="ElasticBeanstalkApplication")

        rows.append(row)
    return rows


# The folder Elastic Beanstalk writes per application in its service bucket.
# Used only as a probe target, never enumerated.
EB_EXTENSIONS_PREFIX = "resources/_runtime/_embedded_extensions/{app}/"


def _eb_service_bucket(app_arn, region):
    """The account's EB-managed bucket, `elasticbeanstalk-<region>-<account>`;
    the account comes from the application's ARN."""
    parts = (app_arn or "").split(":")
    account = parts[4] if len(parts) > 5 else ""
    return f"elasticbeanstalk-{region}-{account}" if account else None


def _service_bucket_holds_application(s3, bucket, app_name):
    """True/False if the bucket holds this application's folder, None on a failed
    lookup. A MaxKeys=1 probe for a path we named; never reads other keys."""
    resp = safe_call(s3.list_objects_v2, Bucket=bucket,
                     Prefix=EB_EXTENSIONS_PREFIX.format(app=app_name), MaxKeys=1)
    if "__error__" in resp:
        return None
    return resp.get("KeyCount", 0) > 0


def _source_bundle_buckets(eb, app_name):
    """Distinct S3 buckets named by this application's version source bundles,
    in first-seen order. One call per application, not per version."""
    resp = safe_call(eb.describe_application_versions, ApplicationName=app_name)
    if "__error__" in resp:
        return None
    buckets = []
    for version in resp.get("ApplicationVersions", []):
        bucket = version.get("SourceBundle", {}).get("S3Bucket")
        if bucket and bucket not in buckets:
            buckets.append(bucket)
    return buckets


def collect_elastic_beanstalk_applications(session, region):
    """Elastic Beanstalk applications, which outlive their environments. Linked to the
    service bucket by source bundle, or by probing the bucket for the app's folder."""
    eb = session.client("elasticbeanstalk", region_name=region, config=RETRY_CONFIG)
    try:
        resp = eb.describe_applications()
    except (ClientError, BotoCoreError) as e:
        return [error_row("ElasticBeanstalkApplication", region, "ERROR", str(e))]

    applications = resp.get("Applications", [])
    if not applications:
        return []

    s3 = session.client("s3", config=RETRY_CONFIG)
    rows = []
    for app in applications:
        name = app["ApplicationName"]
        created = app.get("DateCreated")
        updated = app.get("DateUpdated")
        ref_days = days_ago(updated) if updated else days_ago(created)
        versions = app.get("Versions", []) or []

        tags = {}
        app_arn = app.get("ApplicationArn")
        if app_arn:
            tags_resp = safe_call(eb.list_tags_for_resource, capability=coverage.TAGS, ResourceArn=app_arn)
            if "__error__" not in tags_resp:
                tags = tags_to_dict(tags_resp.get("ResourceTags", []))

        notes = ("DateUpdated used as activity proxy - reflects version and "
                 "configuration changes, not traffic. ")
        notes += (f"{len(versions)} application version(s). " if versions
                  else "No application versions. ")

        row = new_row(
            "ElasticBeanstalkApplication", region, name, name,
            created, updated, ref_days, True,
            flag_stale(ref_days, None, True), notes,
            tags=tags, description=app.get("Description", ""),
        )
        raw_capture.record("ElasticBeanstalkApplication", region, name, app)

        bundle_buckets = _source_bundle_buckets(eb, name)
        if bundle_buckets is None:
            row["notes"] += "Could not read application versions. "
        for bucket in bundle_buckets or ():
            # A bundle may sit in a bucket this scan never sees (e.g. AWS's sample app).
            add_edge(row, bucket, "deploys from",
                     "describe_application_versions SourceBundle.S3Bucket",
                     conn_type="ebapplication.s3bucket.source-bundle",
                     target_service="S3Bucket", assert_exists=False)

        service_bucket = _eb_service_bucket(app_arn, region)
        if service_bucket:
            held = _service_bucket_holds_application(s3, service_bucket, name)
            if held is None:
                row["notes"] += (f"Could not check {service_bucket} for this "
                                 f"application's folder. ")
            elif held:
                add_edge(row, service_bucket, "stores deployment artifacts in",
                         f"folder '{EB_EXTENSIONS_PREFIX.format(app=name)}' exists "
                         f"in the account's Elastic Beanstalk service bucket",
                         conn_type="ebapplication.s3bucket.service-bucket",
                         target_service="S3Bucket")

        rows.append(row)
    return rows
