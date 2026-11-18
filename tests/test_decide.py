import unittest
from pathlib import Path

from agent.decide import Decider, facts_check
from tests.fakes import FakeClaude

FAQ = (Path(__file__).resolve().parent.parent / "data" / "faq.md").read_text(encoding="utf-8")
TICKET = {"id": "T1", "subject": "Wachtwoord", "body": "Ik ben mijn wachtwoord vergeten, hoe lang is de link geldig?",
          "customer_plan": "Basis", "channel": "email"}
GOOD_REPLY = ("Hoi, ga naar app.roosterly.nl/login en klik op \"Wachtwoord vergeten\". "
              "De link is 60 minuten geldig. Groet, Team Roosterly")


def decision(**over):
    base = {"ticket_summary_en": "Forgot password", "language": "nl", "rule_flags": [], "category": "question",
            "priority": "P4", "reason": "FAQ F01", "action": "reply", "faq_ids": ["F01"], "confidence": 0.95,
            "draft_reply": GOOD_REPLY}
    base.update(over)
    return base


REVIEW_OK = {"back_translation_en": "...", "draft_language": "nl", "unsupported_claims": [],
             "answers_question": True, "problems": []}


def run(outputs, ticket=TICKET):
    claude = FakeClaude(outputs)
    return Decider(claude, FAQ).decide(ticket), claude


class DecideTests(unittest.TestCase):
    def test_good_faq_reply_is_sent(self):
        d, claude = run([decision(), REVIEW_OK])
        self.assertEqual(d.action, "reply")
        self.assertEqual(claude.calls, 2)

    def test_refund_forced_to_handoff_even_if_model_says_reply(self):
        t = dict(TICKET, body="Kunnen we de maand augustus terugkrijgen? Ik wil een terugbetaling.")
        d, claude = run([decision(rule_flags=[], priority="P4"), ], t)
        self.assertEqual(d.action, "handoff")
        self.assertEqual(d.priority, "P2")
        self.assertIn("refund_or_dispute", d.rule_flags)
        self.assertIsNone(d.draft_reply)
        self.assertEqual(claude.calls, 1)  # no verifier call wasted on a forced handoff

    def test_model_flag_alone_forces_handoff(self):
        d, _ = run([decision(rule_flags=["legal_threat"])])
        self.assertEqual(d.action, "handoff")
        self.assertEqual(d.priority, "P1")

    def test_low_confidence_reply_becomes_handoff(self):
        d, _ = run([decision(confidence=0.5)])
        self.assertEqual(d.action, "handoff")

    def test_ungrounded_reply_becomes_handoff(self):
        d, _ = run([decision(faq_ids=[])])
        self.assertEqual(d.action, "handoff")

    def test_invented_price_fails_fact_check(self):
        bad = GOOD_REPLY + " Het kost slechts €9 per maand."
        d, claude = run([decision(draft_reply=bad)])
        self.assertEqual(d.action, "handoff")
        self.assertEqual(claude.calls, 1)  # deterministic check caught it before the verifier

    def test_reviewer_unsupported_claim_blocks_reply(self):
        d, _ = run([decision(), dict(REVIEW_OK, unsupported_claims=["promises refund"])])
        self.assertEqual(d.action, "handoff")

    def test_wrong_language_blocks_reply(self):
        d, _ = run([decision(), dict(REVIEW_OK, draft_language="en")])
        self.assertEqual(d.action, "handoff")

    def test_payment_details_never_leave(self):
        t = dict(TICKET, body="Mijn IBAN NL91 ABNA 0417 1643 00 klopt niet, kunt u dit nakijken?")
        d, _ = run([decision(action="handoff", draft_reply=None, category="billing", priority="P2",
                             handoff_note="Customer says IBAN NL91 ABNA 0417 1643 00 is wrong.",
                             reason="Billing question about NL91 ABNA 0417 1643 00")], t)
        self.assertNotIn("ABNA", d.handoff_note)
        self.assertNotIn("ABNA", d.reason)

    def test_empty_ticket_closed_without_model(self):
        d, claude = run([], {"id": "T2", "subject": "", "body": "  "})
        self.assertEqual(d.action, "close")
        self.assertEqual(claude.calls, 0)

    def test_low_confidence_close_becomes_handoff(self):
        d, _ = run([decision(action="close", draft_reply=None, confidence=0.6)])
        self.assertEqual(d.action, "handoff")

    def test_model_failure_falls_back_to_handoff(self):
        d, _ = run([RuntimeError("boom")])
        self.assertEqual(d.action, "handoff")
        self.assertTrue(d.handoff_note)

    def test_invalid_model_output_falls_back_to_handoff(self):
        d, _ = run([decision(priority="P9")])
        self.assertEqual(d.action, "handoff")

    def test_fact_check_accepts_faq_facts(self):
        self.assertEqual(facts_check(GOOD_REPLY, FAQ, "wachtwoord vergeten"), [])

    def test_fact_check_flags_unknown_url_and_number(self):
        probs = facts_check("Ga naar example.com, de link is 999 minuten geldig.", FAQ, "")
        self.assertEqual(len(probs), 2)


if __name__ == "__main__":
    unittest.main()
