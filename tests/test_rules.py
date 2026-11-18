import unittest

from agent import rules


class RulesTests(unittest.TestCase):
    def test_redacts_iban_and_card(self):
        s = "Mijn IBAN is NL91 ABNA 0417 1643 00 en kaart 4111 1111 1111 1111."
        out = rules.redact(s)
        self.assertNotIn("ABNA", out)
        self.assertNotIn("4111", out)
        self.assertTrue(rules.contains_payment_details(s))

    def test_redacts_unspaced_lowercase_iban(self):
        self.assertNotIn("abna", rules.redact("nl91abna0417164300").lower().replace("[iban removed]", ""))

    def test_plain_text_untouched(self):
        s = "Het wachtwoord is 60 minuten geldig, prijs €4 per maand, bel 010 123."
        self.assertEqual(rules.redact(s), s)

    def test_flags_client_rules_in_dutch(self):
        cases = {
            "We willen onze betaling van augustus terugkrijgen": "refund_or_dispute",
            "Wij willen ons abonnement opzeggen": "cancellation_or_contract",
            "Ik wil inzage in mijn persoonsgegevens (AVG)": "personal_data_request",
            "Ik denk dat ons account gehackt is": "security_incident",
            "Anders schakel ik mijn advocaat in": "legal_threat",
            "Anders stappen we over naar een concurrent": "churn_threat",
        }
        for text, flag in cases.items():
            self.assertIn(flag, rules.scan(text), text)

    def test_howto_tickets_are_not_flagged(self):
        for text in ["Hoe voeg ik een medewerker toe? Contracturen zijn 32.",
                     "Ik ben mijn wachtwoord vergeten",
                     "Kan ik de app ook in het Duits gebruiken?"]:
            self.assertEqual(rules.scan(text), set(), text)

    def test_priority_floor(self):
        self.assertEqual(rules.min_priority({"refund_or_dispute"}), "P2")
        self.assertEqual(rules.min_priority({"refund_or_dispute", "security_incident"}), "P1")
        self.assertEqual(rules.min_priority(set()), "")


if __name__ == "__main__":
    unittest.main()
