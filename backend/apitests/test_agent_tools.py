"""The agent's tools and run_turn against a stub chat model: tool calls, masking and
resource_keys. The model's wording is not testable this way."""

import asyncio
import json
import unittest

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from aws_resource_audit.rows import member_key

from . import _REPO  # noqa: F401  (sys.path fixup)
from .api_base import ApiTestCase


class ScriptedToolCallingModel(BaseChatModel):
    """A minimal fake that streams `tool_calls` (GenericFakeChatModel does not), carrying
    scripted AIMessages through both the streaming and non-streaming paths."""

    responses: list
    index: int = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        msg = self.responses[self.index]
        self.index += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        msg = self.responses[self.index]
        self.index += 1
        if msg.tool_calls:
            for tc in msg.tool_calls:
                yield ChatGenerationChunk(message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[{"name": tc["name"], "args": json.dumps(tc["args"]),
                                       "id": tc["id"], "index": 0}],
                ))
        else:
            for word in msg.content.split(" "):
                yield ChatGenerationChunk(message=AIMessageChunk(content=word + " "))

    @property
    def _llm_type(self):
        return "scripted-tool-calling-fake"


def _tool_call_response(name, args, call_id="call1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


class QueryResourcesToolTests(ApiTestCase):
    """The tool directly, no agent loop involved - filters and masking."""

    def setUp(self):
        super().setUp()
        self.rows = self.write_snapshot()
        from app import stores
        from app.agent.tools import make_query_resources_tool
        self.tool = make_query_resources_tool(stores.snapshot_store())

    def test_filters_by_service(self):
        results = self.tool.invoke({"service": "IAMPolicy"})
        self.assertTrue(results)
        self.assertTrue(all(r["service"] == "IAMPolicy" for r in results))

    def test_filters_by_stale_flag(self):
        stale = self.tool.invoke({"stale": True})
        active = self.tool.invoke({"stale": False})
        self.assertTrue(all(r["flag"].startswith("STALE") for r in stale))
        self.assertTrue(all(not r["flag"].startswith("STALE") for r in active))

    def test_returns_the_reasoning_fields_not_just_identifiers(self):
        results = self.tool.invoke({})
        self.assertTrue(results)
        for field in ("flag", "tier", "why_tier", "why_grouped", "notes", "connections"):
            self.assertIn(field, results[0])

    def test_no_raw_account_id_survives_in_any_result(self):
        results = self.tool.invoke({})
        blob = json.dumps(results)
        self.assertNotIn("111122223333", blob)
        self.assertNotIn("123456789012", blob)

    def test_unmatched_filter_returns_empty_not_an_error(self):
        self.assertEqual(self.tool.invoke({"service": "NoSuchService"}), [])


class RunTurnToolCallTests(ApiTestCase):
    """The whole loop with a stub model that calls query_resources: checks the
    decision reached the tool with the right arguments."""

    def setUp(self):
        super().setUp()
        self.rows = self.write_snapshot()
        from app.agent import agent_loop as loop_mod
        self.loop_mod = loop_mod
        loop_mod.reset()
        self.addCleanup(loop_mod.reset)
        # A stand-in index: the real one loads an embedding model from the internet.
        real_get_index = loop_mod.index.get_index
        loop_mod.index.get_index = lambda: object()
        self.addCleanup(setattr, loop_mod.index, "get_index", real_get_index)

    def _run(self, model, message, resource_keys=(), session_id="s1"):
        self.loop_mod.get_chat_model = lambda settings: model

        async def collect():
            return "".join([tok async for tok in
                             self.loop_mod.run_turn(session_id, message, list(resource_keys))])

        return asyncio.run(collect())

    def test_stubbed_tool_call_reaches_query_resources_with_expected_args(self):
        # Patched on loop_mod: agent_loop imported the name at its own import time.
        calls = []
        original = self.loop_mod.make_query_resources_tool

        def spying_factory(store):
            real_tool = original(store)
            original_func = real_tool.func

            def spy(**kwargs):
                calls.append(kwargs)
                return original_func(**kwargs)
            real_tool.func = spy
            return real_tool

        self.loop_mod.make_query_resources_tool = spying_factory
        self.addCleanup(setattr, self.loop_mod, "make_query_resources_tool", original)

        model = ScriptedToolCallingModel(responses=[
            _tool_call_response("query_resources", {"stale": True}),
            AIMessage(content="Here are the stale resources."),
        ])
        text = self._run(model, "what's stale?")
        self.assertIn("stale resources", text)
        # The tool schema fills every parameter (None); "stale" is the one set.
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["stale"], True)
        other_filters = {k: v for k, v in calls[0].items() if k != "stale"}
        self.assertTrue(all(v is None for v in other_filters.values()), calls[0])

    def test_resource_keys_resolve_to_masked_context(self):
        target = next(r for r in self.rows if "111122223333" in r["resource_id"])
        key = member_key(target)

        class EchoModel(ScriptedToolCallingModel):
            def _stream(self, messages, stop=None, run_manager=None, **kwargs):
                last_human = [m for m in messages if m.type == "human"][-1].content
                for word in last_human.split(" "):
                    yield ChatGenerationChunk(message=AIMessageChunk(content=word + " "))

        text = self._run(EchoModel(responses=[]), "what is this?", resource_keys=[key])
        self.assertIn("Currently selected resource(s):", text)
        self.assertNotIn("111122223333", text)

    def test_unknown_resource_key_is_dropped_not_an_error(self):
        model = ScriptedToolCallingModel(responses=[AIMessage(content="sure, ask away")])
        text = self._run(model, "hello", resource_keys=["NoSuchService:us-east-1:missing"])
        self.assertIn("sure, ask away", text)

    def test_multiple_sessions_do_not_share_history(self):
        """Two sessions on one agent: B's turn must not see A's messages."""

        class MessageCountingModel(ScriptedToolCallingModel):
            def _stream(self, messages, stop=None, run_manager=None, **kwargs):
                yield ChatGenerationChunk(
                    message=AIMessageChunk(content=f"saw {len(messages)} messages"))

        model = MessageCountingModel(responses=[])
        text_a = self._run(model, "hi from A", session_id="session-a")
        text_b = self._run(model, "hi from B", session_id="session-b")

        self.assertIn("saw 2 messages", text_a)  # system prompt + A's own message
        self.assertIn("saw 2 messages", text_b)  # system prompt + B's own - not A's too


if __name__ == "__main__":
    unittest.main()


class StreamErrorFrameTests(ApiTestCase):
    """What the endpoint sends when run_turn raises: the 200 is already sent, so the
    failure must appear in the body."""

    def setUp(self):
        super().setUp()
        from app.api import agent as agent_route
        self.agent_route = agent_route
        self.original = agent_route.agent_loop.run_turn
        self.addCleanup(setattr, agent_route.agent_loop, "run_turn", self.original)

    def _stream_raising(self, exc, tokens=()):
        async def fake_run_turn(session_id, message, resource_keys):
            for tok in tokens:
                yield tok
            raise exc
        self.agent_route.agent_loop.run_turn = fake_run_turn
        response = self.client.post("/api/agent/query",
                                    json={"session_id": "s1", "message": "hi"})
        return response, [json.loads(line[len("data: "):])
                          for line in response.text.split("\n\n") if line.strip()]

    def test_audit_error_message_reaches_the_client_verbatim(self):
        from aws_resource_audit.errors import AuditError
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
        self.agent_route.agent_loop.run_turn = fake_run_turn
        response = self.client.post("/api/agent/query",
                                    json={"session_id": "s1", "message": "hi"})
        frames = [json.loads(line[len("data: "):])
                  for line in response.text.split("\n\n") if line.strip()]
        self.assertEqual(frames, [{"token": "line one\n\nline two"}])


class AgentAvailabilityTests(ApiTestCase):
    """GET /api/agent/status, and the key file behind it. No client is ever
    constructed here - that is the whole point of the endpoint."""

    def setUp(self):
        super().setUp()
        import os
        from app.agent import llm
        self.llm = llm
        self.key_file = os.path.join(self.data_dir, "llm_api_key")
        self.addCleanup(os.environ.pop, llm._API_KEY_FILE_ENV, None)
        os.environ[llm._API_KEY_FILE_ENV] = self.key_file

    def _write_key(self, text):
        with open(self.key_file, "w") as f:
            f.write(text)

    def test_no_key_file_is_unavailable_with_a_reason(self):
        body = self.client.get("/api/agent/status").json()
        self.assertFalse(body["available"])
        self.assertIn("API key", body["reason"])

    def test_an_empty_key_file_reads_as_no_key(self):
        """`touch` is the documented way to start the container without a
        key, so an empty file must not read as one."""
        self._write_key("   \n")
        self.assertFalse(self.client.get("/api/agent/status").json()["available"])

    def test_a_key_makes_it_available(self):
        self._write_key("sk-test-not-a-real-key\n")
        body = self.client.get("/api/agent/status").json()
        self.assertTrue(body["available"])
        self.assertEqual(body["reason"], "")

    def test_a_trailing_newline_is_not_part_of_the_key(self):
        self._write_key("sk-test-not-a-real-key\n")
        self.assertEqual(self.llm.read_api_key(), "sk-test-not-a-real-key")

    def test_bedrock_needs_no_key_of_its_own(self):
        import json as _json
        import os
        with open(os.path.join(self.data_dir, "agent_config.json"), "w") as f:
            _json.dump({"provider": "bedrock", "model": "anthropic.claude-v2"}, f)
        self.assertTrue(self.client.get("/api/agent/status").json()["available"])

    def test_a_broken_agent_config_is_unavailable_not_a_400(self):
        """Called on page load: a config error must hide the agent, not greet
        someone with a red banner about a feature they were not using."""
        import os
        with open(os.path.join(self.data_dir, "agent_config.json"), "w") as f:
            f.write("{not json")
        response = self.client.get("/api/agent/status")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["available"])

    def test_get_chat_model_without_a_key_raises_audit_error(self):
        """Not the provider SDK's OpenAIError: the user must fix it, and the message says how."""
        from aws_resource_audit.errors import AuditError
        from app.agent.settings import AgentSettings
        with self.assertRaises(AuditError):
            self.llm.get_chat_model(AgentSettings())
