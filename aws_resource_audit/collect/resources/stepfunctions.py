"""Step Functions state machines: the newest execution is a genuine usage signal,
and the definition names the functions, queues and topics each one calls."""

import json

from ... import coverage
from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.arns import probable_resource_id_from_arn
from ...collect.calls import paged, safe_call, tags_to_dict
from ...rows import add_edge, error_row, new_row
from ...staleness import days_ago, flag_stale


def _arns_in(node):
    """Every resource ARN in an ASL definition, skipping "arn:aws:states:::" integrations."""
    if isinstance(node, dict):
        for value in node.values():
            yield from _arns_in(value)
    elif isinstance(node, list):
        for value in node:
            yield from _arns_in(value)
    elif isinstance(node, str) and node.startswith("arn:") and ":states:::" not in node:
        yield node


# Service integration -> (parameter that names the resource, its type, connection type).
_NAMED_BY_PARAMETER = {
    "dynamodb": ("TableName", "DynamoDBTable", "statemachine.dynamodb.table-parameter"),
    "s3": ("Bucket", "S3Bucket", "statemachine.s3bucket.bucket-parameter"),
}


def _integration(resource):
    """"dynamodb" from arn:aws:states:::dynamodb:putItem or ...:::aws-sdk:dynamodb:getItem."""
    if not isinstance(resource, str) or ":states:::" not in resource:
        return None
    action = resource.split(":states:::", 1)[1]
    action = action[len("aws-sdk:"):] if action.startswith("aws-sdk:") else action
    return action.split(":", 1)[0]


def _named_targets(node):
    """(name, type, conn_type) for every table or bucket a task names literally. A
    key ending ".$" or a {% %} value is filled in at run time, so it names nothing."""
    if isinstance(node, list):
        for value in node:
            yield from _named_targets(value)
        return
    if not isinstance(node, dict):
        return
    named = _NAMED_BY_PARAMETER.get(_integration(node.get("Resource")))
    params = node.get("Parameters") or node.get("Arguments")
    if named and isinstance(params, dict):
        value = params.get(named[0])
        if isinstance(value, str) and value and not value.startswith("{%"):
            yield value, named[1], named[2]
    for value in node.values():
        yield from _named_targets(value)


def _parse(definition):
    try:
        return json.loads(definition or "{}")
    except ValueError:
        return {}


def _definition_targets(definition):
    parsed = _parse(definition)
    seen = []
    for arn in _arns_in(parsed):
        rid, service = probable_resource_id_from_arn(arn)
        if rid and (rid, service) not in seen:
            seen.append((rid, service))
    return seen


def collect_state_machines(session, region):
    sfn = session.client("stepfunctions", region_name=region, config=RETRY_CONFIG)
    machines, page_error = paged(sfn, "list_state_machines", "stateMachines",
                                 service="StepFunctionsStateMachine")
    if page_error and not machines:
        return [error_row("StepFunctionsStateMachine", region, "ERROR", page_error)]
    rows = []
    for machine in machines:
        arn = machine["stateMachineArn"]
        name = machine["name"]
        detail = safe_call(sfn.describe_state_machine, stateMachineArn=arn)
        detail = {} if "__error__" in detail else detail
        runs = safe_call(sfn.list_executions, capability=coverage.METRICS,
                         stateMachineArn=arn, maxResults=1)
        latest = (runs.get("executions") or [{}])[0].get("startDate") if "__error__" not in runs else None
        created = machine.get("creationDate")
        tags_resp = safe_call(sfn.list_tags_for_resource, capability=coverage.TAGS, resourceArn=arn)
        tags = {} if "__error__" in tags_resp else tags_to_dict(tags_resp.get("tags", []), "key", "value")
        notes = "Billed per state transition (standard) or per request (express); idle costs nothing. "
        if "__error__" in runs:
            notes += "Execution history could not be read. "
        elif latest is None:
            notes += "No executions in AWS's retained history. "
        row = new_row(
            "StepFunctionsStateMachine", region, name, name, created, latest,
            days_ago(latest) if latest else days_ago(created), latest is None,
            flag_stale(days_ago(latest) if latest else None, days_ago(created), latest is None),
            notes, tags=tags, description=machine.get("type", ""), arn=arn)
        raw_capture.record("StepFunctionsStateMachine", region, name, detail or machine)
        role, _svc = probable_resource_id_from_arn(detail.get("roleArn") or "")
        add_edge(row, role, "runs as", "state machine roleArn",
                 conn_type="statemachine.iamrole.execution-role", target_service="IAMRole")
        for target_id, target_service in _definition_targets(detail.get("definition")):
            add_edge(row, target_id, "calls", "state machine definition",
                     conn_type="statemachine.any.definition-reference", target_service=target_service)
        for target_name, target_service, conn_type in dict.fromkeys(_named_targets(_parse(detail.get("definition")))):
            if conn_type == "statemachine.dynamodb.table-parameter":
                add_edge(row, target_name, "reads and writes", "task Parameters.TableName",
                         conn_type="statemachine.dynamodb.table-parameter", target_service=target_service)
            else:
                add_edge(row, target_name, "reads and writes", "task Parameters.Bucket",
                         conn_type="statemachine.s3bucket.bucket-parameter", target_service=target_service)
        rows.append(row)
    return rows
