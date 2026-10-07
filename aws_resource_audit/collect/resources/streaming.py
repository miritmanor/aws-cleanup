"""Kinesis Data Streams, Data Firehose and MSK. A provisioned stream or Kafka
cluster bills with no traffic at all; Firehose bills only for data it moves."""

from ... import activity, coverage
from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.arns import probable_resource_id_from_arn
from ...collect.calls import paged, safe_call, tags_to_dict
from ...collect.cloudwatch import activity_note, cw_activity
from ...rows import add_edge, error_row, new_row
from ...staleness import days_ago, flag_from_activity
from ...text import join_nonempty


def collect_kinesis_streams(session, region):
    client = session.client("kinesis", region_name=region, config=RETRY_CONFIG)
    names, page_error = paged(client, "list_streams", "StreamNames", service="KinesisStream")
    if page_error and not names:
        return [error_row("KinesisStream", region, "ERROR", page_error)]
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    for name in names:
        detail = safe_call(client.describe_stream_summary, StreamName=name)
        if "__error__" in detail:
            rows.append(error_row("KinesisStream", region, name, detail["__error__"]))
            continue
        stream = detail.get("StreamDescriptionSummary", {})
        mode = (stream.get("StreamModeDetails") or {}).get("StreamMode", "PROVISIONED")
        created = stream.get("StreamCreationTimestamp")
        tags = safe_call(client.list_tags_for_stream, capability=coverage.TAGS, StreamName=name)
        evidence = cw_activity(cw, "AWS/Kinesis", "IncomingRecords",
                               [{"Name": "StreamName", "Value": name}], stat="Sum")
        last_used = evidence.last_activity
        cost = (f"Billed per shard-hour ({stream.get('OpenShardCount', '?')} open shard(s)) "
                "whether or not records arrive. " if mode == "PROVISIONED"
                else "On-demand: billed per stream-hour plus data, idle or not. ")
        row = new_row(
            "KinesisStream", region, name, name, created, last_used,
            days_ago(last_used) if last_used else days_ago(created), not evidence.used,
            flag_from_activity(evidence, days_ago(created)),
            activity_note(evidence, "IncomingRecords CloudWatch metric used as activity proxy") + " " + cost,
            tags={} if "__error__" in tags else tags_to_dict(tags.get("Tags", [])),
            description=join_nonempty([mode.lower(), f"{stream.get('OpenShardCount', '?')} shard(s)",
                                       f"{stream.get('RetentionPeriodHours', '?')}h retention"], ", "),
            arn=stream.get("StreamARN", ""), activity=evidence)
        raw_capture.record("KinesisStream", region, name, stream)
        rows.append(row)
    return rows


def _delivery_stream_names(client):
    """Firehose pages by the last name seen, not by a token."""
    names, start = [], None
    while True:
        resp = safe_call(client.list_delivery_streams, capability=coverage.INVENTORY, Limit=100,
                         **({"ExclusiveStartDeliveryStreamName": start} if start else {}))
        if "__error__" in resp:
            return names, resp["__error__"]
        page = resp.get("DeliveryStreamNames", [])
        names += page
        if not resp.get("HasMoreDeliveryStreams") or not page:
            return names, None
        start = page[-1]


def _link_destinations(row, description):
    for dest in description.get("Destinations", []):
        s3 = dest.get("ExtendedS3DestinationDescription") or dest.get("S3DestinationDescription") or {}
        bucket, _svc = probable_resource_id_from_arn(s3.get("BucketARN") or "")
        add_edge(row, bucket, "delivers to", "destination BucketARN",
                 conn_type="firehose.s3bucket.destination", target_service="S3Bucket")
        role, _svc = probable_resource_id_from_arn(s3.get("RoleARN") or "")
        add_edge(row, role, "runs as", "destination RoleARN",
                 conn_type="firehose.iamrole.role", target_service="IAMRole")
        for processor in (s3.get("ProcessingConfiguration") or {}).get("Processors", []):
            for param in processor.get("Parameters", []):
                if param.get("ParameterName") == "LambdaArn":
                    fn, _svc = probable_resource_id_from_arn(param.get("ParameterValue") or "")
                    add_edge(row, fn, "transforms with", "ProcessingConfiguration LambdaArn",
                             conn_type="firehose.lambda.processor", target_service="LambdaFunction")


def collect_firehose_streams(session, region):
    client = session.client("firehose", region_name=region, config=RETRY_CONFIG)
    names, page_error = _delivery_stream_names(client)
    if page_error and not names:
        return [error_row("FirehoseStream", region, "ERROR", page_error)]
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    for name in names:
        detail = safe_call(client.describe_delivery_stream, DeliveryStreamName=name)
        if "__error__" in detail:
            rows.append(error_row("FirehoseStream", region, name, detail["__error__"]))
            continue
        description = detail.get("DeliveryStreamDescription", {})
        created = description.get("CreateTimestamp")
        tags = safe_call(client.list_tags_for_delivery_stream, capability=coverage.TAGS,
                         DeliveryStreamName=name)
        evidence = cw_activity(cw, "AWS/Firehose", "IncomingRecords",
                               [{"Name": "DeliveryStreamName", "Value": name}], stat="Sum")
        last_used = evidence.last_activity
        row = new_row(
            "FirehoseStream", region, name, name, created, last_used,
            days_ago(last_used) if last_used else days_ago(created), not evidence.used,
            flag_from_activity(evidence, days_ago(created)),
            activity_note(evidence, "IncomingRecords CloudWatch metric used as activity proxy")
            + " Billed per GB ingested; an idle delivery stream costs nothing. ",
            tags={} if "__error__" in tags else tags_to_dict(tags.get("Tags", [])),
            description=join_nonempty([description.get("DeliveryStreamType", ""),
                                       description.get("DeliveryStreamStatus", "")], ", "),
            arn=description.get("DeliveryStreamARN", ""), activity=evidence)
        raw_capture.record("FirehoseStream", region, name, description)
        source = (description.get("Source") or {}).get("KinesisStreamSourceDescription") or {}
        stream, _svc = probable_resource_id_from_arn(source.get("KinesisStreamARN") or "")
        add_edge(row, stream, "reads from stream", "Source KinesisStreamARN",
                 conn_type="firehose.kinesis.source", target_service="KinesisStream")
        _link_destinations(row, description)
        rows.append(row)
    return rows


def _brokers_traffic(cw, name, brokers):
    """MessagesInPerSec is published per broker; the busiest one speaks for the cluster."""
    return activity.best_of(*[
        cw_activity(cw, "AWS/Kafka", "MessagesInPerSec",
                    [{"Name": "Cluster Name", "Value": name}, {"Name": "Broker ID", "Value": str(b)}],
                    stat="Sum")
        for b in range(1, brokers + 1)])


def _network(cluster):
    """(subnets, security groups) for a provisioned or a serverless cluster."""
    nodes = (cluster.get("Provisioned") or {}).get("BrokerNodeGroupInfo") or {}
    subnets, groups = list(nodes.get("ClientSubnets", [])), list(nodes.get("SecurityGroups", []))
    for vpc in (cluster.get("Serverless") or {}).get("VpcConfigs", []):
        subnets += vpc.get("SubnetIds", [])
        groups += vpc.get("SecurityGroupIds", [])
    return subnets, groups


def collect_msk_clusters(session, region):
    """MSK bills per broker-hour (or, serverless, per cluster- and partition-hour)
    plus storage, whether or not anything produces to it."""
    client = session.client("kafka", region_name=region, config=RETRY_CONFIG)
    clusters, page_error = paged(client, "list_clusters_v2", "ClusterInfoList", service="MSKCluster")
    if page_error and not clusters:
        return [error_row("MSKCluster", region, "ERROR", page_error)]
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    for cluster in clusters:
        name, kind = cluster["ClusterName"], cluster.get("ClusterType", "PROVISIONED")
        provisioned = cluster.get("Provisioned") or {}
        brokers = provisioned.get("NumberOfBrokerNodes", 0)
        evidence = _brokers_traffic(cw, name, brokers) if kind == "PROVISIONED" else None
        last_used, created = (evidence.last_activity if evidence else None), cluster.get("CreationTime")
        instance = ((provisioned.get("BrokerNodeGroupInfo") or {}).get("InstanceType") or "")
        row = new_row(
            "MSKCluster", region, name, name, created, last_used,
            days_ago(last_used) if last_used else days_ago(created), not (evidence and evidence.used),
            flag_from_activity(evidence, days_ago(created)) if evidence
            else "UNKNOWN (serverless - AWS publishes traffic per topic only)",
            (activity_note(evidence, "MessagesInPerSec CloudWatch metric used as activity proxy")
             if evidence else "") + " Billed per broker-hour (serverless: per cluster- and "
            "partition-hour) plus storage, whether or not anything produces to it. ",
            tags=dict(cluster.get("Tags") or {}),
            description=join_nonempty([kind.lower(), f"{brokers} broker(s)" if brokers else "",
                                       instance, cluster.get("State", "")], ", "),
            arn=cluster.get("ClusterArn", ""), activity=evidence)
        raw_capture.record("MSKCluster", region, name, cluster)
        subnets, groups = _network(cluster)
        for subnet_id in subnets:
            add_edge(row, subnet_id, "has a broker in subnet", "BrokerNodeGroupInfo.ClientSubnets",
                     conn_type="msk.subnet.placement", target_service="Subnet")
        for group_id in groups:
            add_edge(row, group_id, "uses security group", "BrokerNodeGroupInfo.SecurityGroups",
                     conn_type="msk.securitygroup.membership", target_service="SecurityGroup")
        rows.append(row)
    return rows
