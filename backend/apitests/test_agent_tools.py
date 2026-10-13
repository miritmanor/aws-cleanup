"""The agent's tools and run_turn against a stub chat model: tool calls, pseudonyms and
resource_keys. The model's wording is not testable this way."""

import asyncio
import json
import unittest

from . import _REPO  # noqa: F401  (sys.path fixup)
from app.agent import install

# Before the langchain imports below: an image built with AUDIT_AGENT_PROVIDER=none has none.
if install.not_installed_reason():
    raise unittest.SkipTest(install.not_installed_reason())

from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage, AIMessageChunk  # noqa: E402
from langchain_core.outputs import (  # noqa: E402
    ChatGeneration, ChatGenerationChunk, ChatResult)

from aws_resource_audit.rows import member_key  # noqa: E402

from .api_base import ApiTestCase  # noqa: E402


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

    def test_a_resource_id_token_finds_its_row(self):
        token = self.tool.invoke({})[0]["resource_id"]
        found = self.tool.invoke({"resource_id": token})
        self.assertEqual([r["resource_id"] for r in found], [token])

    def test_unmatched_filter_returns_empty_not_an_error(self):
        self.assertEqual(self.tool.invoke({"service": "NoSuchService"}), [])


class SearchDocumentsToolTests(ApiTestCase):
    """The document tool with a stand-in index: searched with real values, returned masked."""

    def setUp(self):
        super().setUp()
        self.rows = self.write_snapshot()
        from app import stores
        from app.agent.tools import make_search_documents_tool
        self.queries = []
        name = self.rows[0]["resource_id"]
        self.name = name
        queries = self.queries

        class FakeIndex:
            def similarity_search(self, query, k):
                queries.append(query)
                from langchain_core.documents import Document
                return [Document(page_content=f"{name} belongs to payments, see 10.0.4.17",
                                 metadata={"source": "runbook.md"})]

        self.store = stores.snapshot_store()
        self.tool = make_search_documents_tool(FakeIndex(), self.store)

    def test_hits_are_masked_and_the_query_is_revealed_locally(self):
        pseudonymizer, _rows = self.store.get_masked_view()
        token = pseudonymizer.mask_text(self.name)
        hits = self.tool.invoke({"query": f"what is {token}?"})
        self.assertEqual(self.queries, [f"what is {self.name}?"])
        text = json.dumps(hits)
        self.assertNotIn(self.name, text)
        self.assertNotIn("10.0.4.17", text)


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
        """What the model sees is masked; its echo comes back to the user revealed."""
        target = next(r for r in self.rows if "111122223333" in r["resource_id"])
        key = member_key(target)
        seen = []

        class EchoModel(ScriptedToolCallingModel):
            def _stream(self, messages, stop=None, run_manager=None, **kwargs):
                last_human = [m for m in messages if m.type == "human"][-1].content
                seen.append(last_human)
                for word in last_human.split(" "):
                    yield ChatGenerationChunk(message=AIMessageChunk(content=word + " "))

        text = self._run(EchoModel(responses=[]), "what is this?", resource_keys=[key])
        self.assertIn("Currently selected resource(s):", seen[0])
        for secret in ("111122223333", target["resource_id"], target["name"]):
            self.assertNotIn(secret, seen[0])
        self.assertIn(target["resource_id"], text)

    def test_the_question_is_masked_and_the_reply_revealed(self):
        name = self.rows[0]["resource_id"]
        seen = []

        class EchoModel(ScriptedToolCallingModel):
            def _stream(self, messages, stop=None, run_manager=None, **kwargs):
                last_human = [m for m in messages if m.type == "human"][-1].content
                seen.append(last_human)
                for word in last_human.split(" "):
                    yield ChatGenerationChunk(message=AIMessageChunk(content=word + " "))

        text = self._run(EchoModel(responses=[]), f"why is {name} stale?")
        self.assertNotIn(name, seen[0])
        self.assertEqual(text.strip(), f"why is {name} stale?")

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

    def test_content_blocks_stream_as_their_text(self):
        """OpenAI's Responses API streams content as a list of blocks, not a str."""

        class BlockStreamingModel(ScriptedToolCallingModel):
            def _stream(self, messages, stop=None, run_manager=None, **kwargs):
                yield ChatGenerationChunk(message=AIMessageChunk(
                    content=[{"type": "reasoning", "summary": [], "index": 0}]))
                for word in ("Two ", "stale ", "buckets."):
                    yield ChatGenerationChunk(message=AIMessageChunk(
                        content=[{"type": "text", "text": word, "index": 1}]))

        text = self._run(BlockStreamingModel(responses=[]), "what's stale?")
        self.assertEqual(text, "Two stale buckets.")


if __name__ == "__main__":
    unittest.main()
