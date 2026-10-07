"""Registry invariants: every add_edge site uses a registered id and every registered type
is reachable, read with ast so unexecuted call sites count too."""

import ast
import os
import unittest

from .fakes import audit
from aws_resource_audit import registry
from aws_resource_audit.errors import AuditError

SOURCE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "aws_resource_audit",
)


def _parse_source():
    """Every module in the package, as (path, tree) pairs."""
    trees = []
    for root, _dirs, files in os.walk(SOURCE_DIR):
        for name in sorted(files):
            if name.endswith(".py"):
                path = os.path.join(root, name)
                with open(path) as handle:
                    trees.append((path, ast.parse(handle.read(), filename=path)))
    return trees


def _add_edge_calls(trees):
    for path, tree in trees:
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "add_edge"):
                yield path, node


def _site(path, call):
    return f"{os.path.basename(path)}:{call.lineno}"


def _literal_kwarg(call, name):
    for keyword in call.keywords:
        if keyword.arg == name:
            if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                return keyword.value.value
            return None  # present, but computed at runtime
    return "__MISSING__"


class RegistryShapeTests(unittest.TestCase):

    def test_ids_are_unique(self):
        ids = [ct.id for ct in audit.CONNECTION_TYPES]
        self.assertEqual(len(ids), len(set(ids)), "duplicate connection type id")

    def test_by_id_index_is_complete(self):
        self.assertEqual(len(audit.CONNECTION_TYPES_BY_ID), len(audit.CONNECTION_TYPES))

    def test_default_states_are_valid(self):
        for ct in audit.CONNECTION_TYPES:
            self.assertIn(ct.default_state, audit.CONN_ALL_STATES, ct.id)

    def test_confidences_are_valid_and_displayable(self):
        for ct in audit.CONNECTION_TYPES:
            self.assertIn(ct.confidence, audit.CONFIDENCE_DISPLAY, ct.id)

    def test_kinds_are_valid(self):
        for ct in audit.CONNECTION_TYPES:
            self.assertIn(ct.kind, ("edge", "grouping"), ct.id)

    def test_every_type_has_a_description_and_mechanism(self):
        for ct in audit.CONNECTION_TYPES:
            self.assertTrue(ct.description.strip(), ct.id)
            self.assertTrue(ct.mechanism.strip(), ct.id)

    def test_non_authoritative_display_strings_are_recognised_as_weak_evidence(self):
        """Confidence display strings must match WEAK_EVIDENCE_MARKERS."""
        for confidence, display in audit.CONFIDENCE_DISPLAY.items():
            if confidence == audit.CONF_AUTHORITATIVE:
                continue
            self.assertTrue(
                any(marker in display for marker in audit.WEAK_EVIDENCE_MARKERS),
                f"{confidence!r} displays as {display!r}, which no WEAK_EVIDENCE_MARKER matches",
            )

    def test_heuristic_types_are_not_trusted_for_grouping_by_default(self):
        """The low-confidence mechanisms are the usual cause of a bad cluster
        merge, so none of them may default to full 'on'."""
        for ct in audit.CONNECTION_TYPES:
            if ct.confidence == audit.CONF_HEURISTIC and ct.kind == "edge":
                self.assertEqual(
                    ct.default_state, audit.CONN_REPORT_ONLY,
                    f"{ct.id} is a heuristic but defaults to {ct.default_state}",
                )


class RegistryCoverageTests(unittest.TestCase):
    """Keep the registry and the code that uses it in lockstep."""

    def setUp(self):
        self.trees = _parse_source()

    def test_the_source_scan_reaches_the_whole_package(self):
        """Floors on the source scan, so an empty glob cannot pass silently."""
        self.assertGreaterEqual(len(self.trees), 1, "no source modules found")
        self.assertGreaterEqual(
            len(list(_add_edge_calls(self.trees))), 37,
            "far fewer add_edge sites than expected - is the scan missing a module?",
        )

    def test_every_add_edge_site_passes_conn_type(self):
        for path, call in _add_edge_calls(self.trees):
            self.assertNotEqual(
                _literal_kwarg(call, "conn_type"), "__MISSING__",
                f"add_edge at {_site(path, call)} does not pass conn_type",
            )

    def test_every_add_edge_site_uses_a_registered_id(self):
        for path, call in _add_edge_calls(self.trees):
            value = _literal_kwarg(call, "conn_type")
            if value in (None, "__MISSING__"):
                continue  # computed at runtime; covered functionally below
            self.assertIn(
                value, audit.CONNECTION_TYPES_BY_ID,
                f"add_edge at {_site(path, call)} uses unregistered id {value!r}",
            )

    def test_env_var_strategies_all_return_registered_ids(self):
        """Every computed conn_type from env_value_resource_candidates is registered."""
        samples = [
            "arn:aws:dynamodb:us-east-1:111122223333:table/orders",
            '{"table":"arn:aws:dynamodb:us-east-1:111122223333:table/orders"}',
            "https://abc123.execute-api.us-east-1.amazonaws.com/prod",
            "orders-table",
        ]
        seen = set()
        for value in samples:
            for _candidate, _how, _service, conn_type in audit.env_value_resource_candidates(value):
                self.assertIn(conn_type, audit.CONNECTION_TYPES_BY_ID, value)
                seen.add(conn_type)
        self.assertEqual(
            seen,
            {
                "lambda.any.env-var-arn-value",
                "lambda.any.env-var-arn-embedded",
                "lambda.apigateway.env-var-execute-api-url",
                "lambda.any.env-var-bare-name",
            },
            "an env-var extraction strategy is unreachable from these samples",
        )

    def test_every_registered_type_is_actually_reachable(self):
        """No dead registry entries: every declared id must be referenced by an
        add_edge site, by the env-var extractor, or by a feeds_grouping() gate."""
        referenced = set()
        for _path, call in _add_edge_calls(self.trees):
            value = _literal_kwarg(call, "conn_type")
            if value not in (None, "__MISSING__"):
                referenced.add(value)

        # feeds_grouping("...") gates for the shared-attribute signals.
        for _path, tree in self.trees:
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id in ("feeds_grouping", "detection_enabled")
                        and node.args and isinstance(node.args[0], ast.Constant)):
                    referenced.add(node.args[0].value)

        # The runtime-computed env-var strategies.
        for value in ("arn:aws:s3:::bucket", '{"a":"arn:aws:s3:::bucket"}',
                      "https://abc123.execute-api.us-east-1.amazonaws.com/", "plain-name"):
            for _c, _h, _s, conn_type in audit.env_value_resource_candidates(value):
                referenced.add(conn_type)

        unreferenced = set(audit.CONNECTION_TYPES_BY_ID) - referenced
        self.assertEqual(
            unreferenced, set(),
            f"registered but never used: {sorted(unreferenced)}",
        )


class StateHelperTests(unittest.TestCase):

    def test_unknown_id_raises_with_a_helpful_message(self):
        with self.assertRaises(KeyError) as ctx:
            audit.connection_state("nope.not.real")
        self.assertIn("unknown connection type", str(ctx.exception))

    def test_detection_enabled_is_false_only_for_off(self):
        with audit.connection_states({"ec2.ami.image-id": audit.CONN_ON}):
            self.assertTrue(audit.detection_enabled("ec2.ami.image-id"))
        with audit.connection_states({"ec2.ami.image-id": audit.CONN_REPORT_ONLY}):
            self.assertTrue(audit.detection_enabled("ec2.ami.image-id"))
        with audit.connection_states({"ec2.ami.image-id": audit.CONN_OFF}):
            self.assertFalse(audit.detection_enabled("ec2.ami.image-id"))

    def test_feeds_grouping_is_true_only_for_on(self):
        with audit.connection_states({"ec2.ami.image-id": audit.CONN_ON}):
            self.assertTrue(audit.feeds_grouping("ec2.ami.image-id"))
        with audit.connection_states({"ec2.ami.image-id": audit.CONN_REPORT_ONLY}):
            self.assertFalse(audit.feeds_grouping("ec2.ami.image-id"))
        with audit.connection_states({"ec2.ami.image-id": audit.CONN_OFF}):
            self.assertFalse(audit.feeds_grouping("ec2.ami.image-id"))

    def test_context_manager_restores_previous_state_even_on_error(self):
        before = dict(audit.CONNECTION_STATES)
        with self.assertRaises(RuntimeError):
            with audit.connection_states({"ec2.ami.image-id": audit.CONN_OFF}):
                raise RuntimeError("boom")
        self.assertEqual(audit.CONNECTION_STATES, before)


class ResolveConnectionStatesTests(unittest.TestCase):

    def test_defaults_match_the_registry(self):
        states = audit.resolve_connection_states()
        for ct in audit.CONNECTION_TYPES:
            self.assertEqual(states[ct.id], ct.default_state)

    def test_an_override_switches_one_type_off(self):
        states = audit.resolve_connection_states({"ec2.ami.image-id": audit.CONN_OFF})
        self.assertEqual(states["ec2.ami.image-id"], audit.CONN_OFF)
        self.assertEqual(states["ec2.keypair.key-name"], audit.CONN_ON,
                         "an override must not disturb the types it doesn't name")

    def test_an_override_promotes_a_report_only_default(self):
        """Naming a type explicitly means "I want this", including for the
        heuristics that are report-only by default."""
        states = audit.resolve_connection_states({"amplify.apigateway.name-match": audit.CONN_ON})
        self.assertEqual(states["amplify.apigateway.name-match"], audit.CONN_ON)

    def test_glob_patterns_expand(self):
        states = audit.resolve_connection_states({"lambda.*": audit.CONN_OFF})
        lambda_ids = [c for c in states if c.startswith("lambda.")]
        self.assertTrue(lambda_ids)
        for cid in lambda_ids:
            self.assertEqual(states[cid], audit.CONN_OFF, cid)

    def test_comma_separated_values_expand(self):
        states = audit.resolve_connection_states(
            {"ec2.ami.image-id,ec2.keypair.key-name": audit.CONN_OFF})
        self.assertEqual(states["ec2.ami.image-id"], audit.CONN_OFF)
        self.assertEqual(states["ec2.keypair.key-name"], audit.CONN_OFF)

    def test_a_later_override_wins_over_an_earlier_glob(self):
        """Config order decides: a broad sweep can be narrowed by a later exception."""
        states = audit.resolve_connection_states(
            {"ec2.*": audit.CONN_OFF, "ec2.ami.image-id": audit.CONN_ON})
        self.assertEqual(states["ec2.ami.image-id"], audit.CONN_ON)
        self.assertEqual(states["ec2.keypair.key-name"], audit.CONN_OFF)

    def test_an_override_demotes_to_report_only_without_disabling(self):
        states = audit.resolve_connection_states({"ec2.ami.image-id": audit.CONN_REPORT_ONLY})
        self.assertEqual(states["ec2.ami.image-id"], audit.CONN_REPORT_ONLY)

    def test_authoritative_only_disables_every_softer_signal(self):
        states = audit.resolve_connection_states(authoritative_only=True)
        for ct in audit.CONNECTION_TYPES:
            if ct.confidence == audit.CONF_AUTHORITATIVE:
                self.assertNotEqual(states[ct.id], audit.CONN_OFF, ct.id)
            else:
                self.assertEqual(states[ct.id], audit.CONN_OFF, ct.id)

    def test_an_override_can_put_back_what_authoritative_only_removed(self):
        """authoritative_only is a floor, not a verdict: it is applied first so
        a named exception still stands."""
        states = audit.resolve_connection_states(
            {"amplify.apigateway.name-match": audit.CONN_REPORT_ONLY},
            authoritative_only=True)
        self.assertEqual(states["amplify.apigateway.name-match"], audit.CONN_REPORT_ONLY)

    def test_unknown_pattern_is_a_hard_error(self):
        """A typo must not silently leave a detection switched on."""
        with self.assertRaises(AuditError) as ctx:
            audit.resolve_connection_states({"lamda.*": audit.CONN_OFF})
        self.assertIn("matches no connection type", str(ctx.exception))

    def test_table_renders_every_type(self):
        table = audit.format_connection_type_table(audit.default_connection_states())
        for ct in audit.CONNECTION_TYPES:
            self.assertIn(ct.id, table)


class ConfigStateWiringTests(unittest.TestCase):
    """Configured states reach the gate inside add_edge() (set_connection_states)."""

    def setUp(self):
        self.original = dict(registry.CONNECTION_STATES)
        self.addCleanup(registry.set_connection_states, self.original)

    @staticmethod
    def _edge_recorded(conn_type):
        row = audit.new_row("EC2Instance", "us-east-1", "i-1", "n", "running",
                            "", "", None, True, "ACTIVE", "")
        audit.add_edge(row, "ami-1", "uses AMI", "instance ImageId",
                       conn_type=conn_type)
        return bool(row["_edges"])

    def test_switching_a_type_off_stops_add_edge_recording_it(self):
        registry.set_connection_states(
            audit.resolve_connection_states({"ec2.ami.image-id": audit.CONN_OFF}))
        self.assertFalse(self._edge_recorded("ec2.ami.image-id"),
                         "the configured 'off' state did not reach add_edge's gate")

    def test_a_glob_reaches_the_gate_for_every_type_it_matched(self):
        registry.set_connection_states(
            audit.resolve_connection_states({"ec2.ami.*": audit.CONN_OFF}))
        self.assertFalse(self._edge_recorded("ec2.ami.image-id"))
        self.assertTrue(self._edge_recorded("ec2.keypair.key-name"))

    def test_the_default_state_still_records(self):
        registry.set_connection_states(audit.default_connection_states())
        self.assertTrue(self._edge_recorded("ec2.ami.image-id"))

    def test_the_package_attribute_tracks_the_live_dict(self):
        """audit.CONNECTION_STATES must not be a frozen import-time copy."""
        registry.set_connection_states(
            audit.resolve_connection_states({"ec2.ami.image-id": audit.CONN_OFF}))
        self.assertEqual(audit.CONNECTION_STATES["ec2.ami.image-id"], audit.CONN_OFF)


if __name__ == "__main__":
    unittest.main()
