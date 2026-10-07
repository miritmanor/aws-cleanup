"""audit_config.json parsing and the output layout: a bad config fails at load time,
before any AWS call, never by quietly changing the scan."""

import json
import os
import tempfile
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import aws_resource_audit as audit
from aws_resource_audit.errors import AuditError
from aws_resource_audit.settings import (
    ROLE_NAME_ENV,
    Settings,
    load_settings,
    output_paths,
    resolved_role_name,
)


class LoadSettingsTests(unittest.TestCase):

    def _write(self, data):
        path = os.path.join(self.tmp, "audit_config.json")
        with open(path, "w") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))
        return path

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.tmp = self.tmpdir.name

    def test_a_missing_file_is_the_default_configuration(self):
        """No config must mean the behaviour of a plain run, not an error -
        the file is optional and most accounts never need one."""
        settings = load_settings(os.path.join(self.tmp, "nope.json"))
        self.assertEqual(settings, Settings())
        self.assertEqual(settings.connection_types, {})
        self.assertTrue(settings.graph_in_html)

    def test_an_empty_object_is_also_the_defaults(self):
        self.assertEqual(load_settings(self._write({})), Settings())

    def test_it_reads_every_setting(self):
        settings = load_settings(self._write({
            "connection_types": {"ec2.ami.image-id": "off"},
            "authoritative_only": True,
            "graph_in_html": False,
            "graph_all_nodes": True,
            "bubbles_in_html": False,
            "bubbles_default_metric": "cost",
            "assume_role": True,
            "role_name": "MyCustomAuditRole",
        }))
        self.assertEqual(settings.connection_types, {"ec2.ami.image-id": "off"})
        self.assertTrue(settings.authoritative_only)
        self.assertFalse(settings.graph_in_html)
        self.assertTrue(settings.graph_all_nodes)
        self.assertFalse(settings.bubbles_in_html)
        self.assertEqual(settings.bubbles_default_metric, "cost")
        self.assertTrue(settings.assume_role)
        self.assertEqual(settings.role_name, "MyCustomAuditRole")

    def test_assume_role_and_role_name_default_off(self):
        """A fresh install has assume_role off."""
        settings = load_settings(self._write({}))
        self.assertFalse(settings.assume_role)
        self.assertEqual(settings.role_name, "AWS-audit-role")

    def test_assume_role_must_be_a_bool(self):
        with self.assertRaises(AuditError) as ctx:
            load_settings(self._write({"assume_role": "true"}))
        self.assertIn("must be true or false", str(ctx.exception))

    def test_role_name_must_not_be_empty(self):
        with self.assertRaises(AuditError) as ctx:
            load_settings(self._write({"role_name": ""}))
        self.assertIn("must not be empty", str(ctx.exception))

    def test_the_project_overview_is_on_by_default(self):
        """It needs no CDN - the circles are computed in Python and embedded as
        data - so there is no blocked-network reason to ship it off."""
        settings = load_settings(self._write({}))
        self.assertTrue(settings.bubbles_in_html)
        self.assertEqual(settings.bubbles_default_metric, "resource_count")

    def test_an_unknown_default_metric_is_refused(self):
        """An unknown overview metric is refused, not silently defaulted."""
        with self.assertRaises(AuditError) as ctx:
            load_settings(self._write({"bubbles_default_metric": "resources"}))
        self.assertIn("bubbles_default_metric", str(ctx.exception))
        self.assertIn("resource_count", str(ctx.exception))

    def test_every_declared_metric_is_accepted(self):
        """The validator and present/bubbles must agree on the whole set, not
        just on the default."""
        from aws_resource_audit.config import BUBBLE_METRIC_IDS
        for metric in BUBBLE_METRIC_IDS:
            settings = load_settings(self._write({"bubbles_default_metric": metric}))
            self.assertEqual(settings.bubbles_default_metric, metric)

    def test_bubbles_in_html_must_be_a_bool(self):
        with self.assertRaises(AuditError) as ctx:
            load_settings(self._write({"bubbles_in_html": "yes"}))
        self.assertIn("must be true or false", str(ctx.exception))

    def test_settings_reach_the_state_map_they_configure(self):
        """The join between the file and the registry: parsing the override is
        worthless if it doesn't land in the resolved states."""
        settings = load_settings(self._write(
            {"connection_types": {"ec2.ami.image-id": "off"}}))
        states = audit.resolve_connection_states(
            overrides=settings.connection_types,
            authoritative_only=settings.authoritative_only)
        self.assertEqual(states["ec2.ami.image-id"], audit.CONN_OFF)

    def test_an_unknown_setting_is_rejected(self):
        """A misremembered key would otherwise be a setting that silently does
        nothing, which reads exactly like one that is switched off."""
        with self.assertRaises(AuditError) as ctx:
            load_settings(self._write({"graph_all_node": True}))
        self.assertIn("unknown setting", str(ctx.exception))

    def test_an_unknown_connection_type_id_is_rejected(self):
        """A typo'd connection-type key is caught end to end (by resolve_connection_states)."""
        settings = load_settings(self._write({"connection_types": {"ec2.ami.image-di": "off"}}))
        with self.assertRaises(AuditError) as ctx:
            audit.resolve_connection_states(overrides=settings.connection_types)
        self.assertIn("matches no connection type", str(ctx.exception))

    def test_an_invalid_state_is_rejected(self):
        with self.assertRaises(AuditError) as ctx:
            load_settings(self._write({"connection_types": {"ec2.ami.image-id": "yes"}}))
        self.assertIn("expected one of", str(ctx.exception))

    def test_a_non_boolean_switch_is_rejected(self):
        """"graph_in_html": "false" is truthy in Python and would turn the
        panel ON while reading as OFF."""
        with self.assertRaises(AuditError) as ctx:
            load_settings(self._write({"graph_in_html": "false"}))
        self.assertIn("must be true or false", str(ctx.exception))

    def test_malformed_json_is_rejected(self):
        with self.assertRaises(AuditError) as ctx:
            load_settings(self._write("{not json"))
        self.assertIn("could not be read", str(ctx.exception))

    def test_a_top_level_list_is_rejected(self):
        with self.assertRaises(AuditError) as ctx:
            load_settings(self._write([{"graph_in_html": False}]))
        self.assertIn("expected a JSON object", str(ctx.exception))

    def test_connection_types_must_be_an_object(self):
        with self.assertRaises(AuditError) as ctx:
            load_settings(self._write({"connection_types": ["ec2.ami.image-id"]}))
        self.assertIn("must be an object", str(ctx.exception))


class OutputLocationSettingTests(unittest.TestCase):
    """output_dir and snapshot_file: standing settings for where a scan puts itself."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def _write(self, data):
        path = os.path.join(self.tmpdir.name, "audit_config.json")
        with open(path, "w") as handle:
            json.dump(data, handle)
        return path

    def test_defaults_put_a_scan_in_its_own_directory(self):
        """The default output_dir is a named directory, easy to ignore and delete."""
        settings = load_settings(os.path.join(self.tmpdir.name, "absent.json"))
        self.assertEqual(settings.output_dir, "./Output")
        self.assertEqual(settings.snapshot_file, "aws_scan.json")

    def test_both_are_read_from_the_config(self):
        settings = load_settings(self._write(
            {"output_dir": "out", "snapshot_file": "scan-2026.json"}))
        self.assertEqual(settings.output_dir, "out")
        self.assertEqual(settings.snapshot_file, "scan-2026.json")

    def test_a_non_string_is_rejected(self):
        with self.assertRaises(AuditError) as ctx:
            load_settings(self._write({"output_dir": True}))
        self.assertIn("must be a string", str(ctx.exception))

    def test_an_empty_value_is_rejected(self):
        with self.assertRaises(AuditError) as ctx:
            load_settings(self._write({"snapshot_file": ""}))
        self.assertIn("must not be empty", str(ctx.exception))

    def test_a_snapshot_path_is_refused_rather_than_resolved(self):
        """snapshot_file must be a bare filename inside output_dir."""
        for name in ("../scan.json", "sub/scan.json", "/tmp/scan.json"):
            with self.subTest(name=name):
                with self.assertRaises(AuditError) as ctx:
                    load_settings(self._write({"snapshot_file": name}))
                self.assertIn("bare filename", str(ctx.exception))

    def test_a_snapshot_must_be_json(self):
        """.gitignore matches the snapshot by name, and it is a complete map of
        the account. A scan.txt would sit untracked-but-committable."""
        with self.assertRaises(AuditError) as ctx:
            load_settings(self._write({"snapshot_file": "scan.txt"}))
        self.assertIn("must end in .json", str(ctx.exception))


class OutputPathTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def _paths(self, output_dir, snapshot_file="aws_scan.json"):
        return output_paths(audit.Settings(output_dir=output_dir,
                                           snapshot_file=snapshot_file))

    def _results(self, *parts):
        return os.path.join(self.tmpdir.name, audit.RESULTS_DIRNAME, *parts)

    def _runtime(self, *parts):
        return os.path.join(self.tmpdir.name, audit.RUNTIME_DIRNAME, *parts)

    def test_every_output_the_run_writes_has_a_path(self):
        """Every key run.py and render.py index paths[...] by is present."""
        self.assertEqual(
            set(self._paths(self.tmpdir.name)),
            {"csv", "json", "html", "mermaid", "drawio", "audit", "groups", "snapshot",
             "active_regions", "raw_capture", "normalized_scan_results"})

    def test_it_creates_both_directories_that_do_not_exist_yet(self):
        target = os.path.join(self.tmpdir.name, "new", "nested")
        paths = self._paths(target)
        self.assertTrue(os.path.isdir(os.path.join(target, audit.RESULTS_DIRNAME)))
        self.assertTrue(os.path.isdir(os.path.join(target, audit.RUNTIME_DIRNAME)))
        self.assertEqual(os.path.dirname(paths["csv"]),
                         os.path.join(target, audit.RESULTS_DIRNAME))

    def test_the_filenames_are_the_documented_ones(self):
        """These names appear in the README, the report and the console summary;
        renaming one silently breaks a cross-reference rather than a test."""
        paths = self._paths(self.tmpdir.name)
        self.assertEqual(os.path.basename(paths["csv"]), "aws_inventory.csv")
        self.assertEqual(os.path.basename(paths["html"]), "aws_inventory.html")
        self.assertEqual(os.path.basename(paths["groups"]), "aws_project_groups.json")
        self.assertEqual(os.path.basename(paths["mermaid"]), "aws_resource_graph.mmd")
        self.assertEqual(os.path.basename(paths["drawio"]), "aws_architecture.drawio")

    def test_finished_outputs_and_working_notes_are_kept_apart(self):
        """Outputs go to results/, debug dumps to runtime/."""
        paths = self._paths(self.tmpdir.name)
        for kind in ("csv", "json", "html", "mermaid", "audit", "groups", "snapshot"):
            self.assertEqual(os.path.dirname(paths[kind]), self._results(), kind)
        for kind in ("raw_capture", "normalized_scan_results"):
            self.assertEqual(os.path.dirname(paths[kind]), self._runtime(), kind)

    def test_the_snapshot_name_is_the_configured_one(self):
        """The one output whose name is not fixed. Nothing links to it, because
        it is the renderer's input rather than one of its outputs."""
        paths = self._paths(self.tmpdir.name, snapshot_file="scan-2026.json")
        self.assertEqual(os.path.basename(paths["snapshot"]), "scan-2026.json")
        self.assertEqual(os.path.dirname(paths["snapshot"]), self._results())

    def test_no_directory_falls_back_to_the_default_not_the_cwd(self):
        """An empty output_dir means the default directory, not "."."""
        self.assertEqual(
            self._paths("")["csv"],
            os.path.join(audit.Settings.output_dir, audit.RESULTS_DIRNAME,
                         "aws_inventory.csv"))


class ResolvedRoleNameTests(unittest.TestCase):
    """Which role a scan assumes: the env var, else the file's role_name."""

    def test_the_configured_name_wins_with_no_env_override(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(ROLE_NAME_ENV, None)
            self.assertEqual(
                resolved_role_name(Settings(role_name="CustomRole")), "CustomRole")

    def test_the_env_var_overrides_the_configured_name(self):
        with mock.patch.dict(os.environ, {ROLE_NAME_ENV: "EnvOverrideRole"}):
            self.assertEqual(
                resolved_role_name(Settings(role_name="CustomRole")), "EnvOverrideRole")


if __name__ == "__main__":
    unittest.main()


class GitignoreCoverageTests(unittest.TestCase):
    """Every file a scan writes is ignored by git - and not tracked, which
    `git check-ignore` alone would miss."""

    def setUp(self):
        # These check the repository, not the code: a Docker build stage has no .git to ask.
        if not (Path(__file__).resolve().parent.parent / ".git").exists() or not shutil.which("git"):
            self.skipTest("not a git checkout")

    def _ignored(self, names):
        repo = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            ["git", "check-ignore", "--stdin"],
            cwd=repo, input="\n".join(names), capture_output=True, text=True)
        return set(result.stdout.split())

    def test_every_output_filename_is_gitignored(self):
        names = sorted(audit.OUTPUT_FILENAMES.values())
        missing = [n for n in names if n not in self._ignored(names)]
        self.assertEqual(
            missing, [],
            f"these outputs are not gitignored and would be committed: {missing}")

    def test_every_runtime_filename_is_gitignored(self):
        """The debug dumps are not lesser data. raw_capture is the rawest form
        of the account there is - whatever each AWS API returned, verbatim."""
        names = sorted(audit.RUNTIME_FILENAMES.values())
        missing = [n for n in names if n not in self._ignored(names)]
        self.assertEqual(
            missing, [],
            f"these debug dumps are not gitignored and would be committed: {missing}")

    def test_the_default_output_directory_is_gitignored(self):
        """Anything a default scan writes under output_dir is ignored."""
        probe = os.path.join(audit.Settings.output_dir,
                             audit.RESULTS_DIRNAME, "anything_at_all.json")
        self.assertIn(probe, self._ignored([probe]))

    def test_the_default_snapshot_name_is_gitignored(self):
        """The default snapshot name is ignored; settings.py requires .json for that reason."""
        name = Settings().snapshot_file
        self.assertIn(
            name, self._ignored([name]),
            f"the default snapshot {name} is not gitignored - it is a complete "
            "inventory of the account")

    def test_the_golden_renders_are_still_tracked(self):
        """tests/golden/ stays tracked despite the broad ignore rules."""
        repo = Path(__file__).resolve().parent.parent
        golden = sorted((repo / "tests" / "golden").glob("*"))
        self.assertTrue(golden, "no golden files at all")
        result = subprocess.run(
            ["git", "check-ignore"] + [str(p) for p in golden],
            cwd=repo, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), "",
                         "golden files are being ignored by git")
