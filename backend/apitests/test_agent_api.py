"""The agent's endpoints without the agent's packages: status, error frames, and an image
built with AUDIT_AGENT_PROVIDER=none. Runs in every image, whichever provider it holds."""

import json
import os
import unittest

from aws_resource_audit.errors import AuditError

from . import _REPO  # noqa: F401  (sys.path fixup)
from .api_base import ApiTestCase


class _ProviderTestCase(ApiTestCase):
    """Pins the build-time provider and pretends its packages are installed, so a
    result never depends on which image the tests run in."""

    def setUp(self):
        super().setUp()
        from app.agent import install
        self.install = install
        self._set_provider("openai")
        original = install._is_installed
        install._is_installed = lambda module: True
        self.addCleanup(setattr, install, "_is_installed", original)

    def _set_provider(self, name):
        previous = os.environ.get(self.install.PROVIDER_ENV)
        os.environ[self.install.PROVIDER_ENV] = name
        if previous is None:
            self.addCleanup(os.environ.pop, self.install.PROVIDER_ENV, None)
        else:
            self.addCleanup(os.environ.__setitem__, self.install.PROVIDER_ENV, previous)

    def _write_agent_config(self, data):
        with open(os.path.join(self.data_dir, "agent_config.json"), "w") as f:
            json.dump(data, f)

    def _frames(self, response):
        return [json.loads(line[len("data: "):])
                for line in response.text.split("\n\n") if line.strip()]


class StreamErrorFrameTests(_ProviderTestCase):
    """What the endpoint sends when run_turn raises: the 200 is already sent, so the
    failure must appear in the body."""

    def setUp(self):
        super().setUp()
        from app.api import agent as agent_route
        self.agent_route = agent_route
        self.addCleanup(setattr, agent_route, "_run_turn", agent_route._run_turn)

    def _stream_raising(self, exc, tokens=()):
        async def fake_run_turn(session_id, message, resource_keys):
            for tok in tokens:
                yield tok
            raise exc
        self.agent_route._run_turn = fake_run_turn
        response = self.client.post("/api/agent/query",
                                    json={"session_id": "s1", "message": "hi"})
        return response, self._frames(response)

    def test_audit_error_message_reaches_the_client_verbatim(self):
        response, frames = self._stream_raising(AuditError("Set OPENAI_API_KEY first."))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(frames[-1], {"error": "Set OPENAI_API_KEY first."})

    def test_unexpected_error_is_generic_and_leaks_nothing(self):
        """A bug gets a sentence, not an exception class name."""
        from app.api.agent import GENERIC_ERROR
        _response, frames = self._stream_raising(RuntimeError("boom"))
        self.assertEqual(frames[-1], {"error": GENERIC_ERROR})
        self.assertNotIn("boom", json.dumps(frames))
        self.assertNotIn("RuntimeError", json.dumps(frames))

    def test_tokens_already_streamed_survive_a_later_failure(self):
        from app.api.agent import GENERIC_ERROR
        _response, frames = self._stream_raising(RuntimeError("boom"),
                                                 tokens=["par", "tial"])
        self.assertEqual(frames[:2], [{"token": "par"}, {"token": "tial"}])
        self.assertEqual(frames[-1], {"error": GENERIC_ERROR})

    def test_a_token_containing_a_newline_stays_one_frame(self):
        async def fake_run_turn(session_id, message, resource_keys):
            yield "line one\n\nline two"
        self.agent_route._run_turn = fake_run_turn
        response = self.client.post("/api/agent/query",
                                    json={"session_id": "s1", "message": "hi"})
        self.assertEqual(self._frames(response), [{"token": "line one\n\nline two"}])


class AgentAvailabilityTests(_ProviderTestCase):
    """GET /api/agent/status, and the key file behind it. No client is ever
    constructed here - that is the whole point of the endpoint."""

    def setUp(self):
        super().setUp()
        from app.agent import llm
        self.llm = llm
        self.key_file = os.path.join(self.data_dir, "llm_api_key")
        self.addCleanup(os.environ.pop, llm._API_KEY_FILE_ENV, None)
        os.environ[llm._API_KEY_FILE_ENV] = self.key_file

    def _write_key(self, text):
        with open(self.key_file, "w") as f:
            f.write(text)

    def _status(self):
        return self.client.get("/api/agent/status").json()

    def test_no_key_file_is_unavailable_with_a_reason(self):
        body = self._status()
        self.assertFalse(body["available"])
        self.assertIn("API key", body["reason"])

    def test_an_empty_key_file_reads_as_no_key(self):
        """`touch` is the documented way to start the container without a
        key, so an empty file must not read as one."""
        self._write_key("   \n")
        self.assertFalse(self._status()["available"])

    def test_a_key_makes_it_available(self):
        self._write_key("sk-test-not-a-real-key\n")
        body = self._status()
        self.assertTrue(body["available"])
        self.assertEqual(body["reason"], "")

    def test_a_trailing_newline_is_not_part_of_the_key(self):
        self._write_key("sk-test-not-a-real-key\n")
        self.assertEqual(self.llm.read_api_key(), "sk-test-not-a-real-key")

    def test_bedrock_needs_no_key_of_its_own(self):
        self._set_provider("bedrock")
        self._write_agent_config({"model": "anthropic.claude-v2"})
        self.assertTrue(self._status()["available"])

    def test_a_broken_agent_config_is_unavailable_not_a_400(self):
        """Called on page load: a config error must hide the agent, not greet
        someone with a red banner about a feature they were not using."""
        with open(os.path.join(self.data_dir, "agent_config.json"), "w") as f:
            f.write("{not json")
        response = self.client.get("/api/agent/status")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["available"])

    def test_get_chat_model_without_a_key_raises_audit_error(self):
        """Not the provider SDK's OpenAIError: the user must fix it, and the message says how."""
        from app.agent.settings import AgentSettings
        with self.assertRaises(AuditError):
            self.llm.get_chat_model(AgentSettings())


class BuildTimeProviderTests(_ProviderTestCase):
    """AUDIT_AGENT_PROVIDER: what the image was built with, and so the only provider
    it can run. agent_config.json no longer names one."""

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.data_dir, "llm_api_key"), "w") as f:
            f.write("sk-test-not-a-real-key")
        from app.agent import llm
        self.addCleanup(os.environ.pop, llm._API_KEY_FILE_ENV, None)
        os.environ[llm._API_KEY_FILE_ENV] = os.path.join(self.data_dir, "llm_api_key")

    def test_an_image_built_without_the_agent_says_so(self):
        self._set_provider("none")
        body = self.client.get("/api/agent/status").json()
        self.assertFalse(body["available"])
        self.assertIn("AUDIT_AGENT_PROVIDER=none", body["reason"])

    def test_no_provider_named_means_the_agent_is_off(self):
        os.environ.pop(self.install.PROVIDER_ENV)
        body = self.client.get("/api/agent/status").json()
        self.assertFalse(body["available"])
        self.assertIn("the default", body["reason"])

    def test_a_question_to_an_image_without_the_agent_is_an_error_frame(self):
        """Not a 500 from importing langchain: the endpoint refuses before agent_loop loads."""
        self._set_provider("none")
        response = self.client.post("/api/agent/query",
                                    json={"session_id": "s1", "message": "hi"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("The agent is off", self._frames(response)[-1]["error"])

    def test_a_reindex_in_an_image_without_the_agent_is_a_400(self):
        self._set_provider("none")
        response = self.client.post("/api/agent/documents/reindex")
        self.assertEqual(response.status_code, 400)
        self.assertIn("The agent is off", response.json()["detail"])

    def test_a_missing_provider_package_is_named(self):
        self.install._is_installed = lambda module: module != "langchain_openai"
        body = self.client.get("/api/agent/status").json()
        self.assertFalse(body["available"])
        self.assertIn("langchain_openai", body["reason"])

    def test_an_unknown_provider_lists_the_known_ones(self):
        self._set_provider("mistral")
        reason = self.client.get("/api/agent/status").json()["reason"]
        self.assertIn("'mistral'", reason)
        self.assertIn("bedrock", reason)

    def test_an_old_config_naming_a_provider_says_where_it_went(self):
        self._write_agent_config({"provider": "anthropic"})
        body = self.client.get("/api/agent/status").json()
        self.assertFalse(body["available"])
        self.assertIn("AUDIT_AGENT_PROVIDER", body["reason"])

    def test_an_old_config_naming_an_embedding_model_is_refused(self):
        from app.agent.settings import load_agent_settings
        path = os.path.join(self.data_dir, "agent_config.json")
        self._write_agent_config({"embedding_model": "some/other-model"})
        with self.assertRaises(AuditError) as caught:
            load_agent_settings(path)
        self.assertIn("embedding_model", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
