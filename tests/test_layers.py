"""The collect -> analyze -> present layering, checked statically with `ast`.
KNOWN_VIOLATIONS is a baseline that may shrink, never grow."""

import ast
import os
import unittest

PACKAGE_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aws_resource_audit")
PACKAGE = "aws_resource_audit"


# The rule, as a partial order: a module may import its own tier and below, never above.
BASE = 0        # constants and declarations: config, registry, settings
MODEL = 1       # the row/edge schema everything passes around
LAYER = 2       # collect, analyze, present - peers, and must not import
                # each other, which the rank alone cannot express
FEATURE = 3     # a vertical slice that legitimately spans layers; see below
APP = 4         # run.py and render.py wire everything together, so they see
                # everything - see APP_MODULES
FACADE = 5      # __init__.py re-exports the world for the test suite

TIER_NAMES = {BASE: "base", MODEL: "model", LAYER: "layer", FEATURE: "feature",
              APP: "app", FACADE: "facade"}

# Base-tier modules: channels and vocabulary every layer needs (console, logging, coverage,
# billing, errors, scope), so none of them can live inside one layer.
BASE_MODULES = {"activity", "aws_services", "billing", "billing_tables", "config", "console", "coverage",
                "connection_types", "errors", "logsetup", "region_store", "registry", "scope",
                "settings", "text"}
# Model tier: the row schema (rows, snapshot) and staleness, which collectors must call.
MODEL_MODULES = {"rows", "snapshot", "staleness"}
LAYER_PACKAGES = {"collect", "analyze", "present"}

# The orchestrators. run.py imports render.py; NoAwsFromTheRendererTests guards the reverse.
APP_MODULES = {"run", "render"}

# naming/ spans all three layers, so it sits above them and only entry points may use it.
FEATURE_PACKAGES = {"naming"}

# Modules with no layer yet. Empty: the escape hatch for a genuinely mixed new module.
UNLAYERED = set()

def _v(*pairs):
    return {(f"{PACKAGE}.{a}", f"{PACKAGE}.{b}") for a, b in pairs}


# This list may shrink, never grow. Empty: the package has no upward imports.
KNOWN_VIOLATIONS = set()


def _module_name(path):
    rel = os.path.relpath(path, os.path.dirname(PACKAGE_ROOT))
    parts = rel[: -len(".py")].split(os.sep)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _tier(module):
    """(rank, peer) for a dotted module name inside the package."""
    parts = module.split(".")
    if len(parts) == 1:
        return FACADE, None                      # the package itself
    head = parts[1]
    if head in LAYER_PACKAGES:
        return LAYER, head
    if head in FEATURE_PACKAGES:
        return FEATURE, None
    if head in BASE_MODULES:
        return BASE, None
    if head in MODEL_MODULES:
        return MODEL, None
    if head in APP_MODULES:
        return APP, None
    if head in UNLAYERED:
        return None, None                        # exempt until stage 2
    raise AssertionError(
        f"{module} has no layer. Put it in collect/, analyze/ or present/, or "
        f"add it to UNLAYERED with the stage that resolves it.")


def _resolve(importer, node):
    """Absolute dotted names an ImportFrom reaches; `from . import config` names the
    submodule, not the package."""
    if node.level == 0:
        module = node.module or ""
        return [module] if module.startswith(PACKAGE) else []
    parts = importer.split(".")
    # A package's __init__ IS its package; a module lives inside its parent.
    package = parts if _is_package(importer) else parts[:-1]
    base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
    if node.module:
        return [".".join(base + node.module.split("."))]
    return [".".join(base + [alias.name]) for alias in node.names]


_PACKAGE_DIRS = set()


def _is_package(module):
    return module in _PACKAGE_DIRS


def _walk():
    """(importer, imported) for every intra-package from-import."""
    for root, _dirs, files in os.walk(PACKAGE_ROOT):
        if "__init__.py" in files:
            _PACKAGE_DIRS.add(_module_name(os.path.join(root, "__init__.py")))
    for root, _dirs, files in os.walk(PACKAGE_ROOT):
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            importer = _module_name(path)
            with open(path) as handle:
                tree = ast.parse(handle.read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for target in _resolve(importer, node):
                        if target.startswith(PACKAGE):
                            yield importer, target


def _current_violations():
    found = set()
    for importer, imported in _walk():
        src_rank, src_peer = _tier(importer)
        dst_rank, dst_peer = _tier(imported)
        if src_rank is None or dst_rank is None:
            continue                              # one end is still unlayered
        if dst_rank > src_rank:
            found.add((importer, imported))
        elif src_rank == dst_rank == LAYER and src_peer != dst_peer:
            found.add((importer, imported))
    return found


class LayerDirectionTests(unittest.TestCase):
    maxDiff = None

    def test_no_new_upward_imports(self):
        """No import may point up the layers."""
        new = _current_violations() - KNOWN_VIOLATIONS
        self.assertEqual(sorted(new), [], self._explain(new))

    def test_the_baseline_only_shrinks(self):
        """A fixed violation must be struck off, or the list stops meaning
        anything and a regression can hide inside a stale entry."""
        stale = KNOWN_VIOLATIONS - _current_violations()
        self.assertEqual(
            sorted(stale), [],
            "these are listed as known violations but no longer occur - "
            "remove them from KNOWN_VIOLATIONS")

    def test_every_module_has_a_layer(self):
        """_tier() raises for an unplaced module; this is where that surfaces
        as a readable failure rather than an error inside another test."""
        for root, _dirs, files in os.walk(PACKAGE_ROOT):
            for name in files:
                if name.endswith(".py"):
                    _tier(_module_name(os.path.join(root, name)))

    def _explain(self, violations):
        lines = ["imports that run the wrong way:"]
        for importer, imported in sorted(violations):
            src, dst = _tier(importer), _tier(imported)
            lines.append(f"  {importer} -> {imported} "
                         f"({TIER_NAMES[src[0]]} -> {TIER_NAMES[dst[0]]})")
        lines.append("Move the code, or add it to KNOWN_VIOLATIONS with the "
                     "stage that resolves it.")
        return "\n".join(lines)


class NoAwsFromTheRendererTests(unittest.TestCase):
    """render.py must never reach collect/, even transitively: a re-render needs no
    credentials and makes no chargeable call."""

    maxDiff = None

    def _reachable_from(self, start):
        """Every intra-package module `start` imports, directly or not."""
        edges = {}
        for importer, imported in _walk():
            edges.setdefault(importer, set()).add(imported)
        seen, queue = set(), [f"{PACKAGE}.{start}"]
        while queue:
            module = queue.pop()
            for target in edges.get(module, ()):
                # Skip the facade: it re-exports everything, collectors included.
                if target == PACKAGE:
                    continue
                if target not in seen:
                    seen.add(target)
                    queue.append(target)
        return seen

    def test_the_renderer_cannot_reach_a_collector(self):
        reached = self._reachable_from("render")
        collectors = sorted(m for m in reached if m.startswith(f"{PACKAGE}.collect"))
        self.assertEqual(
            collectors, [],
            "render.py reaches the collection layer:\n  "
            + "\n  ".join(collectors)
            + "\nA render must not need AWS. Whatever this was for belongs in "
              "run.py, or in the snapshot.")

    def test_the_scanner_still_reaches_both(self):
        """run.py does reach collect/ and render, so the test above checks something."""
        reached = self._reachable_from("run")
        for expected in (f"{PACKAGE}.collect.resources", f"{PACKAGE}.render"):
            self.assertIn(expected, reached)


class TheCoreDoesNotKnowAboutTheWebAppTests(unittest.TestCase):
    """The package never imports backend/ or the web stack, so the CLI runs with nothing
    installed but boto3."""

    FORBIDDEN = {"backend", "fastapi", "pydantic", "uvicorn", "starlette", "sqlalchemy"}

    # The CLI scripts, outside the package, listed by hand.
    ENTRY_POINTS = ("aws_resource_audit.py", "aws_regenerate_report.py")

    def _package_files(self):
        for root, _dirs, names in os.walk(PACKAGE_ROOT):
            for name in sorted(names):
                if name.endswith(".py"):
                    yield os.path.join(root, name)

    def _entry_point_files(self):
        repo = os.path.dirname(PACKAGE_ROOT)
        for name in self.ENTRY_POINTS:
            path = os.path.join(repo, name)
            self.assertTrue(os.path.exists(path), f"{name} is missing")
            yield path

    def _offenders(self, paths):
        found = []
        for path in sorted(paths):
            with open(path) as handle:
                tree = ast.parse(handle.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    # level > 0 is a relative import - inside the package by
                    # definition, and covered by the tier rules above.
                    names = [node.module] if node.module and node.level == 0 else []
                else:
                    continue
                for name in names:
                    if name.split(".")[0] in self.FORBIDDEN:
                        found.append(
                            f"{os.path.basename(path)}:{node.lineno} imports {name}")
        return found

    def test_no_module_in_the_package_imports_the_web_stack(self):
        self.assertEqual(
            self._offenders(self._package_files()), [],
            "the core must not depend on the web application:\n  "
            + "\n  ".join(self._offenders(self._package_files()))
            + "\nboto3 is the only runtime dependency the CLI has. Whatever this "
              "was for belongs in backend/, which is free to import the package.")

    def test_no_entry_point_imports_the_web_stack(self):
        """The same rule for the two CLI scripts."""
        self.assertEqual(
            self._offenders(self._entry_point_files()), [],
            "the command line must run with nothing installed but boto3:\n  "
            + "\n  ".join(self._offenders(self._entry_point_files()))
            + "\nIf backend/ has a helper worth sharing, it belongs in the "
              "package, which both the CLI and the web app may import.")

    def test_the_guard_would_actually_catch_something(self):
        """Guard against globs that match nothing."""
        self.assertGreater(len(list(self._package_files())), 30)
        self.assertEqual(len(list(self._entry_point_files())), 2)


if __name__ == "__main__":
    unittest.main()
