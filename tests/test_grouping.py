"""A dependency is not a claim of ownership: only containment and deployment assign a
project; every other link stays visible and suggests nothing on its own."""

import contextlib
import io
import unittest

from .fakes import audit, make_row

EAST = "us-east-1"


def group(rows, **kwargs):
    """Run the grouping pass quietly and return it."""
    with contextlib.redirect_stdout(io.StringIO()):
        return audit.apply_project_grouping(rows, **kwargs)


def projects(rows):
    return {r["resource_id"]: r["project_id"] for r in rows}


def tagged(service, resource_id, project, **kwargs):
    return make_row(service, resource_id, tags={"Project": project}, **kwargs)


class TagDimensionTests(unittest.TestCase):
    """Project, environment and component are three questions, not one."""

    def test_environment_alone_does_not_make_a_project(self):
        rows = [make_row("LambdaFunction", "a", tags={"Environment": "prod"}),
                make_row("S3Bucket", "b", region="global", tags={"Environment": "prod"})]
        group(rows)

        self.assertEqual([r["project_id"] for r in rows], ["", ""],
                         "everything in production is not one project")
        self.assertEqual([r["membership"] for r in rows],
                         ["unassigned", "unassigned"])

    def test_environment_is_still_recorded(self):
        rows = [make_row("LambdaFunction", "a",
                         tags={"Project": "orders", "Environment": "prod"})]
        group(rows)
        self.assertEqual(rows[0]["environment"], "prod")

    def test_project_and_service_tags_are_not_interchangeable(self):
        rows = [make_row("LambdaFunction", "a", tags={"Project": "orders"}),
                make_row("DynamoDBTable", "b", tags={"Service": "orders"})]
        group(rows)

        self.assertNotEqual(rows[0]["project_id"], rows[1]["project_id"])

    def test_the_same_project_tag_still_groups(self):
        rows = [tagged("LambdaFunction", "a", "orders"),
                tagged("DynamoDBTable", "b", "orders")]
        group(rows)

        self.assertEqual(rows[0]["project_id"], rows[1]["project_id"])
        self.assertTrue(rows[0]["project_id"])

    def test_two_project_tags_on_one_resource_are_reported(self):
        row = make_row("LambdaFunction", "a", tags={"Project": "orders", "App": "billing"})
        notes = group([row])
        self.assertTrue(notes["tag_conflicts"], "a contradictory tagging is a finding")


class SharedInfrastructureTests(unittest.TestCase):
    """The heart of it: shared things must not merge their consumers."""

    def _two_projects_sharing(self, conn_type, shared_service, shared_id, **edge):
        a = tagged("LambdaFunction", "orders-fn", "orders")
        b = tagged("LambdaFunction", "billing-fn", "billing")
        shared = make_row(shared_service, shared_id,
                          region="global" if shared_service.startswith("IAM") else EAST)
        for row in (a, b):
            audit.add_edge(row, shared_id, "uses", "test", conn_type=conn_type,
                           target_service=shared_service, **edge)
        rows = [a, b, shared]
        links = audit.resolve_edges(rows)
        group(rows, edge_links=links)
        return a, b, shared

    def test_a_shared_role_does_not_merge_two_projects(self):
        a, b, _role = self._two_projects_sharing(
            "cognito.iamrole.sms-caller", "IAMRole", "shared-exec")
        self.assertNotEqual(a["project_id"], b["project_id"])

    def test_a_shared_permission_policy_does_not_merge_two_projects(self):
        a, b, _bucket = self._two_projects_sharing(
            "iamrole.any.resource-grant", "S3Bucket", "shared-bucket")
        self.assertNotEqual(a["project_id"], b["project_id"])

    def test_a_shared_base_image_does_not_merge_two_projects(self):
        a, b, _ami = self._two_projects_sharing(
            "ec2.ami.image-id", "AMI", "ami-base")
        self.assertNotEqual(a["project_id"], b["project_id"])

    def test_a_shared_vpc_does_not_merge_two_projects(self):
        a = tagged("EC2Instance", "i-a", "orders", vpc_id="vpc-shared")
        b = tagged("EC2Instance", "i-b", "billing", vpc_id="vpc-shared")
        group([a, b])
        self.assertNotEqual(a["project_id"], b["project_id"])

    def test_a_shared_word_in_names_does_not_merge_anything(self):
        a = make_row("KeyPair", "moonpiekeypair")
        b = make_row("AMI", "moonpie-base")
        group([a, b])
        self.assertNotEqual((a["project_id"], a["membership"]),
                            (b["project_id"], "assigned"))
        self.assertFalse(a["project_id"],
                         "name resemblance groups nothing unless configured")

    def test_the_dependency_survives_even_though_it_does_not_group(self):
        """Disabling ownership inference must not erase the fact."""
        a, _b, role = self._two_projects_sharing(
            "cognito.iamrole.sms-caller", "IAMRole", "shared-exec")
        self.assertTrue([r for r in a["_references"] if r["kind"] == "resolved"],
                        "the role dependency is still recorded on the consumer")
        self.assertTrue(role["_references"], "and from the role's side too")


class OwnershipTests(unittest.TestCase):
    """Containment and deployment do assign a project."""

    def test_a_deployment_owned_resource_inherits_its_project(self):
        app = tagged("AmplifyApp", "amp-1", "portal")
        fn = make_row("LambdaFunction", "portal-fn")
        audit.add_edge(app, "portal-fn", "provisions", "stack walk",
                       conn_type="amplify.any.cfn-stack-walk",
                       target_service="LambdaFunction")
        rows = [app, fn]
        group(rows, edge_links=audit.resolve_edges(rows))

        self.assertEqual(fn["project_id"], app["project_id"])
        self.assertEqual(fn["membership"], "assigned")

    def test_an_ami_keeps_its_snapshots_without_claiming_its_users(self):
        ami = tagged("AMI", "ami-1", "imaging")
        snap = make_row("EBSSnapshot", "snap-1")
        user = tagged("EC2Instance", "i-1", "orders")
        audit.add_edge(ami, "snap-1", "backed by", "block device",
                       conn_type="ami.ebssnapshot.block-device",
                       target_service="EBSSnapshot")
        audit.add_edge(user, "ami-1", "launched from", "ImageId",
                       conn_type="ec2.ami.image-id", target_service="AMI")
        rows = [ami, snap, user]
        group(rows, edge_links=audit.resolve_edges(rows))

        self.assertEqual(snap["project_id"], ami["project_id"],
                         "the AMI owns its backing snapshot")
        self.assertNotEqual(user["project_id"], ami["project_id"],
                            "but an instance launched from it is not its project")

    def test_a_deployment_cannot_override_an_explicit_tag(self):
        app = tagged("AmplifyApp", "amp-1", "portal")
        fn = tagged("LambdaFunction", "shared-fn", "billing")
        audit.add_edge(app, "shared-fn", "provisions", "stack walk",
                       conn_type="amplify.any.cfn-stack-walk",
                       target_service="LambdaFunction")
        rows = [app, fn]
        notes = group(rows, edge_links=audit.resolve_edges(rows))

        self.assertNotEqual(fn["project_id"], app["project_id"])
        self.assertTrue(notes["conflicts"], "the contradiction is recorded")


class SharedAndAmbiguousTests(unittest.TestCase):
    """Two more answers than "grouped" and "ungrouped"."""

    def test_a_resource_used_by_two_projects_is_shared(self):
        a = tagged("LambdaFunction", "orders-fn", "orders")
        b = tagged("LambdaFunction", "billing-fn", "billing")
        bucket = make_row("S3Bucket", "shared-assets", region="global")
        for row in (a, b):
            audit.add_edge(row, "shared-assets", "reads", "env var",
                           conn_type="lambda.any.env-var-arn-value",
                           target_service="S3Bucket")
        rows = [a, b, bucket]
        group(rows, edge_links=audit.resolve_edges(rows))

        self.assertEqual(bucket["membership"], "shared")
        self.assertCountEqual(bucket["shared_with"],
                              [a["project_id"], b["project_id"]])

    def test_a_shared_dependency_does_not_claim_its_consumers_siblings(self):
        a = tagged("LambdaFunction", "orders-fn", "orders")
        b = tagged("LambdaFunction", "billing-fn", "billing")
        sibling = make_row("DynamoDBTable", "orders-table")
        bucket = make_row("S3Bucket", "shared-assets", region="global")
        for row in (a, b):
            audit.add_edge(row, "shared-assets", "reads", "env var",
                           conn_type="lambda.any.env-var-arn-value",
                           target_service="S3Bucket")
        rows = [a, b, sibling, bucket]
        group(rows, edge_links=audit.resolve_edges(rows))

        self.assertNotEqual(sibling["project_id"], b["project_id"])

    def test_an_unassignable_resource_says_so(self):
        lonely = make_row("SQSQueue", "orphan")
        group([lonely])
        self.assertEqual(lonely["membership"], "unassigned")


class StabilityTests(unittest.TestCase):
    """An assignment must not depend on the order rows arrived in."""

    def _assignments(self, rows):
        group(rows, edge_links=audit.resolve_edges(rows))
        return {r["resource_id"]: r["project_id"] for r in rows}

    def test_input_order_does_not_change_assignments(self):
        def build():
            app = tagged("AmplifyApp", "amp-1", "portal")
            fn = make_row("LambdaFunction", "portal-fn")
            audit.add_edge(app, "portal-fn", "provisions", "stack walk",
                           conn_type="amplify.any.cfn-stack-walk",
                           target_service="LambdaFunction")
            return [app, fn]

        forward = self._assignments(build())
        backward = self._assignments(list(reversed(build())))
        self.assertEqual(forward, backward)

    def test_unrelated_resources_do_not_change_an_assignment(self):
        def build(extra):
            app = tagged("AmplifyApp", "amp-1", "portal")
            fn = make_row("LambdaFunction", "portal-fn")
            audit.add_edge(app, "portal-fn", "provisions", "stack walk",
                           conn_type="amplify.any.cfn-stack-walk",
                           target_service="LambdaFunction")
            return [app, fn] + extra

        alone = self._assignments(build([]))
        crowded = self._assignments(build(
            [make_row("SQSQueue", f"noise-{i}") for i in range(20)]))
        self.assertEqual(alone["portal-fn"], crowded["portal-fn"])


class NameRuleTests(unittest.TestCase):
    """Name grouping is something you ask for, not something inferred."""

    def test_a_rule_groups_exactly_its_matches(self):
        kp = make_row("KeyPair", "moonpiekeypair")
        ami = make_row("AMI", "MoonPie base")
        other = make_row("SQSQueue", "unrelated")
        group([kp, ami, other], name_rules={"moonpie": "MoonPie"})

        self.assertEqual(kp["project_id"], ami["project_id"])
        self.assertEqual(kp["project_group"], "MoonPie")
        self.assertNotEqual(other["project_id"], kp["project_id"])

    def test_matching_is_case_insensitive_and_substring(self):
        row = make_row("SecurityGroup", "sg-1", name="MoonPie Windows Server")
        group([row], name_rules={"moonpie": "MoonPie"})
        self.assertEqual(row["project_group"], "MoonPie")

    def test_a_rule_matches_the_name_only(self):
        """Not the resource id, not the description, not the tags."""
        row = make_row("SecurityGroup", "moonpie-sg", name="web tier")
        group([row], name_rules={"moonpie": "MoonPie"})
        self.assertNotEqual(row["project_group"], "MoonPie")

    def test_a_rule_beats_an_anchor_and_the_conflict_is_reported(self):
        row = tagged("LambdaFunction", "fn-1", "billing", name="moonpie-worker")
        notes = group([row], name_rules={"moonpie": "MoonPie"})

        self.assertEqual(row["project_group"], "MoonPie")
        self.assertTrue(notes["conflicts"])


class CandidateWordTests(unittest.TestCase):
    """Candidate words: those worth writing a rule for, and no others."""

    def test_a_word_that_would_change_nothing_is_not_a_candidate(self):
        a = tagged("LambdaFunction", "orders-fn", "orders", name="orders-fn")
        b = tagged("DynamoDBTable", "orders-table", "orders", name="orders-table")
        notes = group([a, b])

        words = {w["word"] for w in notes["candidate_words"]}
        self.assertNotIn("orders", words,
                         "they are already one project; a rule would add nothing")

    def test_a_word_that_would_group_two_loose_resources_is_a_candidate(self):
        a = make_row("KeyPair", "moonpiekeypair", name="moonpiekeypair")
        b = make_row("AMI", "ami-1", name="moonpie base")
        notes = group([a, b])

        words = {w["word"] for w in notes["candidate_words"]}
        self.assertIn("moonpie", words)

    def test_candidates_change_no_grouping_by_themselves(self):
        a = make_row("KeyPair", "moonpiekeypair", name="moonpiekeypair")
        b = make_row("AMI", "ami-1", name="moonpie base")
        group([a, b])

        self.assertFalse(a["project_id"])
        self.assertFalse(b["project_id"])


if __name__ == "__main__":
    unittest.main()
