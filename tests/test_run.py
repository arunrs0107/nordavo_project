import tempfile
import unittest
from pathlib import Path

from agent.decide import Decider
from agent.helpdesk import AlreadyHandled
from agent.journal import Journal
from agent.run import RunLog, process_ticket
from tests.fakes import FakeClaude
from tests.test_decide import FAQ, REVIEW_OK, TICKET, decision


class FakeHelpdesk:
    """Behaves like the real one: a second answer to the same ticket is a 409."""
    def __init__(self, fail_first_send=False):
        self.handled, self.sent, self.fail_first_send = set(), [], fail_first_send

    def reply(self, tid, payload):
        if self.fail_first_send:
            self.fail_first_send = False
            raise KeyboardInterrupt  # simulates a crash right before the POST completes
        if tid in self.handled:
            raise AlreadyHandled(tid)
        self.handled.add(tid)
        self.sent.append((tid, payload))
        return {}

    handoff = close = reply


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.journal_path = self.tmp / "journal.jsonl"

    def go(self, client, claude, journal=None):
        decider = Decider(claude, FAQ)
        return process_ticket(TICKET, decider=decider, client=client, journal=journal or Journal(self.journal_path),
                              log=RunLog(self.tmp / "logs"), dry_run=False, version="t")

    def test_crash_then_rerun_sends_once_and_reuses_decision(self):
        client = FakeHelpdesk(fail_first_send=True)
        claude = FakeClaude([decision(), REVIEW_OK])
        with self.assertRaises(KeyboardInterrupt):
            self.go(client, claude)
        calls_after_crash = claude.calls
        # restart: fresh Journal object reads the file; no new model calls are available
        outcome = self.go(client, claude)
        self.assertEqual(outcome, "sent")
        self.assertEqual(claude.calls, calls_after_crash)  # decision reused, not regenerated
        self.assertEqual(len(client.sent), 1)

    def test_rerun_after_success_never_double_sends(self):
        client = FakeHelpdesk()
        claude = FakeClaude([decision(), REVIEW_OK])
        self.go(client, claude)
        outcome = self.go(client, claude)  # e.g. ticket still shown open: the 409 is the backstop
        self.assertEqual(outcome, "already_handled")
        self.assertEqual(len(client.sent), 1)

    def test_changed_prompt_version_invalidates_cache(self):
        j = Journal(self.journal_path)
        j.record_decision(TICKET, "v1", decision())
        self.assertIsNotNone(j.cached_decision(TICKET, "v1"))
        self.assertIsNone(j.cached_decision(TICKET, "v2"))

    def test_log_has_one_line_and_no_body_or_payment_details(self):
        client = FakeHelpdesk()
        t = dict(TICKET, body="IBAN NL91 ABNA 0417 1643 00 " + TICKET["body"])
        decider = Decider(FakeClaude([decision(), REVIEW_OK]), FAQ)
        log = RunLog(self.tmp / "logs")
        process_ticket(t, decider=decider, client=client, journal=Journal(self.journal_path),
                       log=log, dry_run=False, version="t")
        lines = log.path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertNotIn("ABNA", lines[0])
        self.assertNotIn("wachtwoord vergeten, hoe lang", lines[0])


if __name__ == "__main__":
    unittest.main()
