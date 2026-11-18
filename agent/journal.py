"""Append-only journal that makes the agent safe to re-run.

Order of operations per ticket:
  1. decide  -> journal 'decided' (full decision, fsynced)
  2. POST to helpdesk
  3. journal 'sent' (or 'already_handled')

If the process dies between 1 and 3, the next run finds the ticket still open on the
server, reuses the saved decision (no second model call, no different answer) and sends it.
If the POST had actually landed, the server answers 409 and we record that instead.
The helpdesk's own 409 is the final guard: a customer can never get two answers.
"""
import hashlib
import json
import os
from pathlib import Path
from typing import Optional


def fingerprint(ticket: dict, version: str) -> str:
    """Cached decisions are only reused for identical ticket text AND identical prompt/code version."""
    raw = json.dumps(
        [ticket.get("id"), ticket.get("subject"), ticket.get("body"), ticket.get("customer_plan"), version],
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class Journal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._decisions = {}
        self._finished = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a torn last line from a crash is ignored
                if rec.get("event") == "decided":
                    self._decisions[rec["ticket_id"]] = rec
                elif rec.get("event") in ("sent", "already_handled"):
                    self._finished[rec["ticket_id"]] = rec

    def _append(self, rec: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def cached_decision(self, ticket: dict, version: str) -> Optional[dict]:
        rec = self._decisions.get(ticket["id"])
        if rec and rec.get("fingerprint") == fingerprint(ticket, version):
            return rec["decision"]
        return None

    def record_decision(self, ticket: dict, version: str, decision: dict) -> None:
        rec = {"event": "decided", "ticket_id": ticket["id"],
               "fingerprint": fingerprint(ticket, version), "decision": decision}
        self._decisions[ticket["id"]] = rec
        self._append(rec)

    def record_finished(self, ticket_id: str, event: str) -> None:
        rec = {"event": event, "ticket_id": ticket_id}
        self._finished[ticket_id] = rec
        self._append(rec)

    def clear(self) -> None:
        self._decisions.clear()
        self._finished.clear()
        if self.path.exists():
            self.path.unlink()
