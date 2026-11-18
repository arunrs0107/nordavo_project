"""Deterministic safety layer. The model is the first line of defence for the client's
'always go to a human' rules; this module is the second, so one bad model answer cannot
send a refund or a legal threat to auto-reply.

It only ever makes the agent MORE cautious (force a handoff, raise a priority). Sending an
unsure ticket to a human is never punished; a wrong automatic reply is.
"""
import re
from typing import Dict, Set


RULES: Dict[str, tuple] = {
    "refund_or_dispute": ("P2", [
        r"terugbetal", r"terugstort", r"restitutie", r"geld terug", r"terugkrijgen", r"credit ?nota",
        r"creditfactuur", r"betwist", r"onterecht", r"dubbel (afgeschreven|betaald|gefactureerd|geïncasseerd)",
        r"te veel (betaald|afgeschreven|gefactureerd)", r"chargeback", r"refund", r"money back",
        r"double[- ]charged", r"disput", r"erstattung", r"rückzahlung", r"zurückzahl",
        r"niet akkoord met (de )?(factuur|afschrijving|incasso)", r"incasso.{0,20}stornee?r",
    ]),
    "cancellation_or_contract": ("P2", [
        r"opzeg", r"opzegg", r"beëindig", r"stopzetten", r"annuler", r"\bcancel", r"terminat",
        r"\bcontract\b", r"contractwijziging", r"contractduur", r"looptijd", r"overeenkomst",
        r"downgrad", r"kündig", r"vertrag",
    ]),
    "personal_data_request": ("P2", [
        r"\bavg\b", r"gdpr", r"persoonsgegevens", r"persoonlijke gegevens", r"\binzage\b",
        r"vergeten worden", r"recht op vergetelheid", r"gegevens (laten )?verwijderen",
        r"(mijn|onze|alle) (gegevens|data) .{0,20}verwijder", r"data subject", r"datenschutz",
        r"auskunft", r"löschung", r"autoriteit persoonsgegevens",
    ]),
    "security_incident": ("P1", [
        r"gehackt", r"\bhack", r"datalek", r"data ?breach", r"\bbreach", r"ongeautoriseerd",
        r"onbevoegd", r"unauthori[sz]ed", r"phishing", r"verdachte (login|inlog|activiteit|mail)",
        r"onbekende (login|inlog|persoon|apparaat)", r"account.{0,25}(overgenomen|misbruikt)",
        r"iemand (anders )?(heeft )?(toegang|ingelogd)", r"gestolen", r"sicherheitsvorfall", r"unbefugt",
        r"beveiligingsincident", r"security incident", r"\blek\b",
    ]),
    "legal_threat": ("P1", [
        r"advocaat", r"juridisch", r"rechtszaak", r"aanklagen", r"dagvaard", r"aansprakelijk",
        r"\brechter\b", r"ingebrekestelling", r"lawyer", r"legal action", r"\bsue\b", r"lawsuit",
        r"\banwalt", r"rechtliche schritte", r"\bklage\b",
    ]),
    "churn_threat": ("P2", [
        r"overstappen", r"stap(pen)? over", r"stappen we over", r"naar (een )?(andere|concurrent)",
        r"concurrent", r"andere (leverancier|aanbieder|partij|software)", r"weg bij jullie",
        r"switch(ing)? to", r"look(ing)? elsewhere", r"leave you", r"wechseln", r"abwander",
    ]),
}
_COMPILED = {name: (prio, [re.compile(p, re.I) for p in pats]) for name, (prio, pats) in RULES.items()}


def scan(text: str) -> Set[str]:
    """Which always-escalate rules does this text trip?"""
    return {name for name, (_, pats) in _COMPILED.items() if any(p.search(text) for p in pats)}


def min_priority(flags) -> str:
    """Most urgent minimum implied by the flags ('P1' is more urgent than 'P2'); '' if none."""
    prios = [RULES[f][0] for f in flags if f in RULES]
    return min(prios) if prios else ""



_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b", re.I)
_CARD = re.compile(r"(?<![\w€])(?:\d[ -]?){13,19}(?!\w)")


def redact(text: str) -> str:
    text = _IBAN.sub("[IBAN removed]", text)
    return _CARD.sub("[card number removed]", text)


def contains_payment_details(text: str) -> bool:
    return redact(text) != text
