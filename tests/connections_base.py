"""Shared by the test_connections_* modules: assert_three_states and the mock
session builders the connection tests drive real collectors with."""

import contextlib
import io
import unittest

from .fakes import NO_METRICS, FakeSession, aws_ts, find_edge, audit, recent


REGION = "us-east-1"
ACCOUNT = "111122223333"


def epoch_ms(dt):
    return int(dt.timestamp() * 1000)


def quiet(fn, *args, **kwargs):
    """Run something that prints a summary, discarding stdout."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def instance_payload(**overrides):
    payload = {
        "InstanceId": "i-0abc", "ImageId": "ami-0123", "KeyName": "deploy-key",
        "SecurityGroups": [{"GroupId": "sg-0123", "GroupName": "web"}],
        "State": {"Name": "running"}, "LaunchTime": recent(),
        "InstanceType": "t3.micro", "VpcId": "vpc-0123", "SubnetId": "subnet-0123",
        "Tags": [{"Key": "Name", "Value": "web-server"}],
    }
    payload.update(overrides)
    return payload


def ec2_session(instances=None, extra_ec2=None, cw=None):
    ec2_ops = {"describe_instances": {"Reservations": [{"Instances": instances or []}]}}
    ec2_ops.update(extra_ec2 or {})
    return FakeSession({"ec2": ec2_ops, "cloudwatch": cw or NO_METRICS})


def lambda_session(functions, aliases=None, cw=None, extra=None):
    ops = {
        "list_event_source_mappings": {"EventSourceMappings": []},
        "list_functions": {"Functions": functions},
        "list_tags": {"Tags": {}},
        "list_aliases": {"Aliases": aliases or []},
    }
    ops.update(extra or {})
    return FakeSession({"lambda": ops, "cloudwatch": cw or NO_METRICS})


def function_payload(**overrides):
    payload = {
        "FunctionName": "orders-fn",
        "FunctionArn": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:orders-fn",
        "Role": f"arn:aws:iam::{ACCOUNT}:role/orders-role",
        "LastModified": aws_ts(recent()),
        "Runtime": "python3.11", "State": "Active",
    }
    payload.update(overrides)
    return payload


def iam_session(users=None, groups=None, policies=None, roles=None, extra=None):
    ops = {
        "list_users": {"Users": users or []},
        "list_groups": {"Groups": groups or []},
        "list_policies": {"Policies": policies or []},
        "list_roles": {"Roles": roles or []},
        "list_role_policies": {"PolicyNames": []},
        "list_attached_role_policies": {"AttachedPolicies": []},
    }
    ops.update(extra or {})
    return FakeSession({"iam": ops})


def sqs_session(queue_name, attributes=None, cw=None):
    attrs = {"QueueArn": f"arn:aws:sqs:{REGION}:{ACCOUNT}:{queue_name}",
             "CreatedTimestamp": str(int(recent(60).timestamp())),
             "ApproximateNumberOfMessages": "0"}
    attrs.update(attributes or {})
    return FakeSession({
        "sqs": {
            "list_queues": {"QueueUrls": [
                f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/{queue_name}"]},
            "get_queue_attributes": {"Attributes": attrs},
            "list_queue_tags": {"Tags": {}},
        },
        "cloudwatch": cw or NO_METRICS,
    })


def sns_session(topic_name, subscriptions=None, attributes=None, cw=None):
    attrs = {"SubscriptionsConfirmed": str(len(subscriptions or [])),
             "SubscriptionsPending": "0"}
    attrs.update(attributes or {})
    return FakeSession({
        "sns": {
            "list_topics": {"Topics": [
                {"TopicArn": f"arn:aws:sns:{REGION}:{ACCOUNT}:{topic_name}"}]},
            "get_topic_attributes": {"Attributes": attrs},
            "list_tags_for_resource": {"Tags": []},
            "list_subscriptions_by_topic": {"Subscriptions": subscriptions or []},
        },
        "cloudwatch": cw or NO_METRICS,
    })


def cognito_session(pool_detail):
    return FakeSession({"cognito-idp": {
        "list_user_pools": {"UserPools": [{
            "Id": "us-east-1_abc123", "Name": "orders-users", "Status": "ENABLED",
            "CreationDate": recent(300), "LastModifiedDate": recent(20),
        }]},
        "describe_user_pool": {"UserPool": pool_detail},
    }})


class ConnectionTypeTestCase(unittest.TestCase):
    """Base class providing the three-state assertion."""

    def _row(self, rows, resource_id):
        for row in rows:
            if row["resource_id"] == resource_id:
                return row
        self.fail(f"no row with resource_id {resource_id!r}; got "
                  f"{[r['resource_id'] for r in rows]}")

    @staticmethod
    def _isolated(conn_type, state):
        """Everything off except the type under test."""
        return audit.connection_states(
            {conn_type: state},
            base={cid: audit.CONN_OFF for cid in audit.CONNECTION_TYPES_BY_ID},
        )

    def assert_three_states(self, conn_type, build_rows, source_id, target_id,
                            expect_target_annotation=True):
        """`build_rows` must rebuild the rows each time: edges are filtered at creation."""
        self.assertIn(conn_type, audit.CONNECTION_TYPES_BY_ID)

        # Match the qualified "<Service>:<target_id>" form; a bare id is often a substring
        # of unrelated collector text.
        def needle(rows):
            return f"{self._row(rows, target_id)['service']}:{target_id}"

        # --- on ------------------------------------------------------------
        with self._isolated(conn_type, audit.CONN_ON):
            rows = build_rows()
            links = audit.resolve_edges(rows)
            source = self._row(rows, source_id)
            target = self._row(rows, target_id)

            edge = find_edge(source, conn_type)
            self.assertIsNotNone(edge, f"{conn_type}: collector produced no edge")
            self.assertEqual(edge["target_id"], target_id, f"{conn_type}: wrong target")
            self.assertIn(needle(rows), audit.render_connections(source),
                          f"{conn_type}: source row doesn't mention the target")
            if expect_target_annotation:
                self.assertIn("used by", audit.render_connections(target),
                              f"{conn_type}: target row not reverse-annotated")
            self.assertTrue(links, f"{conn_type}: resolved but produced no grouping link")

            # Grouping is decided by what the link MEANS (its ownership), not its state,
            # so ask the registry what this type may claim.
            quiet(audit.apply_project_grouping, rows, edge_links=links)
            ownership = audit.CONNECTION_TYPES_BY_ID[conn_type].ownership
            if ownership == "ownership":
                self.assertTrue(source["project_id"] or target["project_id"],
                                f"{conn_type}: an ownership link grouped nothing")
                self.assertEqual(source["project_id"], target["project_id"],
                                 f"{conn_type}: endpoints landed in different projects")
            else:
                self.assertIn(needle(rows), audit.render_connections(source),
                              f"{conn_type}: the dependency must remain visible")

        # --- report-only ---------------------------------------------------
        with self._isolated(conn_type, audit.CONN_REPORT_ONLY):
            rows = build_rows()
            links = audit.resolve_edges(rows)
            source = self._row(rows, source_id)
            target = self._row(rows, target_id)

            self.assertIsNotNone(find_edge(source, conn_type),
                                 f"{conn_type}: report-only should still detect")
            self.assertIn(needle(rows), audit.render_connections(source),
                          f"{conn_type}: report-only should still be reported")
            self.assertIn("report-only", audit.render_connections(source),
                          f"{conn_type}: report-only link doesn't say why it isn't grouping")
            self.assertEqual(links, [], f"{conn_type}: report-only must not feed grouping")

            quiet(audit.apply_project_grouping, rows, edge_links=links)
            self.assertEqual(source["project_group"], "",
                             f"{conn_type}: report-only still grouped the source")

        # --- off -----------------------------------------------------------
        with self._isolated(conn_type, audit.CONN_OFF):
            rows = build_rows()
            links = audit.resolve_edges(rows)
            source = self._row(rows, source_id)

            self.assertIsNone(find_edge(source, conn_type),
                              f"{conn_type}: 'off' still produced an edge")
            self.assertNotIn(needle(rows), audit.render_connections(source),
                             f"{conn_type}: 'off' still reported the link")
            self.assertEqual(links, [], f"{conn_type}: 'off' still produced a grouping link")
