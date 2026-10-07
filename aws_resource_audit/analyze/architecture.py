"""The architecture graph: runtime types and runtime lines only, no deployment
tier, with IAM grants and name matches dotted."""

from ..connection_types.vocabulary import SEM_INVOKES, SEM_PERMITS_ACCESS_TO, SEM_READS_OR_WRITES
from ..registry import CONNECTION_TYPES_BY_ID
from .graph import extract_project
from .network import network_placement
from .tiers import TIER_DEPLOYMENT

# What an AWS reference diagram draws: no EBSVolume (part of its instance there),
# plus CognitoUserPool (the API's sign-in).
ARCHITECTURE_SERVICES = frozenset({
    "EC2Instance", "LambdaFunction", "ElasticBeanstalkEnvironment", "StepFunctionsStateMachine",
    "ECSService", "SageMakerEndpoint", "EKSCluster",
    "S3Bucket", "EFSFileSystem", "RDSInstance", "RDSCluster", "DynamoDBTable",
    "ElastiCacheCluster", "OpenSearchDomain", "OpenSearchServerlessCollection", "RedshiftCluster",
    "RedshiftServerlessWorkgroup",
    "DocumentDBCluster", "DocumentDBInstance", "NeptuneCluster", "NeptuneInstance",
    "Route53HostedZone", "CloudFrontDistribution", "WAFWebACL", "NetworkFirewall",
    "TransitGateway", "VPNConnection", "Route53ResolverEndpoint",
    "AmplifyApp", "APIGatewayRestApi", "APIGatewayV2Api", "AppSyncApi", "LoadBalancer",
    "CognitoUserPool",
    "SQSQueue", "SNSTopic", "KinesisStream", "FirehoseStream", "MSKCluster",
    # Schedules and rules stay as their own boxes: their target may also be
    # invoked some other way, and a box is how the diagram says "and on a timer".
    "EventBridgeRule", "EventBridgeSchedule", "EventBridgeBus",
})

# The verb on a runtime line, where the semantics' default ("invokes", "uses")
# can say more. A connection type absent here uses the default for its semantics.
VERBS = {
    "apigateway.lambda.integration": "invokes",
    "apigateway.lambda.stage-variable": "invokes",
    "apigatewayv2.lambda.integration": "invokes",
    "loadbalancer.any.target": "routes to",
    "sns.any.subscription": "delivers to",
    "s3bucket.any.notification": "notifies",
    "eventbridgerule.any.target": "triggers",
    "eventbridgeschedule.any.target": "triggers",
    "cognito.lambda.trigger": "triggers",
    "lambda.any.event-source-mapping": "triggers",
    "sqs.sqs.redrive-policy": "dead-letters to",
    "lambda.any.dead-letter-config": "dead-letters to",
    "lambda.apigateway.env-var-execute-api-url": "calls",
    "amplify.apigateway.env-var-execute-api-url": "calls",
    "elasticbeanstalk.ec2instance.environment-resource": "runs on",
    "cloudfront.s3bucket.origin": "serves from",
    "cloudfront.loadbalancer.origin": "forwards to",
    "cloudfront.apigateway.origin": "forwards to",
    "ecsservice.loadbalancer.target": "routes to",
    "route53.cloudfront.alias": "resolves to",
    "route53.loadbalancer.alias": "resolves to",
    "route53.s3bucket.alias": "resolves to",
    "eventbridgerule.eventbridgebus.membership": "routes",
    "wafwebacl.loadbalancer.protection": "protects",
    "wafwebacl.apigateway.protection": "protects",
    "wafwebacl.cognito.protection": "protects",
    "cloudfront.wafwebacl.protected-by": "protects",
    "route53.apigatewaydomain.alias": "resolves to",
    "apigateway.cognito.authorizer": "authorizes with",
    "apigateway.lambda.authorizer": "authorizes with",
    "apigatewayv2.cognito.authorizer": "authorizes with",
    "apigatewayv2.lambda.authorizer": "authorizes with",
    "firehose.kinesis.source": "feeds",
    "firehose.s3bucket.destination": "delivers to",
    "firehose.lambda.processor": "transforms with",
    "vpnconnection.transitgateway.attachment": "connects through",
    "appsync.lambda.datasource": "resolves with",
    "appsync.cognito.authorizer": "authorizes with",
    "appsync.lambda.authorizer": "authorizes with",
}

# Not drawn, but lines run through them: Route 53 -> custom domain -> API is
# drawn as Route 53 -> API, with the verb of the line coming in.
PASS_THROUGH = frozenset({"APIGatewayDomainName"})

_DEFAULT_VERB = {SEM_INVOKES: "invokes", SEM_READS_OR_WRITES: "uses"}

# Recorded from the consumer's side, but data flows the other way: a Lambda's
# event-source mapping names its queue, and the queue is what triggers it.
REVERSED = frozenset({"lambda.any.event-source-mapping", "ecsservice.loadbalancer.target",
                      "eventbridgerule.eventbridgebus.membership",
                      "cloudfront.wafwebacl.protected-by", "firehose.kinesis.source"})

# Runtime by semantics but about deploying: a Beanstalk app's source bundles.
NOT_RUNTIME = frozenset({
    "elasticbeanstalk.s3bucket.deployment-artifact",
    "ebapplication.s3bucket.source-bundle",
    "ebapplication.s3bucket.service-bucket",
})

# Drawn although not runtime by semantics: a Beanstalk environment's instances serve
# its traffic, and a VPN's traffic enters through its transit gateway.
ALSO_RUNTIME = frozenset({"elasticbeanstalk.ec2instance.environment-resource",
                          "vpnconnection.transitgateway.attachment"})

# Likely but unproven runtime links, drawn dotted: a Lambda env var holding a
# bare table or bucket name is a name match, yet almost always means "uses".
LIKELY = {"lambda.any.env-var-bare-name": "uses"}

GRANT_VERB = "can access"
_ATTACH = "iampolicy.iamrole.attachment"

# Only the identity a compute resource's own code runs as says what it can reach;
# a Cognito SMS role or an EventBridge target role does not.
PRINCIPAL_ROLE_LINKS = frozenset({"lambda.iamrole.execution-role",
                                  "ec2.iamrole.instance-profile",
                                  "ecsservice.iamrole.task-role",
                                  "statemachine.iamrole.execution-role"})


def runtime_verb(conn_type):
    """The verb for a runtime line of this connection type, or None."""
    if conn_type in NOT_RUNTIME:
        return None
    conn = CONNECTION_TYPES_BY_ID.get(conn_type)
    if conn is None:
        return None
    if conn_type in ALSO_RUNTIME:
        return VERBS[conn_type]
    default = _DEFAULT_VERB.get(conn.semantics)
    return VERBS.get(conn_type, default) if default else None


def _service(node_id):
    return node_id.split(":", 1)[0]


def _semantics(conn_type):
    conn = CONNECTION_TYPES_BY_ID.get(conn_type)
    return conn.semantics if conn else None


def _drawable_ids(nodes, tier_of):
    return {n["id"] for n in nodes
            if n.get("kind") == "resource"
            and n.get("service") in ARCHITECTURE_SERVICES
            and tier_of(n["id"]) != TIER_DEPLOYMENT}


def _links(graph):
    """(source, target, conn_type) for every resolved link in the scan graph."""
    for edge in graph.get("edges") or []:
        if edge.get("state") != "resolved":
            continue
        for link in edge.get("links") or []:
            yield edge["source"], edge["target"], link.get("conn_type") or ""


def _grants(graph):
    """Role -> targets it may access, directly or through attached policies."""
    role_grants, policy_grants, attached = {}, {}, {}
    for source, target, conn_type in _links(graph):
        if conn_type == _ATTACH:
            attached.setdefault(target, set()).add(source)
        elif _semantics(conn_type) == SEM_PERMITS_ACCESS_TO:
            bucket = role_grants if source.startswith("IAMRole:") else policy_grants
            bucket.setdefault(source, set()).add(target)
    grants = {}
    for role in set(role_grants) | set(attached):
        targets = set(role_grants.get(role, ()))
        for policy in attached.get(role, ()):
            targets |= policy_grants.get(policy, set())
        grants[role] = targets
    return grants


def _ecs_groups(links, drawn, asg_members, labels):
    """An ECS cluster on EC2 capacity: a box around its services and container
    instances, holding the Auto Scaling groups those instances belong to."""
    instances, services = {}, {}
    for source, target, conn_type in links:
        if conn_type == "ecscluster.ec2instance.container-instance" and target in drawn:
            instances.setdefault(source, set()).add(target)
        elif conn_type == "ecsservice.ecscluster.membership" and source in drawn:
            services.setdefault(target, set()).add(source)
    in_asg = {m for ids in asg_members.values() for m in ids}
    groups, asg_parent = [], {}
    for cluster, ids in sorted(instances.items()):
        asg_parent.update({asg: cluster for asg, members in asg_members.items() if members & ids})
        groups.append({"id": cluster, "kind": "ecs", "title": f"ECS cluster {labels.get(cluster, cluster)}",
                       "members": sorted((ids - in_asg) | services.get(cluster, set())), "parent": None})
    return groups, asg_parent


def _groups(graph, drawn):
    """Boxes AWS diagrams draw around members: an Auto Scaling group around its
    instances, an EKS or ECS cluster around what runs in it."""
    labels = {n["id"]: n.get("label") or n["id"].split(":")[-1] for n in graph.get("nodes") or []}
    links = list(_links(graph))
    members, cluster_of = {}, {}
    for source, target, conn_type in links:
        if conn_type == "asg.ec2.instance-membership" and target in drawn:
            members.setdefault(source, set()).add(target)
        elif conn_type == "ekscluster.autoscalinggroup.nodegroup":
            cluster_of[target] = source
    ecs, ecs_parent = _ecs_groups(links, drawn, members, labels)
    groups = [{"id": asg, "kind": "asg", "title": f"Auto Scaling group {labels.get(asg, asg)}",
               "members": sorted(ids), "parent": cluster_of.get(asg) or ecs_parent.get(asg)}
              for asg, ids in sorted(members.items())]
    for cluster in sorted({g["parent"] for g in groups if g["parent"] in set(cluster_of.values())}):
        groups.append({"id": cluster, "kind": "eks", "title": f"EKS cluster {labels.get(cluster, cluster)}",
                       "members": [cluster] if cluster in drawn else [], "parent": None})
    return groups + ecs


def scope_groups(groups, node_ids):
    """The groups that still hold a node after scoping, with only those members."""
    kept = [dict(g, members=[m for m in g["members"] if m in node_ids]) for g in groups]
    alive = {g["id"] for g in kept if g["members"]}
    alive |= {g["parent"] for g in kept if g["id"] in alive and g["parent"]}
    return [g for g in kept if g["id"] in alive]


def architecture_graph(graph, all_rows):
    """The payload {nodes, edges, stats}; edges add "verb" and "style".
    Shaped like build_graph_data's so extract_project can scope it."""
    tiers = {}
    for row in all_rows:
        tiers[f"{row['service']}:{row['region']}:{row['resource_id']}"] = row.get("tier") or ""

    def tier_of(node_id):
        return tiers.get(node_id.split("#", 1)[0], "")

    nodes = graph.get("nodes") or []
    drawn = _drawable_ids(nodes, tier_of)
    edges = {}

    def add(source, target, verb, style, conn_type):
        if source == target or source not in drawn or target not in drawn:
            return
        current = edges.get((source, target))
        # A solid line beats a dotted one between the same pair: it is the stronger claim.
        if current and (current["style"] == "solid" or style == "dotted"):
            current["links"].append({"conn_type": conn_type})
            return
        edges[(source, target)] = {
            "source": source, "target": target, "verb": verb, "style": style,
            "category": "link", "state": "resolved", "grouping": False,
            "bridged": style == "dotted", "links": [{"conn_type": conn_type}]}

    runs_as, into, out_of = [], {}, {}
    for source, target, conn_type in _links(graph):
        verb = runtime_verb(conn_type)
        if verb and _service(target) in PASS_THROUGH:
            into.setdefault(target, []).append((source, verb, conn_type))
        elif verb and _service(source) in PASS_THROUGH:
            out_of.setdefault(source, []).append(target)
        if verb:
            if conn_type in REVERSED:
                source, target = target, source
            add(source, target, verb, "solid", conn_type)
        elif conn_type in LIKELY:
            add(source, target, LIKELY[conn_type], "dotted", conn_type)
        elif conn_type in PRINCIPAL_ROLE_LINKS:
            runs_as.append((source, target, conn_type))

    for middle, sources in into.items():
        for source, verb, conn_type in sources:
            for target in out_of.get(middle, ()):
                add(source, target, verb, "solid", conn_type)

    grants = _grants(graph)
    for principal, role, conn_type in runs_as:
        for target in sorted(grants.get(role, ())):
            add(principal, target, GRANT_VERB, "dotted", f"bridge:{conn_type}")

    node_list = [dict(n) for n in nodes if n["id"] in drawn]
    edge_list = list(edges.values())
    return {"nodes": node_list, "edges": edge_list, "groups": _groups(graph, drawn),
            "stats": {"nodes": len(node_list), "edges": len(edge_list),
                      "grant_edges": sum(1 for e in edge_list if e["style"] == "dotted"),
                      "scanned_nodes": len(nodes)}}


def project_architecture(arch_graph, scan_graph, all_rows, project_id):
    """One project's architecture plus what it touches, with its network placement
    attached as "network" so the renderers can draw Region > VPC > subnet boxes."""
    scoped = extract_project(arch_graph, all_rows, project_id)
    node_ids = {n["id"] for n in scoped["nodes"]}
    scoped["network"] = network_placement(scan_graph, all_rows, node_ids)
    scoped["groups"] = scope_groups(arch_graph.get("groups") or [], node_ids)
    return scoped
