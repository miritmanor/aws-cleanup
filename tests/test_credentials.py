"""credentials_help(): what a failed authentication tells you to do next. Each test
fixes the environment explicitly, so results do not depend on the developer's ~/.aws."""

import os
import unittest
from unittest import mock

from aws_resource_audit.collect import credentials as creds

CREDS_FILE = "/tmp/aws-audit-test/credentials"
CONFIG_FILE = "/tmp/aws-audit-test/config"


def _env(**extra):
    """An environment with every credential source explicitly absent."""
    base = {
        creds.CREDENTIALS_FILE_ENV: CREDS_FILE,
        creds.CONFIG_FILE_ENV: CONFIG_FILE,
    }
    base.update(extra)
    return mock.patch.dict(os.environ, base, clear=True)


def _help(profile=None, message="Unable to locate credentials", container=False,
          files=None):
    """The message, with the filesystem answers stubbed rather than created."""
    files = files or {}
    with _env(), \
         mock.patch.object(creds, "in_container", return_value=container), \
         mock.patch("os.path.exists", side_effect=lambda p: p in files), \
         mock.patch.object(creds, "profiles_in",
                           side_effect=lambda p, is_config=False: files.get(p, [])):
        return creds.credentials_help(profile, message)


class ClassifyTests(unittest.TestCase):

    def test_botocore_wordings_are_recognised(self):
        self.assertEqual(creds.classify("Unable to locate credentials"), creds.MISSING)
        self.assertEqual(creds.classify("The config profile (x) could not be found"),
                         creds.NO_PROFILE)
        self.assertEqual(
            creds.classify("An error occurred (ExpiredToken) when calling GetCallerIdentity"),
            creds.EXPIRED)

    def test_an_unrelated_failure_gets_no_credential_advice(self):
        """An access denial is not a credentials problem, and telling someone to
        write a credentials file is sending them to fix the wrong thing."""
        message = "An error occurred (AccessDenied) when calling GetCallerIdentity"
        self.assertEqual(creds.classify(message), creds.UNKNOWN)
        self.assertEqual(_help(message=message), "")


class MissingCredentialsTests(unittest.TestCase):

    def test_it_names_both_files_and_both_ways_to_fix_it(self):
        text = _help()
        self.assertIn(CREDS_FILE, text)
        self.assertIn(CONFIG_FILE, text)
        self.assertIn("does not exist", text)
        self.assertIn(creds.ENV_KEY, text)
        self.assertIn("aws configure --profile default", text)
        self.assertIn("iam/README.md", text)

    def test_it_reports_the_profile_that_was_actually_requested(self):
        text = _help(profile="aws-audit")
        self.assertIn('profile "aws-audit" (requested for this scan)', text)
        self.assertIn("aws configure --profile aws-audit", text)

    def test_it_reports_env_credentials_as_set_when_they_are(self):
        with _env(**{creds.ENV_KEY: "AKIA", creds.ENV_SECRET: "secret"}), \
             mock.patch.object(creds, "in_container", return_value=False):
            text = creds.credentials_help(None, "Unable to locate credentials")
        self.assertIn(f"{creds.ENV_KEY} / {creds.ENV_SECRET} in the environment: set", text)

    def test_on_a_normal_machine_there_is_no_container_advice(self):
        text = _help(container=False)
        self.assertNotIn("container", text)
        self.assertNotIn("docker-compose", text)

    def test_a_secret_is_not_told_to_write_into_the_read_only_path(self):
        """Never advise writing to /run/secrets, the one file a container cannot write."""
        with mock.patch.dict(os.environ,
                             {creds.CREDENTIALS_FILE_ENV: "/run/secrets/aws_credentials",
                              creds.CONFIG_FILE_ENV: CONFIG_FILE}, clear=True), \
             mock.patch.object(creds, "in_container", return_value=True):
            text = creds.credentials_help(None, "Unable to locate credentials")
        self.assertNotIn("into /run/secrets/aws_credentials", text)
        self.assertNotIn("aws configure", text)
        self.assertIn("Fix it on the HOST", text)

    def test_a_docker_secret_is_named_as_one_and_points_at_the_host(self):
        """A Docker secret is fixed on the host, and the message says so."""
        with mock.patch.dict(os.environ,
                             {creds.CREDENTIALS_FILE_ENV: "/run/secrets/aws_credentials",
                              creds.CONFIG_FILE_ENV: CONFIG_FILE}, clear=True), \
             mock.patch.object(creds, "in_container", return_value=True):
            text = creds.credentials_help(None, "Unable to locate credentials")
        self.assertIn("/run/secrets/aws_credentials is a Docker secret", text)
        self.assertIn("AUDIT_AWS_CREDENTIALS_FILE", text)
        self.assertIn("~/.aws-audit/credentials", text)
        self.assertIn("docker-compose up", text)

    def test_a_secret_is_recognised_by_its_path_not_by_the_container_check(self):
        """A secret path gets the secret wording even if the container check says no."""
        with mock.patch.dict(os.environ,
                             {creds.CREDENTIALS_FILE_ENV: "/run/secrets/aws_credentials",
                              creds.CONFIG_FILE_ENV: CONFIG_FILE}, clear=True), \
             mock.patch.object(creds, "in_container", return_value=False):
            text = creds.credentials_help(None, "Unable to locate credentials")
        self.assertIn("Docker secret", text)

    def test_in_a_plain_container_it_says_the_file_belongs_on_the_host(self):
        text = _help(container=True)
        self.assertIn("running in a container", text)
        self.assertIn("mounted from the host read-only", text)
        self.assertIn("Fix it on the HOST", text)


class UnknownProfileTests(unittest.TestCase):

    def test_it_lists_the_profiles_that_do_exist(self):
        text = _help(profile="typo",
                     message="The config profile (typo) could not be found",
                     files={CREDS_FILE: ["aws-audit", "personal"]})
        self.assertIn('The profile "typo" is not defined.', text)
        self.assertIn("aws-audit, personal", text)

    def test_it_says_so_when_there_are_none(self):
        text = _help(profile="typo",
                     message="The config profile (typo) could not be found")
        self.assertIn("No profiles are defined", text)


class ExpiredCredentialsTests(unittest.TestCase):

    def test_it_asks_for_a_refresh_not_for_a_new_file(self):
        text = _help(profile="aws-audit",
                     message="(ExpiredToken) The security token ... is expired")
        self.assertIn('Credentials were found (profile "aws-audit")', text)
        self.assertIn("aws sso login", text)
        self.assertIn(creds.ENV_TOKEN, text)
        self.assertNotIn("No AWS credentials were found", text)


class ProfileParsingTests(unittest.TestCase):
    """profiles_in() reads two files that spell profiles differently, and must
    not turn either one's non-profile sections into offers of a profile name."""

    def _write(self, name, body):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return path

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def test_credentials_file_sections_are_profile_names(self):
        path = self._write("credentials", "[default]\nk = v\n\n[aws-audit]\nk = v\n")
        self.assertEqual(creds.profiles_in(path), ["aws-audit", "default"])

    def test_config_file_drops_the_prefix_and_ignores_sso_sessions(self):
        path = self._write(
            "config",
            "[default]\nregion = us-east-1\n\n[profile aws-audit]\nregion = us-east-1\n"
            "\n[sso-session corp]\nsso_start_url = https://example\n")
        self.assertEqual(creds.profiles_in(path, is_config=True),
                         ["aws-audit", "default"])

    def test_an_unreadable_file_lists_nothing_rather_than_raising(self):
        self.assertEqual(creds.profiles_in(os.path.join(self.dir, "absent")), [])
        path = self._write("broken", "this is not ini\n[[[\n")
        self.assertEqual(creds.profiles_in(path), [])


if __name__ == "__main__":
    unittest.main()
