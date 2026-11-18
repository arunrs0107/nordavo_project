import unittest

import requests

from agent.helpdesk import AlreadyHandled, ApiError, Helpdesk, HelpdeskError
from tests.fakes import FakeResp, FakeSession


def make(script, sleeps=None):
    sleeps = sleeps if sleeps is not None else []
    return Helpdesk("http://x", "key", "me@example.com", session=FakeSession(script),
                    sleep=sleeps.append, clock=lambda: 0.0), sleeps


class HelpdeskTests(unittest.TestCase):
    def test_503_is_retried_then_succeeds(self):
        hd, sleeps = make([FakeResp(503), FakeResp(503), FakeResp(200, {"ok": 1})])
        self.assertEqual(hd.request("GET", "/status"), {"ok": 1})
        self.assertEqual(len(hd.session.calls), 3)

    def test_429_waits_retry_after(self):
        hd, sleeps = make([FakeResp(429, headers={"Retry-After": "2"}), FakeResp(200, {"ok": 1})])
        hd.request("GET", "/status")
        self.assertTrue(any(s >= 2 for s in sleeps))

    def test_409_is_never_retried(self):
        hd, _ = make([FakeResp(409, {"error": "already_handled"})])
        with self.assertRaises(AlreadyHandled) as ctx:
            hd.request("POST", "/tickets/T1/reply", json={})
        self.assertFalse(ctx.exception.ambiguous)
        self.assertEqual(len(hd.session.calls), 1)

    def test_timeout_then_409_is_flagged_ambiguous(self):
        hd, _ = make([requests.Timeout(), FakeResp(409)])
        with self.assertRaises(AlreadyHandled) as ctx:
            hd.request("POST", "/tickets/T1/reply", json={})
        self.assertTrue(ctx.exception.ambiguous)

    def test_422_is_not_retried(self):
        hd, _ = make([FakeResp(422, {"error": "validation_failed", "problems": ["x"]})])
        with self.assertRaises(ApiError):
            hd.request("POST", "/tickets/T1/reply", json={})
        self.assertEqual(len(hd.session.calls), 1)

    def test_gives_up_after_max_attempts(self):
        hd, _ = make([FakeResp(503)] * 8)
        with self.assertRaises(HelpdeskError):
            hd.request("GET", "/status")

    def test_pagination_follows_cursor(self):
        hd, _ = make([
            FakeResp(200, {"tickets": [{"id": "T1"}, {"id": "T2"}], "next_cursor": "T2"}),
            FakeResp(503),
            FakeResp(200, {"tickets": [{"id": "T3"}], "next_cursor": None}),
        ])
        self.assertEqual([t["id"] for t in hd.list_open()], ["T1", "T2", "T3"])

    def test_self_throttle_stays_under_5_per_second(self):
        sleeps = []
        hd = Helpdesk("http://x", "k", "e", session=FakeSession([FakeResp(200, {})] * 3),
                      sleep=sleeps.append, clock=lambda: 0.0)
        for _ in range(3):
            hd.request("GET", "/status")
        self.assertTrue(all(s >= 0.25 - 1e-9 for s in sleeps[1:]))


if __name__ == "__main__":
    unittest.main()
