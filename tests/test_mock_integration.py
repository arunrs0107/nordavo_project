"""End-to-end check of the REAL Helpdesk client against the local mock server over HTTP."""
import threading
import unittest

from agent.helpdesk import AlreadyHandled, ApiError, Helpdesk
from tools.mock_helpdesk import TICKETS, serve


class MockIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = serve(port=0, fail_rate=0.3)  # 30% 503s: the client must cope
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def hd(self):
        return Helpdesk(self.url, "testkey123", "me@example.com", max_rps=4)

    def test_full_cycle_with_503s_pagination_409_reset(self):
        hd = self.hd()
        tickets = hd.list_open()
        self.assertEqual(len(tickets), len(TICKETS))  # pagination (10/page) worked despite 503s
        body = {"language": "nl", "category": "question", "priority": "P4", "reason": "r", "draft_reply": "hoi"}
        hd.reply("T001", body)
        with self.assertRaises(AlreadyHandled):
            hd.reply("T001", body)  # a second answer is refused
        self.assertEqual(len(hd.list_open()), len(TICKETS) - 1)
        with self.assertRaises(ApiError) as ctx:
            hd.reply("T002", {"language": "xx"})
        self.assertEqual(ctx.exception.status, 422)
        hd.reset_practice()
        self.assertEqual(len(hd.list_open()), len(TICKETS))

    def test_fast_burst_is_throttled_not_rejected(self):
        hd = self.hd()
        for _ in range(12):
            hd.status()  # would trigger 429s if we did not throttle; client handles them either way


if __name__ == "__main__":
    unittest.main()
