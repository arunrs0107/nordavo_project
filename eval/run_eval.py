"""Eval on the 10 labelled examples from GET /examples.

    python -m eval.run_eval --tag before
    ... change something ...
    python -m eval.run_eval --tag after

The labels come from Nordavo; no model ever writes or edits them. The agent's full pipeline
(model + guards + reply checks) runs on each example, but nothing is posted anywhere.
Results are saved to eval/results/<tag>.json so before/after can be compared.
"""
import argparse
import json
import sys
from pathlib import Path

from agent.config import FAQ_PATH, load_settings
from agent.decide import Decider
from agent.helpdesk import Helpdesk
from agent.llm import make_llm

RESULTS_DIR = Path(__file__).parent / "results"
FIELDS = ["language", "category", "priority", "action"]


def score(label: dict, d) -> dict:
    got = {"language": d.language, "category": d.category, "priority": d.priority, "action": d.action}
    row = {f: got[f] == label.get(f) for f in FIELDS}
    row["got"], row["want"] = got, {f: label.get(f) for f in FIELDS}
    # Safety: replying where the label says a human must handle it is the worst error.
    row["unsafe_reply"] = d.action == "reply" and label.get("action") != "reply"
    # Usefulness: sending a FAQ-answerable ticket to a human is the over-escalation error.
    row["over_escalation"] = d.action == "handoff" and label.get("action") == "reply"
    return row


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="run")
    args = parser.parse_args(argv)

    s = load_settings()
    client = Helpdesk(s.helpdesk_url, s.helpdesk_key, s.candidate_email, s.candidate_name)
    claude = make_llm(s)
    decider = Decider(claude, FAQ_PATH.read_text(encoding="utf-8"), s.confidence_min)

    rows = []
    for ex in client.examples():
        ticket = {"id": ex["id"], "subject": ex.get("subject"), "body": ex.get("body"),
                  "customer_plan": ex.get("customer_plan"), "channel": ex.get("channel")}
        d = decider.decide(ticket)
        row = score(ex["label"], d)
        row.update(id=ex["id"], summary_en=d.summary_en, overrides=d.overrides)
        rows.append(row)
        flags = "".join("." if row[f] else "X" for f in FIELDS)
        print(f"{ex['id']}  [{flags}] want {row['want']['action']}/{row['want']['priority']}/{row['want']['category']}"
              f"  got {d.action}/{d.priority}/{d.category}"
              f"{'  UNSAFE' if row['unsafe_reply'] else ''}{'  OVER-ESCALATED' if row['over_escalation'] else ''}")

    n = len(rows)
    summary = {f: sum(r[f] for r in rows) / n for f in FIELDS}
    summary["unsafe_replies"] = sum(r["unsafe_reply"] for r in rows)
    summary["over_escalations"] = sum(r["over_escalation"] for r in rows)
    summary["tickets"] = n
    summary["tokens_in"], summary["tokens_out"] = claude.usage.input_tokens, claude.usage.output_tokens
    if s.price_in is not None and s.price_out is not None:
        cost = (claude.usage.input_tokens * s.price_in + claude.usage.output_tokens * s.price_out) / 1e6
        summary["cost_per_100_tickets_usd"] = round(cost / n * 100, 4)

    print("\n" + json.dumps(summary, indent=2))
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / f"{args.tag}.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
