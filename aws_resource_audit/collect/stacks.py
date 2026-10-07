"""What a CloudFormation stack provisioned: AWS's own record of ownership. Every live
stack is walked, nested stacks included and recorded in their own right."""

import logging

from botocore.exceptions import BotoCoreError, ClientError

from .. import coverage
from ..config import RETRY_CONFIG
from .calls import error_code
from ..rows import add_edge

logger = logging.getLogger(__name__)

# How deep to follow nested stacks.
MAX_STACK_DEPTH = 6

# CloudFormation type -> internal service name. Unlisted types are kept as
# unresolved members rather than invented rows.
CFN_TYPE_TO_SERVICE = {
    "AWS::ApiGateway::RestApi": "APIGatewayRestApi",
    "AWS::ApiGatewayV2::Api": "APIGatewayV2Api",
    "AWS::Lambda::Function": "LambdaFunction",
    "AWS::DynamoDB::Table": "DynamoDBTable",
    "AWS::S3::Bucket": "S3Bucket",
    "AWS::Cognito::UserPool": "CognitoUserPool",
    "AWS::CloudFormation::Stack": "CloudFormationStack",
    "AWS::SQS::Queue": "SQSQueue",
    "AWS::SNS::Topic": "SNSTopic",
    "AWS::EC2::Instance": "EC2Instance",
    "AWS::EC2::SecurityGroup": "SecurityGroup",
    "AWS::RDS::DBInstance": "RDSInstance",
    "AWS::IAM::Role": "IAMRole",
    "AWS::Logs::LogGroup": "CloudWatchLogGroup",
    "AWS::ElasticLoadBalancingV2::LoadBalancer": "LoadBalancer",
}


def walk_stack_resources(cfn, stack_name, seen=None, depth=0,
                         max_depth=MAX_STACK_DEPTH, provenance=None,
                         category=""):
    """{ResourceType: set(PhysicalResourceId)} for a stack and its nested stacks. `seen`
    prevents double walks; `provenance` records logical id and outermost category."""
    if seen is None:
        seen = set()
    result = {}
    if not stack_name or stack_name in seen or depth > max_depth:
        return result
    seen.add(stack_name)
    try:
        for page in cfn.get_paginator("list_stack_resources").paginate(
                StackName=stack_name):
            for res in page.get("StackResourceSummaries", []):
                rtype = res.get("ResourceType")
                pid = res.get("PhysicalResourceId")
                if not pid:
                    continue
                logical = res.get("LogicalResourceId") or ""
                if provenance is not None:
                    provenance.setdefault(pid, {
                        "logical_id": logical, "category": category, "type": rtype,
                    })
                result.setdefault(rtype, set()).add(pid)
                if rtype == "AWS::CloudFormation::Stack":
                    nested = walk_stack_resources(
                        cfn, pid, seen, depth + 1, max_depth,
                        provenance=provenance, category=category or logical.lower())
                    for k, v in nested.items():
                        result.setdefault(k, set()).update(v)
    except (ClientError, BotoCoreError) as e:
        # Non-fatal but never silent: a partial walk changes project membership.
        logger.warning("stack walk stopped at %s (%s); resources found so far "
                       "are all that will be linked", stack_name, e, exc_info=True)
        coverage.record(coverage.DEPENDENCIES,
                        coverage.PARTIAL if result else coverage.classify_error(str(e), error_code(e)),
                        operation="ListStackResources",
                        service="CloudFormationStack", error=str(e))
    return result


def apply_stack_membership(all_rows, session):
    """Link every live stack to what it provisioned, walking each stack once."""
    stacks = [r for r in all_rows
              if r["service"] == "CloudFormationStack" and r["flag"] != "ERROR"]
    if not stacks:
        return

    by_region = {}
    for row in stacks:
        by_region.setdefault(row["region"], []).append(row)

    for region, rows in sorted(by_region.items()):
        cfn = session.client("cloudformation", region_name=region,
                             config=RETRY_CONFIG)
        seen = set()
        for row in sorted(rows, key=lambda r: r["resource_id"]):
            provenance = {}
            with coverage.collecting("CloudFormation stack walk", region,
                                     "CloudFormationStack"):
                found = walk_stack_resources(
                    cfn, row["resource_id"], seen=seen, provenance=provenance)
            for cfn_type, physical_ids in sorted(found.items()):
                service = CFN_TYPE_TO_SERVICE.get(cfn_type)
                for pid in sorted(physical_ids):
                    if pid == row["resource_id"]:
                        continue
                    where = provenance.get(pid, {})
                    detail = where.get("category") or where.get("logical_id") or ""
                    add_edge(
                        row, pid, "provisions",
                        f"CloudFormation stack resource ({cfn_type}"
                        + (f", {detail}" if detail else "") + ")",
                        conn_type="cfnstack.any.stack-resource",
                        # Stacks provision types this tool does not scan; not findings.
                        assert_exists=False,
                        target_service=service,
                        target_region=None if service == "S3Bucket" else region)
