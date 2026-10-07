"""Starting and stopping a scan, AWS profiles, and the saved configuration."""

import os
import threading
import time
import unittest
from . import _REPO  # noqa: F401  (import for the sys.path fixup side effect)
import aws_resource_audit as audit
from .api_base import ApiTestCase


class ScanControlTests(ApiTestCase):
    """The lock: one scan at a time."""

    def setUp(self):
        super().setUp()
        from app import scan_runner
        self.runner = scan_runner.runner
        # Every test here leaves the runner as it found it, since it is a
        # process-global singleton shared with every other test in the file.
        self.addCleanup(self._release)

    def _release(self):
        if self.runner._lock.locked():
            self.runner._lock.release()
        self.runner.state.reset()

    def test_a_second_scan_is_refused(self):
        """The whole interaction model: one scan at a time, and the refusal is
        explicit rather than a queue."""
        self.assertTrue(self.runner._lock.acquire(blocking=False))
        response = self.client.post("/api/scans", json={"regions": ["us-east-1"]})
        self.assertEqual(response.status_code, 409)
        self.assertIn("already running", response.json()["detail"])

    def test_a_failed_scan_releases_the_lock(self):
        """A scan that dies must release the lock and report "failed", not "running"."""
        def explode(*args, **kwargs):
            raise RuntimeError("collector exploded")

        from app import scan_runner
        original = scan_runner.run
        scan_runner.run = explode
        try:
            from aws_resource_audit.settings import Settings
            self.runner.start(object(), Settings(output_dir=self.data_dir))
            for _ in range(200):
                if not self.runner.is_running():
                    break
                time.sleep(0.01)
        finally:
            scan_runner.run = original

        self.assertFalse(self.runner.is_running(), "the lock was not released")
        status = self.runner.status()
        self.assertEqual(status["state"], "failed")
        self.assertIn("collector exploded", status["error"])

        # And the app can scan again.
        self.assertTrue(self.runner._lock.acquire(blocking=False))
        self.runner._lock.release()

    def test_an_audit_error_is_reported_as_the_users_problem(self):
        """No credentials: the core's message verbatim, and no traceback in the log tail."""
        def no_credentials(*args, **kwargs):
            raise audit.AuditError("Could not authenticate to AWS: no credentials")

        from app import scan_runner
        from aws_resource_audit.settings import Settings
        original = scan_runner.run
        scan_runner.run = no_credentials
        try:
            self.runner.start(object(), Settings(output_dir=self.data_dir))
            for _ in range(200):
                if not self.runner.is_running():
                    break
                time.sleep(0.01)
        finally:
            scan_runner.run = original

        status = self.runner.status()
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["error"],
                         "Could not authenticate to AWS: no credentials")
        self.assertNotIn("Traceback", "\n".join(status["log"]))

    def _wait_until_idle(self):
        for _ in range(400):
            if not self.runner.is_running():
                return
            time.sleep(0.01)
        self.fail("the scan thread did not end")

    def test_a_stop_ends_the_scan_as_cancelled_and_releases_the_lock(self):
        """The fake scan does what run() does: it asks
        should_stop between steps and raises ScanCancelled when told to."""
        from aws_resource_audit.settings import Settings
        from app import scan_runner
        started = threading.Event()

        def fake_scan(args, settings=None, progress=None, should_stop=None):
            started.set()
            for _ in range(400):
                if should_stop():
                    raise audit.ScanCancelled("The scan was stopped before it "
                                              "finished. Nothing was saved.")
                time.sleep(0.01)

        original = scan_runner.run
        scan_runner.run = fake_scan
        try:
            self.runner.start(object(), Settings(output_dir=self.data_dir))
            self.assertTrue(started.wait(2))
            response = self.client.post("/api/scans/cancel")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["stopping"])
            self._wait_until_idle()
        finally:
            scan_runner.run = original

        status = self.client.get("/api/scans/status").json()
        self.assertEqual(status["state"], "cancelled")
        self.assertFalse(status["stopping"])
        self.assertIsNone(status["error"])
        self.assertNotIn("Traceback", "\n".join(status["log"]))
        self.assertFalse(self.runner.is_running())

    def test_default_regions_are_empty_before_any_scan(self):
        body = self.client.get("/api/scans/default-regions").json()
        self.assertEqual((body["account"], body["regions"]), ("", []))

    def test_default_regions_are_the_saved_list_for_the_last_scans_account(self):
        self.write_snapshot()
        paths = audit.output_paths(audit.Settings(output_dir=self.data_dir))
        audit.record_active_regions(paths["active_regions"], "123456789012",
                                    ["us-east-1", "eu-west-1"], replace=True,
                                    observed_at=audit.now())
        audit.record_active_regions(paths["active_regions"], "999999999999",
                                    ["ap-south-1"], replace=True, observed_at=audit.now())
        body = self.client.get("/api/scans/default-regions").json()
        self.assertEqual(body["account"], "123456789012")
        self.assertEqual(body["regions"], ["eu-west-1", "us-east-1"])
        self.assertTrue(body["updated_at"])

    def test_a_stop_with_no_scan_running_is_not_an_error(self):
        response = self.client.post("/api/scans/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "idle")
        self.assertFalse(response.json()["stopping"])

    def test_a_stop_request_does_not_leak_into_the_next_scan(self):
        from aws_resource_audit.settings import Settings
        from app import scan_runner
        seen = []

        def fake_scan(args, settings=None, progress=None, should_stop=None):
            seen.append(should_stop())

        self.runner._stop.set()
        original = scan_runner.run
        scan_runner.run = fake_scan
        try:
            self.runner.start(object(), Settings(output_dir=self.data_dir))
            self._wait_until_idle()
        finally:
            scan_runner.run = original
        self.assertEqual(seen, [False])
        self.assertEqual(self.runner.status()["state"], "done")

    def test_progress_and_log_reach_the_status_endpoint(self):
        """Progress (steps) and the log tail are separate seams; either works alone."""
        from aws_resource_audit import console
        from aws_resource_audit.settings import Settings
        from app import scan_runner

        def fake_scan(args, settings=None, progress=None, should_stop=None):
            progress({"phase": "start", "step": 0, "total": 4})
            console.say("  EC2 instances                   ", end="")
            console.say("    3")
            progress({"phase": "collect", "region": "us-east-1",
                      "collector": "EC2 instances", "step": 1, "total": 4,
                      "count": 3})
            progress({"phase": "done", "step": 4, "total": 4, "rows": 3})

        original = scan_runner.run
        scan_runner.run = fake_scan
        try:
            self.runner.start(object(), Settings(output_dir=self.data_dir))
            for _ in range(200):
                if not self.runner.is_running():
                    break
                time.sleep(0.01)
        finally:
            scan_runner.run = original

        status = self.client.get("/api/scans/status").json()
        self.assertEqual(status["state"], "done")
        self.assertEqual(status["steps_total"], 4)
        self.assertEqual(status["steps_done"], 4)
        # The two say() calls are one terminal line - the label is written with
        # end="" and the count fills it in - so the tail must show one entry.
        self.assertIn("EC2 instances", "\n".join(status["log"]))
        self.assertEqual(len([l for l in status["log"] if "EC2 instances" in l]), 1)

    def test_the_request_schema_refuses_a_contradiction(self):
        """regions AND all_regions: get_regions would silently prefer one, and
        the user would get a 17-region scan they did not ask for."""
        response = self.client.post(
            "/api/scans", json={"regions": ["us-east-1"], "all_regions": True})
        self.assertEqual(response.status_code, 422)

    def test_tagging_cannot_be_requested(self):
        """The backend is read-only against AWS: tag_groups is rejected and pinned False."""
        response = self.client.post("/api/scans", json={"tag_groups": True})
        self.assertIn(response.status_code, (409, 422))
        from app.schemas import ScanRequest
        self.assertNotIn("tag_groups", ScanRequest.model_fields)
        self.assertFalse(ScanRequest().to_options().tag_groups)
        self.assertFalse(ScanRequest().to_options().confirm)


class ProfileTests(ApiTestCase):
    """The Scan panel's suggestions. Read off the credentials file, no AWS."""

    def _with_credentials(self, body):
        path = os.path.join(self.data_dir, "credentials")
        with open(path, "w") as f:
            f.write(body)
        previous = os.environ.get("AWS_SHARED_CREDENTIALS_FILE")
        os.environ["AWS_SHARED_CREDENTIALS_FILE"] = path
        self.addCleanup(
            lambda: os.environ.pop("AWS_SHARED_CREDENTIALS_FILE", None)
            if previous is None
            else os.environ.__setitem__("AWS_SHARED_CREDENTIALS_FILE", previous))
        return path

    def test_every_section_is_offered(self):
        path = self._with_credentials(
            "[default]\naws_access_key_id = A\naws_secret_access_key = s\n\n"
            "[work]\naws_access_key_id = B\naws_secret_access_key = t\n")
        body = self.client.get("/api/profiles").json()
        self.assertEqual(body["profiles"], ["default", "work"])
        self.assertEqual(body["path"], path)
        self.assertTrue(body["exists"])

    def test_a_missing_file_is_empty_not_an_error(self):
        """The first run, before anyone has written credentials. The box must
        still render rather than the panel failing to load."""
        os.environ["AWS_SHARED_CREDENTIALS_FILE"] = os.path.join(
            self.data_dir, "nope")
        self.addCleanup(os.environ.pop, "AWS_SHARED_CREDENTIALS_FILE", None)
        response = self.client.get("/api/profiles")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["profiles"], [])
        self.assertFalse(response.json()["exists"])

    def test_a_malformed_file_does_not_500(self):
        """A broken credentials file must not take the Scan panel down."""
        self._with_credentials("[default]\n[default]\nnot ini at all\n")
        response = self.client.get("/api/profiles")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["profiles"], [])


class ConfigTests(ApiTestCase):
    def test_an_unknown_key_is_rejected_and_nothing_is_written(self):
        """An unknown key is rejected, atomically."""
        self.write_config({"graph_in_html": False})
        response = self.client.request(
            "PUT", "/api/config", json={"graph_in_html": True, "nonsense": 1})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.read_config(), {"graph_in_html": False})

    def test_a_connection_type_matching_nothing_is_rejected_before_writing(self):
        """Connection-type KEYS are resolved before writing, so a bad key is never saved."""
        self.write_config({"graph_in_html": False})
        response = self.client.request(
            "PUT", "/api/config",
            json={"connection_types": {"no.such.type": "off"}})
        self.assertEqual(response.status_code, 400)
        self.assertIn("no.such.type", response.json()["detail"])
        self.assertEqual(self.read_config(), {"graph_in_html": False})

    def test_a_valid_config_is_saved_and_read_back(self):
        response = self.client.request(
            "PUT", "/api/config",
            json={"connection_types": {"amplify.apigateway.name-match": "off"},
                  "graph_all_nodes": True})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["graph_all_nodes"])
        states = {ct["id"]: ct["state"] for ct in body["connection_types"]}
        self.assertEqual(states["amplify.apigateway.name-match"], "off")
        self.assertEqual(self.read_config()["graph_all_nodes"], True)

    def test_assume_role_and_role_name_round_trip(self):
        """The Settings page can set assume_role and role_name, like audit_config.json."""
        response = self.client.request(
            "PUT", "/api/config",
            json={"assume_role": True, "role_name": "MyCustomAuditRole"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["assume_role"])
        self.assertEqual(body["role_name"], "MyCustomAuditRole")

        get_response = self.client.get("/api/config")
        self.assertTrue(get_response.json()["assume_role"])
        self.assertEqual(get_response.json()["role_name"], "MyCustomAuditRole")
        self.assertEqual(self.read_config()["role_name"], "MyCustomAuditRole")

    def test_the_error_message_names_the_file_the_user_knows(self):
        """Validation errors name the user's file, not the temp candidate."""
        response = self.client.request(
            "PUT", "/api/config", json={"snapshot_file": "scan.txt"})
        self.assertEqual(response.status_code, 400)
        detail = response.json()["detail"]
        self.assertIn("audit_config.json", detail)
        self.assertNotIn("candidate", detail)

    def test_output_dir_cannot_be_set(self):
        """output_dir cannot be set: the mount decides where files go."""
        from app.schemas import ConfigUpdate
        self.assertNotIn("output_dir", ConfigUpdate.model_fields)
        response = self.client.request(
            "PUT", "/api/config", json={"output_dir": "/tmp/elsewhere"})
        self.assertEqual(response.status_code, 422)

    def test_a_broken_config_on_disk_is_a_400_not_a_crash(self):
        """At the command line this exits the process. In a server it has to be
        an answer - and one naming the file, since that is the fix."""
        with open(os.path.join(self.data_dir, "audit_config.json"), "w") as f:
            f.write("{not json")
        response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 400)
        self.assertIn("audit_config.json", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
