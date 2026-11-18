"""One command: python -m agent.run

    python -m agent.run                # handle every open ticket
    python -m agent.run --dry-run      # decide and print, post nothing
    python -m agent.run --reset        # POST /practice/reset, clear local state, then run (final run)
    python -m agent.run --limit 5      # only the first 5 open tickets
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import rules
from .config import FAQ_PATH, LOG_DIR, STATE_DIR, load_settings
from .decide import Decider, Decision
from .helpdesk import AlreadyHandled, ApiError, Helpdesk, HelpdeskError
from .journal import Journal
from .llm import LLMFatal, LLMUnavailable, make_llm
from .prompts import PROMPT_VERSION


def send(client: Helpdesk, ticket_id: str, d: Decision) -> dict:
    payload = d.api_payload()
    if d.action == "reply":
        return client.reply(ticket_id, payload)
    if d.action == "handoff":
        return client.handoff(ticket_id, payload)
    return client.close(ticket_id, payload)


class RunLog:
    """One JSON line per ticket. Never contains the ticket body or any payment detail."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.path = directory / f"run-{stamp}.jsonl"

    def write(self, **fields) -> None:
        fields = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **fields}
        line = rules.redact(json.dumps(fields, ensure_ascii=False))
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def process_ticket(ticket: dict, *, decider: Decider, client: Optional[Helpdesk], journal: Journal,
                   log: RunLog, dry_run: bool, version: str) -> str:
    tid = ticket["id"]
    started = time.monotonic()
    tokens_before = (decider.claude.usage.input_tokens, decider.claude.usage.output_tokens)

    cached = journal.cached_decision(ticket, version)
    if cached:
        decision, source = Decision.from_dict(cached), "journal"
    else:
        try:
            decision, source = decider.decide(ticket), "model"
        except LLMUnavailable as exc:  # leave the ticket open; a later run will pick it up
            log.write(ticket_id=tid, outcome="skipped_model_unavailable", error=str(exc)[:200])
            print(f"{tid}: skipped (model unavailable, ticket left open)")
            return "skipped_model_unavailable"
        if not dry_run:
            journal.record_decision(ticket, version, decision.to_dict())  # saved BEFORE sending

    outcome = "dry_run"
    if not dry_run:
        try:
            send(client, tid, decision)
            journal.record_finished(tid, "sent")
            outcome = "sent"
        except AlreadyHandled as exc:
            journal.record_finished(tid, "already_handled")
            outcome = "already_handled_after_retry" if exc.ambiguous else "already_handled"
        except (ApiError, HelpdeskError) as exc:
            outcome = f"error: {str(exc)[:200]}"

    log.write(
        ticket_id=tid, outcome=outcome, decision_source=source,
        language=decision.language, category=decision.category, priority=decision.priority,
        action=decision.action, confidence=decision.confidence, faq_ids=decision.faq_ids,
        rule_flags=decision.rule_flags, overrides=decision.overrides,
        summary_en=decision.summary_en, reason=decision.reason,
        draft_reply=decision.draft_reply, handoff_note=decision.handoff_note,  # payment details already redacted
        seconds=round(time.monotonic() - started, 2),
        tokens_in=decider.claude.usage.input_tokens - tokens_before[0],
        tokens_out=decider.claude.usage.output_tokens - tokens_before[1],
    )
    print(f"{tid}: {decision.action:<8} {decision.priority} {decision.category:<15} "
          f"{decision.language} -> {outcome}")
    return outcome


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Roosterly support-ticket agent")
    parser.add_argument("--dry-run", action="store_true", help="decide but do not post anything")
    parser.add_argument("--reset", action="store_true", help="re-open all practice tickets and clear local state first")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    settings = load_settings()
    client = Helpdesk(settings.helpdesk_url, settings.helpdesk_key, settings.candidate_email, settings.candidate_name)
    journal = Journal(STATE_DIR / "journal.jsonl")

    if args.reset:
        client.reset_practice()
        journal.clear()
        print("Practice tickets re-opened, local state cleared.")

    claude = make_llm(settings)
    decider = Decider(claude, FAQ_PATH.read_text(encoding="utf-8"), settings.confidence_min)
    log = RunLog(LOG_DIR)
    version = f"{PROMPT_VERSION}:{settings.provider}:{settings.model}:{settings.confidence_min}"

    tickets = client.list_open()
    if args.limit:
        tickets = tickets[: args.limit]
    print(f"{len(tickets)} open tickets. Log: {log.path}")

    outcomes = []
    try:
        for ticket in tickets:
            outcomes.append(process_ticket(ticket, decider=decider, client=client, journal=journal,
                                           log=log, dry_run=args.dry_run, version=version))
    except LLMFatal as exc:
        print(f"\nSTOPPED: the model API is not usable ({exc}). Nothing further was sent. Fix .env and re-run.")
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted. Re-run to continue where it stopped; nothing is sent twice.")
        return 130

    failed = [o for o in outcomes if o.startswith("error") or o.startswith("skipped")]
    print(f"Done: {len(outcomes) - len(failed)} ok, {len(failed)} failed or skipped (re-run to retry). "
          f"Tokens in/out: {claude.usage.input_tokens}/{claude.usage.output_tokens}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
