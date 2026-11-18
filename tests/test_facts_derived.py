import unittest
from agent.decide import facts_check

FAQ = "Plus: €6 per active employee per month. Basis: €4 per active employee per month."


class DerivedPriceTests(unittest.TestCase):
    def test_calculated_price_is_accepted(self):
        ticket = "Wat kost Plus voor 25 medewerkers?"
        draft = "25 × €6 = €150 per maand, excl. btw."
        self.assertEqual(facts_check(draft, FAQ, ticket), [])

    def test_invented_price_is_rejected(self):
        ticket = "Wat kost Plus voor 25 medewerkers?"
        draft = "25 × €6 = €175 per maand."
        self.assertTrue(facts_check(draft, FAQ, ticket))

    def test_no_quantity_in_ticket_means_no_derived_price(self):
        ticket = "Wat kost Plus?"
        draft = "Dat is €150 per maand."
        self.assertTrue(facts_check(draft, FAQ, ticket))


if __name__ == "__main__":
    unittest.main()