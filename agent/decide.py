"""Decision pipeline: model proposes -> code guards -> (for replies) two independent checks.

Design rule: the model can only ever propose. Code decides what is sent, and every guard
can only make the outcome safer (reply -> handoff), never the reverse.
"""
import re
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from . import rules
from .llm import LLMFatal, LLMUnavailable
from .prompts import (DECISION_TOOL, REVIEW_TOOL, build_decision_system,
                      build_verifier_system, render_ticket)

LANGS = {"nl", "en", "de"}
CATEGORIES = {"bug", "billing", "feature_request", "question", "other"}
PRIORITIES = {"P1", "P2", "P3", "P4"}
ACTIONS = {"reply", "handoff", "close"}
CLOSE_MIN_CONFIDENCE = 0.85


@dataclass
class Decision:
    language: str = "nl"
    category: str = "other"
    priority: str = "P3"
    reason: str = ""
    action: str = "handoff"
    draft_reply: Optional[str] = None
    handoff_note: Optional[str] = None
    confidence: Optional[float] = None
    faq_ids: List[str] = field(default_factory=list)
    rule_flags: List[str] = field(default_factory=list)
    summary_en: str = ""
    overrides: List[str] = field(default_factory=list)  # every time code overruled the model

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Decision":
        return cls(**data)

    def api_payload(self) -> dict:
        body = {"language": self.language, "category": self.category, "priority": self.priority,
                "reason": self.reason}
        if self.confidence is not None:
            body["confidence"] = round(float(self.confidence), 2)
        if self.action == "reply":
            body["draft_reply"] = self.draft_reply
        elif self.action == "handoff":
            body["handoff_note"] = self.handoff_note
        return body


# ---------------------------------------------------------------------------------------
# Deterministic fact check: lets us validate a Dutch reply without reading Dutch.
# Every URL, euro amount, quoted menu name and number in the draft must come from the
# help centre (or from the customer's own ticket text).
# ---------------------------------------------------------------------------------------
_URL = re.compile(r"(?<![@\w.-])(?:https?://)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:nl|com|de|eu)(?:/[^\s,;)\"”]*)?", re.I)
_EURO = re.compile(r"€\s?\d+(?:[.,]\d+)?")
_QUOTED = re.compile(r"[\"“„]([^\"”“]{2,60})[\"”“]")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.casefold()).strip()


def _num(text: str) -> float:
    return float(text.replace(",", "."))


def _derived_amounts(faq: str, ticket: str) -> set:
    """Products of a help-centre price and a number from the ticket (e.g. 25 employees x EUR 6).
    A correct calculation from documented prices is not an invented fact."""
    prices = {_num(p) for p in re.findall(r"€\s?(\d+(?:[.,]\d+)?)", faq)}
    counts = {float(n) for n in re.findall(r"\b\d{1,4}\b", ticket)}
    return {round(p * n, 2) for p in prices for n in counts}


def facts_check(draft: str, faq_text: str, ticket_text: str) -> List[str]:
    """Return a list of problems (empty = ok)."""
    faq, ticket = _norm(faq_text), _norm(ticket_text)
    faq_nospace = faq.replace(" ", "")
    derived = _derived_amounts(faq, ticket)
    problems = []
    for m in _URL.findall(draft):
        if m.casefold().rstrip(".,;:)") not in faq:
            problems.append(f"URL not in help centre: {m}")
    for m in _EURO.findall(draft):
        amount = _num(re.sub(r"[^\d.,]", "", m))
        if m.casefold().replace(" ", "") not in faq_nospace and round(amount, 2) not in derived:
            problems.append(f"price not in help centre: {m}")
    for m in _QUOTED.findall(draft):
        if _norm(m) not in faq and _norm(m) not in ticket:
            problems.append(f"quoted text not in help centre: {m}")
    allowed = {_num(x) for x in _NUMBER.findall(faq)} | {_num(x) for x in _NUMBER.findall(ticket)} | derived
    for m in _NUMBER.findall(draft):
        if _num(m) not in allowed:
            problems.append(f"number not in help centre or ticket: {m}")
    return problems


class Decider:
    def __init__(self, claude, faq_text: str, confidence_min: float = 0.75):
        self.claude = claude
        self.faq_text = faq_text
        self.confidence_min = confidence_min
        self._decision_system = build_decision_system(faq_text)
        self._verifier_system = build_verifier_system(faq_text)

    # -- public ----------------------------------------------------------------------
    def decide(self, ticket: dict) -> Decision:
        subject = (ticket.get("subject") or "").strip()
        body = (ticket.get("body") or "").strip()
        if not subject and not body:
            return Decision(language="nl", category="other", priority="P4", action="close", confidence=1.0,
                            reason="Empty ticket: no subject and no body.", summary_en="(empty ticket)")
        try:
            raw = self.claude.call_tool(self._decision_system, render_ticket(ticket), DECISION_TOOL)
            decision = self._parse(raw)
        except (LLMFatal, LLMUnavailable):
            raise  # not a ticket problem: never turn a broken model connection into 'handoff' decisions
        except Exception as exc:  # unparseable or invalid answer: a human takes it
            return self._fallback(ticket, f"model call failed ({exc.__class__.__name__})")
        decision = self._apply_guards(ticket, decision)
        return self._finalise(decision)

    # -- parsing -----------------------------------------------------------------------
    def _parse(self, raw: dict) -> Decision:
        d = Decision(
            language=raw.get("language", "other"),
            category=raw.get("category", ""),
            priority=raw.get("priority", ""),
            reason=str(raw.get("reason", "")).strip(),
            action=raw.get("action", ""),
            draft_reply=(raw.get("draft_reply") or "").strip() or None,
            handoff_note=(raw.get("handoff_note") or "").strip() or None,
            confidence=_to_float(raw.get("confidence")),
            faq_ids=[str(x) for x in raw.get("faq_ids", [])],
            rule_flags=[str(x) for x in raw.get("rule_flags", [])],
            summary_en=str(raw.get("ticket_summary_en", "")).strip(),
        )
        if d.category not in CATEGORIES or d.priority not in PRIORITIES or d.action not in ACTIONS:
            raise ValueError("invalid enum value from model")
        return d

    # -- guards ------------------------------------------------------------------------
    def _apply_guards(self, ticket: dict, d: Decision) -> Decision:
        text = f"{ticket.get('subject') or ''}\n{ticket.get('body') or ''}"

        # 1. Client's always-handoff rules: model flags UNION keyword safety net.
        flags = sorted(set(d.rule_flags) | rules.scan(text))
        d.rule_flags = flags
        if flags:
            floor = rules.min_priority(flags)
            if floor and d.priority > floor:  # 'P3' > 'P2' as strings: less urgent than the floor
                d.overrides.append(f"priority raised {d.priority}->{floor} by rule {','.join(flags)}")
                d.priority = floor
            if d.action != "handoff":
                d.overrides.append(f"{d.action}->handoff by client rule: {', '.join(flags)}")
                self._to_handoff(d, f"Client rule: {', '.join(flags)} always goes to a human.")

        # 2. Languages we cannot answer in.
        if d.language not in LANGS:
            d.language = "en"
            if d.action == "reply":
                d.overrides.append("reply->handoff: unsupported language")
                self._to_handoff(d, "Ticket is in a language other than nl/en/de.")

        # 3. Closing needs high confidence; otherwise a human looks.
        if d.action == "close" and (d.confidence or 0) < CLOSE_MIN_CONFIDENCE:
            d.overrides.append("close->handoff: low confidence")
            self._to_handoff(d, "Looked like spam/empty but the model was not sure.")

        # 4. Replying needs grounding, confidence and clean text.
        if d.action == "reply":
            why = None
            if not d.draft_reply:
                why = "no draft produced"
            elif not d.faq_ids:
                why = "reply not grounded in any help-centre entry"
            elif (d.confidence or 0) < self.confidence_min:
                why = f"confidence {d.confidence} below {self.confidence_min}"
            elif rules.contains_payment_details(d.draft_reply):
                why = "draft contained payment details"
            if why is None:
                why = self._verify(ticket, d, text)
            if why:
                d.overrides.append(f"reply->handoff: {why}")
                self._to_handoff(d, f"Automatic reply not safe to send ({why}).")
        return d

    def _verify(self, ticket: dict, d: Decision, ticket_text: str) -> Optional[str]:
        """Returns None if the reply passes, otherwise a short reason."""
        problems = facts_check(d.draft_reply, self.faq_text, ticket_text)
        if problems:
            return "fact check failed: " + "; ".join(problems[:3])
        try:
            user = render_ticket(ticket) + "\n\n<draft_reply>\n" + d.draft_reply.replace("<", "&lt;") + "\n</draft_reply>"
            review = self.claude.call_tool(self._verifier_system, user, REVIEW_TOOL)
        except (LLMFatal, LLMUnavailable):
            raise
        except Exception as exc:
            return f"review failed ({exc.__class__.__name__})"
        if review.get("draft_language") != d.language:
            return f"draft language {review.get('draft_language')} != ticket language {d.language}"
        if review.get("unsupported_claims"):
            return "unsupported claims: " + "; ".join(map(str, review["unsupported_claims"][:3]))
        if not review.get("answers_question"):
            return "draft does not answer the question"
        if review.get("problems"):
            return "reviewer problems: " + "; ".join(map(str, review["problems"][:3]))
        return None

    # -- helpers -------------------------------------------------------------------------
    def _to_handoff(self, d: Decision, why: str) -> None:
        d.action = "handoff"
        d.draft_reply = None
        if not d.handoff_note:
            d.handoff_note = (
                f"{why} Customer ({d.language}) wrote: {d.summary_en or 'see ticket'}. "
                f"Model reasoning: {d.reason or 'n/a'}. Please review and answer manually."
            )

    def _fallback(self, ticket: dict, why: str) -> Decision:
        d = Decision(language="nl", category="other", priority="P3", action="handoff", confidence=0.0,
                     reason=f"Could not classify automatically: {why}.",
                     handoff_note=f"Automatic triage failed ({why}). Please read the ticket and handle it manually.")
        d.overrides.append(f"fallback handoff: {why}")
        text = f"{ticket.get('subject') or ''}\n{ticket.get('body') or ''}"
        d.rule_flags = sorted(rules.scan(text))
        return self._finalise(d)

    def _finalise(self, d: Decision) -> Decision:
        """Last step before anything leaves the process: redact payment details everywhere."""
        d.reason = rules.redact(d.reason) or "No reason given."
        d.summary_en = rules.redact(d.summary_en)
        if d.draft_reply:
            d.draft_reply = rules.redact(d.draft_reply)
        if d.action == "handoff":
            d.handoff_note = rules.redact(d.handoff_note or "Please review this ticket.")
        if d.overrides:
            d.reason = (d.reason + " [guard: " + " | ".join(d.overrides) + "]")[:500]
        return d


def _to_float(value) -> Optional[float]:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None
