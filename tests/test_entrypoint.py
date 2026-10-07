"""The seam between the CLI scripts and the package: `from aws_resource_audit import run`
must be the function, not the same-named submodule, and the CLIs must really start."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from . import _PARENT  # noqa: F401  (import for the sys.path fixup side effect)
from . import corpus

import aws_resource_audit as audit

REPO = Path(__file__).resolve().parent.parent


def _load_entry_point(filename):
    """Load a repo-root CLI script by path (the package shadows its name). Safe: both
    scripts work under `if __name__ == "__main__"`."""
    path = REPO / filename
    spec = importlib.util.spec_from_file_location("_entrypoint_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _no_credentials_env():
    """The real environment minus every credential source. HOME stays (boto3 may live
    under it); AWS_* variables are removed, not emptied."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AWS_")}
    env.update({
        "AWS_CONFIG_FILE": "/nonexistent",
        "AWS_SHARED_CREDENTIALS_FILE": "/nonexistent",
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_DEFAULT_REGION": "us-east-1",
    })
    return env


class EntryPointTests(unittest.TestCase):
    def test_the_name_audit_py_imports_is_the_function_not_the_module(self):
        """`run` must resolve to the function, despite the same-named submodule."""
        from aws_resource_audit import run
        self.assertTrue(callable(run), "run resolved to the module, not the function")

    def test_the_cli_parses_its_flags(self):
        cli = _load_entry_point("aws_resource_audit.py")
        args = cli.build_parser().parse_args(
            ["--regions", "us-east-1", "--no-cost", "--profile", "aws-audit"])
        self.assertEqual(args.regions, ["us-east-1"])
        self.assertTrue(args.no_cost)
        self.assertEqual(args.profile, "aws-audit")

    def test_the_cli_runs_far_enough_to_need_credentials(self):
        """With no credentials the real entry point must fail at authentication, proving
        everything before it (imports, config, paths) ran."""
        result = subprocess.run(
            [sys.executable, "aws_resource_audit.py", "--regions", "us-east-1", "--no-cost",
             "--profile", "aws-audit"],
            cwd=REPO, capture_output=True, text=True, timeout=120,
            # Only credential lookup is redirected; HOME stays real.
            env=_no_credentials_env(),
        )
        combined = result.stdout + result.stderr
        self.assertNotIn("Traceback", combined,
                         f"the CLI crashed rather than reporting cleanly:\n{combined}")
        self.assertIn("Could not authenticate to AWS", combined,
                      f"expected a clean auth failure, got:\n{combined}")

    def test_profile_is_required(self):
        """No silent fallback to boto3's default chain: --profile is required."""
        result = subprocess.run(
            [sys.executable, "aws_resource_audit.py", "--regions", "us-east-1"],
            cwd=REPO, capture_output=True, text=True, timeout=120,
            env=_no_credentials_env(),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--profile", result.stdout + result.stderr)
        self.assertIn("required", result.stdout + result.stderr)

    def test_the_removed_output_dir_flag_fails_loudly(self):
        """A removed flag is an error, not silently accepted."""
        result = subprocess.run(
            [sys.executable, "aws_resource_audit.py", "--profile", "aws-audit", "--output-dir", "out"],
            cwd=REPO, capture_output=True, text=True, timeout=120,
            env=_no_credentials_env(),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized arguments", result.stdout + result.stderr)


class ReportEntryPointTests(unittest.TestCase):
    """The renderer's entry point, end to end with every credential source removed: a
    render needs no AWS."""

    def test_the_name_report_py_imports_is_the_function_not_the_module(self):
        from aws_resource_audit import render
        self.assertTrue(callable(render), "render resolved to the module, not the function")

    def test_the_cli_has_no_flags(self):
        """aws_regenerate_report.py has no flags; adding one must be a deliberate choice."""
        cli = _load_entry_point("aws_regenerate_report.py")
        parser = cli.build_parser()
        flags = {action.option_strings[0] for action in parser._actions
                 if action.option_strings}
        self.assertEqual(flags, {"-h"})

    def test_it_renders_a_snapshot_with_no_credentials(self):
        """The end-to-end claim: a real snapshot on disk, the real entry point,
        no AWS reachable, and six rendered files at the end of it."""
        rows, _contracted, full, notes = corpus.build()
        with tempfile.TemporaryDirectory() as tmp:
            # Where the renderer will look, from the same function it uses.
            scan_dir = os.path.normpath(os.path.join(tmp, audit.Settings.output_dir))
            paths = audit.output_paths(audit.Settings(output_dir=scan_dir))
            audit.write_snapshot(
                paths["snapshot"], rows, full, notes,
                account="123456789012", regions=["us-east-1"], scanned_at=audit.now())
            # Run in the temp directory: the (absent) config is read from the cwd.
            result = subprocess.run(
                [sys.executable, os.path.join(REPO, "aws_regenerate_report.py")],
                cwd=tmp, capture_output=True, text=True, timeout=120,
                env=_no_credentials_env(),
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("Traceback", combined, combined)
            self.assertEqual(result.returncode, 0, combined)
            for name in ("aws_inventory.csv", "aws_inventory.html",
                         "aws_inventory.json",
                         "aws_resource_graph.mmd", "grouping_audit.md"):
                self.assertTrue(
                    os.path.exists(os.path.join(os.path.dirname(paths["csv"]), name)),
                    name)
            # The file list and the messages doc are the CLI's to print.
            self.assertIn("Open the report:", result.stdout)
            self.assertIn("help/cli-messages.md", result.stdout)
            self.assertTrue((REPO / "help" / "cli-messages.md").exists())

    def test_the_library_render_prints_no_file_list(self):
        """The web app calls render() too, and its users cannot open these paths."""
        rows, _contracted, full, notes = corpus.build()
        lines = []
        with tempfile.TemporaryDirectory() as tmp:
            settings = audit.Settings(output_dir=tmp)
            paths = audit.output_paths(settings)
            audit.write_snapshot(
                paths["snapshot"], rows, full, notes,
                account="123456789012", regions=["us-east-1"], scanned_at=audit.now())
            previous = audit.console.set_sink(lambda message, *_: lines.append(message))
            try:
                audit.render(None, settings=settings)
            finally:
                audit.console.set_sink(previous)
        printed = "\n".join(lines)
        self.assertNotIn("Open the report", printed)
        self.assertNotIn("aws_regenerate_report.py", printed)

    def test_it_says_what_to_do_when_there_is_no_snapshot(self):
        """The likeliest first run: aws_regenerate_report.py before aws_resource_audit.py has ever run."""
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, os.path.join(REPO, "aws_regenerate_report.py")],
                cwd=tmp, capture_output=True, text=True, timeout=120,
                env=_no_credentials_env(),
            )
            combined = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", combined, combined)
            self.assertIn("Run aws_resource_audit.py first", combined)


class LibraryRaisesCliExitsTests(unittest.TestCase):
    """The library raises AuditError (catchable as Exception); only the CLI exits, with
    the same sentence and no traceback."""

    def test_audit_error_is_catchable_as_an_ordinary_exception(self):
        self.assertTrue(issubclass(audit.AuditError, Exception))

    def test_audit_error_is_not_a_system_exit(self):
        """The whole point. If this ever becomes true again, every `except
        Exception` in a caller silently stops catching configuration errors."""
        self.assertFalse(issubclass(audit.AuditError, SystemExit))

    def test_the_library_does_not_exit_the_process_itself(self):
        """No SystemExit or sys.exit() anywhere in the package, checked structurally."""
        import ast

        package = REPO / "aws_resource_audit"
        offenders = []
        for path in sorted(package.rglob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                # `raise SystemExit(...)` and a bare `raise SystemExit`
                if isinstance(node, ast.Raise):
                    exc = node.exc
                    if isinstance(exc, ast.Call):
                        exc = exc.func
                    if isinstance(exc, ast.Name) and exc.id == "SystemExit":
                        offenders.append(f"{path.name}:{node.lineno} raise SystemExit")
                # `sys.exit(...)` / `os._exit(...)`
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr in ("exit", "_exit"):
                        base = node.func.value
                        if isinstance(base, ast.Name) and base.id in ("sys", "os"):
                            offenders.append(f"{path.name}:{node.lineno} {base.id}.{node.func.attr}()")

        self.assertEqual(offenders, [],
                         "the library must raise AuditError and let the caller decide; "
                         "only aws_resource_audit.py and aws_regenerate_report.py may end the process:\n  "
                         + "\n  ".join(offenders))


if __name__ == "__main__":
    unittest.main()
