"""AppSync GraphQL APIs: billed per query and per real-time minute, so an idle one
is free - but often left behind after a prototype, still wired to its tables."""

from ...config import RETRY_CONFIG
from ...collect import raw_capture
from ...collect.arns import probable_resource_id_from_arn
from ...collect.calls import paged
from ...collect.cloudwatch import activity_note, cw_activity
from ...rows import add_edge, error_row, new_row
from ...staleness import days_ago, flag_from_activity
from ...text import join_nonempty


def _link_data_sources(client, row, api_id):
    sources, _error = paged(client, "list_data_sources", "dataSources", service="AppSyncApi", apiId=api_id)
    for source in sources:
        fn, _svc = probable_resource_id_from_arn((source.get("lambdaConfig") or {}).get("lambdaFunctionArn") or "")
        add_edge(row, fn, "resolves with", f"data source {source.get('name', '')}",
                 conn_type="appsync.lambda.datasource", target_service="LambdaFunction")
        add_edge(row, (source.get("dynamodbConfig") or {}).get("tableName"), "reads and writes",
                 f"data source {source.get('name', '')}", conn_type="appsync.dynamodb.datasource",
                 target_service="DynamoDBTable")


def _link_authorizers(row, api):
    """The primary and every additional authorization provider."""
    for provider in [api] + list(api.get("additionalAuthenticationProviders") or []):
        add_edge(row, (provider.get("userPoolConfig") or {}).get("userPoolId"), "authorizes with",
                 provider.get("authenticationType", ""), conn_type="appsync.cognito.authorizer",
                 target_service="CognitoUserPool")
        fn, _svc = probable_resource_id_from_arn(
            (provider.get("lambdaAuthorizerConfig") or {}).get("authorizerUri") or "")
        add_edge(row, fn, "authorizes with", provider.get("authenticationType", ""),
                 conn_type="appsync.lambda.authorizer", target_service="LambdaFunction")


def collect_appsync_apis(session, region):
    client = session.client("appsync", region_name=region, config=RETRY_CONFIG)
    apis, page_error = paged(client, "list_graphql_apis", "graphqlApis", service="AppSyncApi")
    if page_error and not apis:
        return [error_row("AppSyncApi", region, "ERROR", page_error)]
    cw = session.client("cloudwatch", region_name=region, config=RETRY_CONFIG)
    rows = []
    for api in apis:
        api_id = api["apiId"]
        # Requests is account-wide; Latency's sample count is this API's request count.
        evidence = cw_activity(cw, "AWS/AppSync", "Latency",
                               [{"Name": "GraphQLAPIId", "Value": api_id}], stat="SampleCount")
        last_used = evidence.last_activity
        row = new_row(
            "AppSyncApi", region, api_id, api.get("name", api_id), None, last_used,
            days_ago(last_used), not evidence.used, flag_from_activity(evidence, None),
            activity_note(evidence, "Latency sample count (requests) used as activity proxy")
            + " Billed per query and per real-time connection minute; an idle API is free. ",
            tags=dict(api.get("tags") or {}),
            description=join_nonempty([api.get("apiType", "GRAPHQL").lower(),
                                       api.get("authenticationType", "")], ", "),
            arn=api.get("arn", ""), activity=evidence)
        raw_capture.record("AppSyncApi", region, api_id, api)
        _link_data_sources(client, row, api_id)
        _link_authorizers(row, api)
        rows.append(row)
    return rows
