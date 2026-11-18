import tempfile
import unittest
from pathlib import Path

import requests

from agent.decide import Decider
from agent.journal import Journal
from agent.llm import LLMFatal, LLMUnavailable, OpenAICompatible, extract_json
from agent.run import RunLog, process_ticket
from tests.fakes import FakeClaude, FakeResp, FakeSession
from tests.test_decide import FAQ, TICKET

TOOL = {"name": "t", "input_schema": {"type": "object", "properties": {"a": {"type": "string"}}}}


def chat(content, usage=(10, 5)):
    return FakeResp(200, {"choices": [{"message": {"content": content}}],
                          "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}})


def llm(script):
    return OpenAICompatible("m", "http://x/v1", "k", min_interval=0, session=FakeSession(script),
                            sleep=lambda s: None, clock=lambda: 0.0)


class ExtractJsonTests(unittest.TestCase):
    def test_plain_fenced_and_chatty(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(extract_json('Sure! Here it is: {"a": "x"} Hope that helps'), {"a": "x"})

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            extract_json("no json here")


class OpenAICompatibleTests(unittest.TestCase):
    def test_success_and_token_usage(self):
        m = llm([chat('{"a": "ok"}', usage=(100, 20))])
        self.assertEqual(m.call_tool("sys", "user", TOOL), {"a": "ok"})
        self.assertEqual((m.usage.input_tokens, m.usage.output_tokens), (100, 20))

    def test_rate_limit_is_retried(self):
        m = llm([FakeResp(429, headers={"Retry-After": "1"}), chat('{"a": "ok"}')])
        self.assertEqual(m.call_tool("s", "u", TOOL), {"a": "ok"})

    def test_bad_key_is_fatal_not_retried(self):
        m = llm([FakeResp(401, {"error": "bad key"})])
        with self.assertRaises(LLMFatal):
            m.call_tool("s", "u", TOOL)
        self.assertEqual(len(m.session.calls), 1)

    def test_persistent_outage_is_unavailable(self):
        m = llm([FakeResp(503)] * 6)
        with self.assertRaises(LLMUnavailable):
            m.call_tool("s", "u", TOOL)

    def test_network_error_is_retried(self):
        m = llm([requests.ConnectionError(), chat('{"a": "ok"}')])
        self.assertEqual(m.call_tool("s", "u", TOOL), {"a": "ok"})

    def test_invalid_json_retried_once_then_valueerror(self):
        m = llm([chat("nope"), chat('{"a": "ok"}')])
        self.assertEqual(m.call_tool("s", "u", TOOL), {"a": "ok"})
        m = llm([chat("nope"), chat("still nope")])
        with self.assertRaises(ValueError):
            m.call_tool("s", "u", TOOL)


class PipelineSafetyTests(unittest.TestCase):
    def test_model_outage_leaves_ticket_open_instead_of_handing_off(self):
        tmp = Path(tempfile.mkdtemp())
        sent = []

        class Client:
            def reply(self, *a): sent.append(a)
            handoff = close = reply

        decider = Decider(FakeClaude([LLMUnavailable("quota")]), FAQ)
        out = process_ticket(TICKET, decider=decider, client=Client(), journal=Journal(tmp / "j.jsonl"),
                             log=RunLog(tmp / "logs"), dry_run=False, version="t")
        self.assertEqual(out, "skipped_model_unavailable")
        self.assertEqual(sent, [])  # nothing posted

    def test_fatal_model_error_aborts(self):
        decider = Decider(FakeClaude([LLMFatal("bad key")]), FAQ)
        with self.assertRaises(LLMFatal):
            decider.decide(TICKET)


if __name__ == "__main__":
    unittest.main()
