"""Databases and object storage: RDS, DynamoDB, S3."""

import logging

from botocore.exceptions import BotoCoreError, ClientError

from ..arns import arn_scope, probable_resource_id_from_arn
from ..cloudwatch import activity_note, cw_activity
from ... import activity, coverage
from ...config import RETRY_CONFIG
from ...staleness import days_ago, flag_from_activity
from ...collect.calls import safe_call, tags_to_dict
from ...text import join_nonempty
from ...rows import add_edge, error_row, new_row
from ...collect import raw_capture
from .rds import rds_family

logger = logging.getLogger(__name__)

ACCOUNT_WIDE = "global"


def collect_rds_instances(session, region):
    rds = session.client("rds", region_name=region, config=RETRY_CONFIG)
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    paginator = rds.get_paginator("describe_db_instances")
    try:
        for page in paginator.paginate():
            for db in page["DBInstances"]:
                did = db["DBInstanceIdentifier"]
                _cluster_type, instance_type, namespace, metric = rds_family(db.get("Engine"))
                created = db.get("InstanceCreateTime")
                tags_resp = safe_call(rds.list_tags_for_resource, capability=coverage.TAGS, ResourceName=db.get("DBInstanceArn", "")) if db.get("DBInstanceArn") else {}
                tags = tags_to_dict(tags_resp.get("TagList", [])) if "__error__" not in tags_resp else {}
                evidence = cw_activity(
                    cw, namespace, metric,
                    [{"Name": "DBInstanceIdentifier", "Value": did}], stat="Sum"
                )
                last_used = evidence.last_activity
                inferred = not evidence.used
                ref_days = days_ago(last_used) if last_used else days_ago(created)
                vpc_id = db.get("DBSubnetGroup", {}).get("VpcId", "")
                sg_names = [g.get("VpcSecurityGroupId") for g in db.get("VpcSecurityGroups", [])]
                connections = join_nonempty([
                    f"VPC={vpc_id}" if vpc_id else "",
                    f"SGs=[{','.join(sg_names)}]" if sg_names else "",
                    f"subnet-group={db.get('DBSubnetGroup', {}).get('DBSubnetGroupName', '')}",
                ])
                description = join_nonempty([
                    f"{db.get('Engine', '')} {db.get('EngineVersion', '')}",
                    db.get("DBInstanceClass", ""),
                    f"{db.get('AllocatedStorage', '?')}GiB",
                ])
                rows.append(new_row(
                    instance_type, region, did, tags.get("Name", ""),
                    created, last_used, ref_days, inferred,
                    flag_from_activity(evidence, days_ago(created)),
                    activity_note(evidence, f"{metric} CloudWatch metric (15mo max) used as activity proxy"),
                    tags=tags, description=description, connections=connections, vpc_id=vpc_id,
                    arn=db.get("DBInstanceArn", ""),
                ))
                raw_capture.record(instance_type, region, did, db)
                for subnet in (db.get("DBSubnetGroup") or {}).get("Subnets") or []:
                    add_edge(rows[-1], subnet.get("SubnetIdentifier"), "can run in subnet",
                             "DBSubnetGroup.Subnets", conn_type="rdsinstance.subnet.placement",
                             target_service="Subnet")
    except (ClientError, BotoCoreError) as e:
        rows.append(error_row("RDSInstance", region, "ERROR", str(e)))
    return rows


def collect_dynamodb_tables(session, region):
    ddb = session.client("dynamodb", region_name=region, config=RETRY_CONFIG)
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    try:
        paginator = ddb.get_paginator("list_tables")
        table_names = []
        for page in paginator.paginate():
            table_names.extend(page["TableNames"])
        for name in table_names:
            desc = safe_call(ddb.describe_table, TableName=name)
            if "__error__" in desc:
                rows.append(error_row("DynamoDBTable", region, name, desc["__error__"]))
                continue
            table = desc["Table"]
            created = table.get("CreationDateTime")
            tags_resp = safe_call(ddb.list_tags_of_resource, ResourceArn=table.get("TableArn", "")) if table.get("TableArn") else {}
            tags = tags_to_dict(tags_resp.get("Tags", [])) if "__error__" not in tags_resp else {}
            # Reads OR writes: an append-only table reads zero but is in constant use.
            dimensions = [{"Name": "TableName", "Value": name}]
            evidence = activity.best_of(
                cw_activity(cw, "AWS/DynamoDB", "ConsumedReadCapacityUnits",
                            dimensions, stat="Sum", explanation="reads"),
                cw_activity(cw, "AWS/DynamoDB", "ConsumedWriteCapacityUnits",
                            dimensions, stat="Sum", explanation="writes"))
            last_used = evidence.last_activity
            inferred = not evidence.used
            ref_days = days_ago(last_used) if last_used else days_ago(created)
            description = join_nonempty([
                table.get("BillingModeSummary", {}).get("BillingMode", ""),
                f"{table.get('ItemCount', '?')} items",
            ])
            rows.append(new_row(
                "DynamoDBTable", region, name, name,
                created, last_used, ref_days, inferred,
                flag_from_activity(evidence, days_ago(created)),
                activity_note(evidence, "Read and write capacity are both "
                                        "consulted - a write-only table is in use."),
                tags=tags, description=description,
                arn=table.get("TableArn", ""), activity=evidence,
            ))
            # describe_table's payload, not the list_tables envelope that only
            # carried the name.
            raw_capture.record("DynamoDBTable", region, name, table)
    except (ClientError, BotoCoreError) as e:
        rows.append(error_row("DynamoDBTable", region, "ERROR", str(e)))
    return rows


def collect_s3_buckets(session):
    """One account-wide listing, run once; each bucket is recorded in its own region."""
    s3 = session.client("s3", config=RETRY_CONFIG)
    resp = safe_call(s3.list_buckets)
    if "__error__" in resp:
        return [error_row("S3Bucket", ACCOUNT_WIDE, "ERROR", resp["__error__"])]
    rows = []
    for b in resp.get("Buckets", []):
        name = b["Name"]
        region = _bucket_region(s3, b)
        created = b.get("CreationDate")
        tags_resp = safe_call(s3.get_bucket_tagging, capability=coverage.TAGS, Bucket=name)
        tags = tags_to_dict(tags_resp.get("TagSet", [])) if "__error__" not in tags_resp else {}
        # No native "last accessed" for S3 without Storage Lens or S3 access logs / data-event CloudTrail.
        evidence = activity.unavailable(
            "S3 publishes no last-accessed signal without Storage Lens or "
            "CloudTrail data events, neither of which this read-only scan "
            "enables.")
        rows.append(new_row(
            "S3Bucket", region, name, name,
            created, "", None, True,
            # Age alone is not evidence about use; it is still shown in "created".
            flag_from_activity(evidence, days_ago(created)),
            activity_note(evidence, "Enable S3 Storage Lens or CloudTrail data "
                                    "events if you need real access history."),
            tags=tags, activity=evidence,
        ))
        raw_capture.record("S3Bucket", region, name, b)
        _attach_bucket_notifications(s3, rows[-1], name)
    return rows


def _bucket_region(s3, bucket):
    """ListBuckets' BucketRegion, else GetBucketLocation (None means us-east-1, "EU"
    is the legacy eu-west-1), else "global" with a warning."""
    if bucket.get("BucketRegion"):
        return bucket["BucketRegion"]
    name = bucket["Name"]
    resp = safe_call(s3.get_bucket_location, Bucket=name)
    if "__error__" in resp:
        logger.warning("S3 bucket %s: no BucketRegion and GetBucketLocation failed (%s) "
                       "- recorded as global, so its cost stays unallocated",
                       name, resp["__error__"])
        return ACCOUNT_WIDE
    location = resp.get("LocationConstraint")
    return {None: "us-east-1", "": "us-east-1", "EU": "eu-west-1"}.get(location, location)


def _attach_bucket_notifications(s3, row, bucket):
    """What a bucket triggers when an object lands in it. Reads the notification
    configuration only, never objects."""
    config = safe_call(s3.get_bucket_notification_configuration,
                       capability=coverage.DEPENDENCIES, Bucket=bucket)
    if "__error__" in config:
        return

    for key, arn_field, rel in (
            ("LambdaFunctionConfigurations", "LambdaFunctionArn", "triggers"),
            ("QueueConfigurations", "QueueArn", "notifies"),
            ("TopicConfigurations", "TopicArn", "notifies")):
        for entry in config.get(key) or []:
            arn = entry.get(arn_field)
            if not arn:
                continue
            target_id, target_service = probable_resource_id_from_arn(arn)
            if not target_id:
                continue
            account, region = arn_scope(arn)
            events = ", ".join(entry.get("Events") or []) or "object events"
            add_edge(row, target_id, rel,
                     f"S3 bucket notification ({events})",
                     conn_type="s3bucket.any.notification",
                     target_service=target_service, target_arn=arn,
                     target_account=account, target_region=region)

    if config.get("EventBridgeConfiguration") is not None:
        # EventBridge delivery names no destination here; the rules do.
        row["connections"] = join_nonempty([
            row.get("connections", ""),
            "sends object events to EventBridge (destinations are on the "
            "EventBridge rules, not on the bucket)"])
