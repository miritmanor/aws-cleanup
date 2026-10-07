"""ElastiCache, OpenSearch and OpenSearch Serverless: all bill hourly while they
exist, and all are classic "spun up for a trial, never deleted" resources."""

from ... import activity, coverage
from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.arns import probable_resource_id_from_arn
from ...collect.calls import epoch_seconds_to_dt, paged, safe_call, tags_to_dict
from ...collect.cloudwatch import activity_note, cw_activity
from ...rows import add_edge, error_row, new_row
from ...staleness import days_ago, flag_from_activity
from ...scope import scan_account
from ...text import join_nonempty

_HOURLY = " Billed hourly per node while it exists, whether or not anything connects. "


def _elasticache_tags(client, arn):
    if not arn:
        return {}
    resp = safe_call(client.list_tags_for_resource, capability=coverage.TAGS, ResourceName=arn)
    return {} if "__error__" in resp else tags_to_dict(resp.get("TagList", []))


def _cache_row(region, cid, name, created, evidence, notes, tags, description, arn, groups, raw,
               subnets=()):
    last_used = evidence.last_activity
    row = new_row(
        "ElastiCacheCluster", region, cid, name, created, last_used,
        days_ago(last_used) if last_used else days_ago(created), not evidence.used,
        flag_from_activity(evidence, days_ago(created)), notes, tags=tags,
        description=description, arn=arn, activity=evidence)
    raw_capture.record("ElastiCacheCluster", region, cid, raw)
    for subnet_id in subnets:
        add_edge(row, subnet_id, "runs in subnet", "cache subnet group",
                 conn_type="elasticache.subnet.placement", target_service="Subnet")
    for group_id in groups:
        add_edge(row, group_id, "uses security group", "SecurityGroups",
                 conn_type="elasticache.securitygroup.membership", target_service="SecurityGroup")
    return row


def collect_elasticache_clusters(session, region):
    """Node-based clusters (one row per node group member, as AWS lists them)
    and serverless caches. Activity is CurrConnections."""
    client = session.client("elasticache", region_name=region, config=RETRY_CONFIG)
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    clusters, page_error = paged(client, "describe_cache_clusters", "CacheClusters",
                                 service="ElastiCacheCluster")
    if page_error and not clusters:
        return [error_row("ElastiCacheCluster", region, "ERROR", page_error)]
    groups, _error = paged(client, "describe_cache_subnet_groups", "CacheSubnetGroups",
                           service="ElastiCacheCluster")
    subnet_groups = {g["CacheSubnetGroupName"]: [s["SubnetIdentifier"] for s in g.get("Subnets", [])
                                                 if s.get("SubnetIdentifier")] for g in groups}
    rows = []
    for cluster in clusters:
        cid = cluster["CacheClusterId"]
        evidence = cw_activity(cw, "AWS/ElastiCache", "CurrConnections",
                               [{"Name": "CacheClusterId", "Value": cid}], stat="Sum")
        rows.append(_cache_row(
            region, cid, cluster.get("ReplicationGroupId", ""), cluster.get("CacheClusterCreateTime"),
            evidence, activity_note(evidence, "CurrConnections CloudWatch metric used as activity proxy")
            + _HOURLY, _elasticache_tags(client, cluster.get("ARN")),
            join_nonempty([f"{cluster.get('Engine', '')} {cluster.get('EngineVersion', '')}",
                           cluster.get("CacheNodeType", ""),
                           f"{cluster.get('NumCacheNodes', '?')} node(s)"], ", "),
            cluster.get("ARN", ""),
            [g["SecurityGroupId"] for g in cluster.get("SecurityGroups", []) if g.get("SecurityGroupId")],
            cluster, subnet_groups.get(cluster.get("CacheSubnetGroupName"), [])))
    # Serverless caches are a separate listing; a denial there loses only them.
    caches, _error = paged(client, "describe_serverless_caches", "ServerlessCaches",
                           service="ElastiCacheCluster")
    for cache in caches:
        cid = cache["ServerlessCacheName"]
        evidence = cw_activity(cw, "AWS/ElastiCache", "CurrConnections",
                               [{"Name": "clusterId", "Value": cid}], stat="Sum")
        rows.append(_cache_row(
            region, cid, cid, cache.get("CreateTime"), evidence,
            activity_note(evidence, "CurrConnections CloudWatch metric used as activity proxy")
            + " Serverless: billed for stored data and compute used, with a minimum. ",
            _elasticache_tags(client, cache.get("ARN")),
            join_nonempty([cache.get("Engine", ""), "serverless"], ", "), cache.get("ARN", ""),
            cache.get("SecurityGroupIds", []), cache, cache.get("SubnetIds", [])))
    return rows


def collect_opensearch_domains(session, region):
    """Activity is SearchRate; a domain nobody searches still bills per instance-hour."""
    client = session.client("opensearch", region_name=region, config=RETRY_CONFIG)
    listing = safe_call(client.list_domain_names)
    if "__error__" in listing:
        return [error_row("OpenSearchDomain", region, "ERROR", listing["__error__"])]
    names = [d["DomainName"] for d in listing.get("DomainNames", [])]
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    for start in range(0, len(names), 5):   # DescribeDomains takes at most five
        resp = safe_call(client.describe_domains, DomainNames=names[start:start + 5])
        if "__error__" in resp:
            rows.append(error_row("OpenSearchDomain", region, "ERROR", resp["__error__"]))
            continue
        for domain in resp.get("DomainStatusList", []):
            rows.append(_domain_row(client, cw, region, domain))
    return rows


def _domain_row(client, cw, region, domain):
    name = domain["DomainName"]
    arn = domain.get("ARN", "")
    account = arn.split(":")[4] if arn.count(":") >= 5 else ""
    evidence = cw_activity(cw, "AWS/ES", "SearchRate", [
        {"Name": "DomainName", "Value": name}, {"Name": "ClientId", "Value": account}], stat="Sum")
    tags_resp = safe_call(client.list_tags, capability=coverage.TAGS, ARN=arn) if arn else {}
    tags = {} if "__error__" in tags_resp else tags_to_dict(tags_resp.get("TagList", []))
    config = domain.get("ClusterConfig", {})
    vpc = domain.get("VPCOptions") or {}
    last_used = evidence.last_activity
    row = new_row(
        "OpenSearchDomain", region, name, name, None, last_used,
        days_ago(last_used) if last_used else None, not evidence.used,
        flag_from_activity(evidence, None),
        activity_note(evidence, "SearchRate CloudWatch metric used as activity proxy")
        + " Billed per instance-hour and per GB of storage, searched or not. ",
        tags=tags, description=join_nonempty([
            domain.get("EngineVersion", ""), config.get("InstanceType", ""),
            f"{config.get('InstanceCount', '?')} instance(s)"], ", "),
        arn=arn, activity=evidence, vpc_id=vpc.get("VPCId"))
    raw_capture.record("OpenSearchDomain", region, name, domain)
    for subnet_id in vpc.get("SubnetIds", []):
        add_edge(row, subnet_id, "runs in subnet", "VPCOptions.SubnetIds",
                 conn_type="opensearch.subnet.placement", target_service="Subnet")
    for group_id in vpc.get("SecurityGroupIds", []):
        add_edge(row, group_id, "uses security group", "VPCOptions.SecurityGroupIds",
                 conn_type="opensearch.securitygroup.membership", target_service="SecurityGroup")
    return row


def _collection_traffic(cw, collection):
    """Search or ingest requests; AOSS publishes them per collection, keyed by account too."""
    dims = [{"Name": "ClientId", "Value": scan_account()},
            {"Name": "CollectionId", "Value": collection.get("id", "")},
            {"Name": "CollectionName", "Value": collection.get("name", "")}]
    return activity.best_of(cw_activity(cw, "AWS/AOSS", "SearchRequestRate", dims, stat="Sum"),
                            cw_activity(cw, "AWS/AOSS", "IngestionRequestRate", dims, stat="Sum"))


def collect_opensearch_serverless_collections(session, region):
    """Collections bill in OCU-hours with a floor: the first one in a region keeps
    compute running (about $175-$350/month) even when nothing queries it."""
    client = session.client("opensearchserverless", region_name=region, config=RETRY_CONFIG)
    summaries, page_error = paged(client, "list_collections", "collectionSummaries",
                                  service="OpenSearchServerlessCollection")
    if page_error and not summaries:
        return [error_row("OpenSearchServerlessCollection", region, "ERROR", page_error)]
    ids = [s["id"] for s in summaries]
    details = []
    for start in range(0, len(ids), 100):   # BatchGetCollection takes at most 100
        resp = safe_call(client.batch_get_collection, ids=ids[start:start + 100])
        details += [] if "__error__" in resp else resp.get("collectionDetails", [])
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    for collection in details:
        name, arn = collection.get("name", ""), collection.get("arn", "")
        created = epoch_seconds_to_dt((collection.get("createdDate") or 0) // 1000 or None)
        evidence = _collection_traffic(cw, collection)
        last_used = evidence.last_activity
        tags = safe_call(client.list_tags_for_resource, capability=coverage.TAGS, resourceArn=arn)
        redundant = collection.get("standbyReplicas") != "DISABLED"
        row = new_row(
            "OpenSearchServerlessCollection", region, collection.get("id", name), name, created, last_used,
            days_ago(last_used) if last_used else days_ago(created), not evidence.used,
            flag_from_activity(evidence, days_ago(created)),
            activity_note(evidence, "Search and ingestion request rates used as activity proxy")
            + " Billed per OCU-hour with a minimum while any collection exists in the region"
            + (" (standby replicas on, so the higher minimum). " if redundant else ". "),
            tags={} if "__error__" in tags else tags_to_dict(tags.get("tags", []), "key", "value"),
            description=join_nonempty([(collection.get("type") or "").lower(),
                                       collection.get("status", ""),
                                       "standby replicas" if redundant else "no standby"], ", "),
            arn=arn, activity=evidence)
        raw_capture.record("OpenSearchServerlessCollection", region, row["resource_id"], collection)
        key, _svc = probable_resource_id_from_arn(collection.get("kmsKeyArn") or "")
        add_edge(row, key, "encrypted with", "collection kmsKeyArn",
                 conn_type="opensearchserverless.kmskey.encryption", target_service="KMSKey")
        rows.append(row)
    return rows
