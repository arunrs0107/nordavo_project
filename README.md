# Roosterly support ticket agent

A small agent that goes through the open support tickets for Roosterly, a shift planning app. For each ticket it decides to **reply**, **hand off to a human**, or **close**, and then posts that decision to the helpdesk API.

My main rule was to be safe first. If the agent isn't sure, a person looks at the ticket.

## Features

- Reads every open ticket from the helpdesk once, up front, so handling tickets cannot disturb paging.
- Asks the model for one structured decision: language, category, priority, action and reason.
- Checks that decision in code before anything is sent. The checks can only make an answer safer.
- Grounds every reply in the help centre: URLs, prices, menu names and numbers are compared against the FAQ.
- Sends each draft reply to a second model call that translates it back to English and lists unsupported claims.
- Redacts IBANs and card numbers from replies, handoff notes and logs.
- Survives crashes and repeat runs: decisions are journaled before posting, and a 409 is never retried.
- Writes one JSON line per ticket with timing and token counts, and never the customer's text.

## Technologies

| | |
|---|---|
| Language | Python 3.9+ (developed on 3.13) |
| HTTP | `requests` (with retries, backoff and its own rate limiter) |
| Model | Google Gemini free tier through the OpenAI-compatible backend, or Anthropic Claude |
| Configuration | `python-dotenv` plus environment variables; no secrets in the repo |
| Tests | `unittest` from the standard library, with a local mock helpdesk |
| Data | `data/faq.md` (help centre), `eval/results/*.json` (saved eval runs), `logs/*.jsonl` (run logs) |

## At a glance

| | |
|---|---|
| Language | Python |
| Model | Gemini `gemini-3.1-flash-lite` on the free tier (there is also an Anthropic backend in the code) |
| Eval | 10 out of 10 actions correct on the 10 labelled examples, with 0 unsafe replies |
| Final run | All 30 tickets handled, none failed: 11 replies, 17 handoffs, 2 closed |
| Cost | Free on the free tier. About $0.11 per 100 tickets on the paid price. |

## How to run

```
python -m venv .venv
.venv\Scripts\activate            (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env            (Mac/Linux: cp), then fill in the keys

python -m agent.run --dry-run     decide and log, post nothing
python -m agent.run --reset       final run: re-open the 30 tickets, handle each one once
python -m agent.run               handle whatever is still open (safe to repeat)
python -m eval.run_eval --tag x   score the 10 labelled examples
python -m unittest discover -s tests -t .
```

The `.env` file holds the keys and is not in the repo:

| Setting | What it is |
|---|---|
| `HELPDESK_KEY` | The key from Nordavo |
| `CANDIDATE_EMAIL` | your-email@example.com (the address you register the ticket run with) |
| `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | The model I use (see `.env.example`) |

To try the agent without the real helpdesk, `tools/mock_helpdesk.py` is a fake one I wrote. Its tickets are made up, so none of my numbers come from it.

## How it works

1. Read all the open tickets from the helpdesk.
2. Ask the model for a decision: language, category, priority, action and a reason.
3. Check the decision in code. The checks can only make it safer (a reply can become a handoff, never the other way round).
4. Save the decision to a file.
5. Post it to the helpdesk.

## Decisions

**The model suggests, the code decides.** The model returns one decision as JSON. My code then checks it before anything is sent. The model never talks to the helpdesk itself.

**The client's rules are checked twice.** These topics always go to a human: refunds and disputes, cancellations and contract changes, personal data requests, security incidents, legal threats, and customers who say they will leave. The model has to flag them, and a keyword list in `rules.py` (Dutch, English and German) checks again. If either one finds a match, the ticket is handed off.

**Checking Dutch replies when I can't read Dutch.** Every draft reply goes through two checks:
- Every URL, price, quoted menu name and number in the draft must appear in the FAQ or in the ticket. Simple maths with the FAQ prices is fine, like 25 employees times 6 euro.
- A second model call translates the draft back to English and lists anything the FAQ doesn't support.

If either check fails, the ticket is handed off. Every ticket also gets a one line English summary in the log, so I can see what it was about.

**Thresholds.** A reply needs a confidence of at least 0.75 and a reference to an FAQ entry. Closing a ticket needs at least 0.85.

**Assumptions I made.** The brief leaves these open, so I decided and wrote them down:
- Security and legal problems are at least P1. Refunds, contract changes, personal data and customers threatening to leave are at least P2.
- Asking how to upgrade or what a plan costs is a normal question (F05). Cancelling, downgrading or changing the terms is a contract change.
- "Where is our data stored?" is answered from F09. Requests to see, export or delete personal data always go to a human.
- A customer who can't log in counts as "one customer blocked", so P2, even though a reset link exists.
- For "do you integrate with X?" questions that the FAQ doesn't cover, the agent may point to the roadmap or hand off. Both are safe.

**How I used AI.** I used Claude, an AI assistant, a lot. It wrote most of the code and the tests, and the first version of this README. I set the project up on my own computer, picked the free Gemini model because I didn't want to pay for an API key, and ran the tests, the eval, the dry runs and the final run myself. I read the logs after each run, and I only kept a change when the eval stayed clean. I used the English summaries in the logs to check tickets I can't read.

## Reliability

| What happens | What the agent does |
|---|---|
| The helpdesk returns 503 | Waits and tries again, with a growing delay. Nothing was saved, so this is safe. |
| The helpdesk returns 429 | Waits for the `Retry-After` time and tries again. I also keep to 4 requests per second myself. |
| The helpdesk returns 409 (already answered) | Never retries. It is logged as already handled. |
| The program stops halfway | The decision was saved before posting. On the next run it reuses that decision without asking the model again. If the first post had worked, the retry gets a 409 and is logged as `already_handled_after_retry`. |
| The model key or model name is wrong | The whole run stops, and nothing is posted. |
| The model is rate limited for too long | That ticket is skipped and stays open for the next run. |

A broken model connection never turns into a pile of handoff decisions.

**Security.**
- Ticket text is escaped and treated as data, never as instructions.
- Card numbers and IBANs are removed from replies, handoff notes and logs.
- The log has one line per ticket and never contains the ticket text.
- Keys are read from environment variables.

## Eval and fix

I scored the agent on the 10 labelled examples from `GET /examples`. No model wrote or changed the labels.

| | Before | After |
|---|---|---|
| Language, category, priority | 1.0 | 1.0 |
| Action | 0.7 | 1.0 |
| Over-escalations (a human got a ticket the FAQ could answer) | 3 | 0 |
| Unsafe replies | 0 | 0 |

**The problem.** Three tickets that the FAQ could answer were sent to a human. I made a small change to the prompt and rules after reading the three wrong tickets, then re-ran the eval.

**Later fixes.** I found these by reading the reasons in the logs, and I re-ran the eval after each one:
- My own number check blocked a correct price calculation.
- The reviewer rejected a short German translation next to a Dutch menu name.
- A question about how to upgrade was flagged as a contract change.

**Limits.**
- 10 out of 10 is on the examples I tuned against, so it says little about tickets I haven't seen.
- Some tickets (like T016 and T020) switched between reply and handoff when I changed the prompt. With the final prompt, my dry run and my real run gave the same decisions and the same token counts.

## Cost per 100 tickets

The 30 ticket run used 91,039 input tokens and 6,896 output tokens. That is about **303,000 input and 23,000 output tokens per 100 tickets**.

On the free tier this costs nothing. On the paid plan, Google lists this model at $0.25 per million input tokens and $1.50 per million output tokens (standard rate, see ai.google.dev/gemini-api/docs/pricing):

- Input: 303,000 x $0.25 / 1,000,000 = about $0.076
- Output: 23,000 x $1.50 / 1,000,000 = about $0.035
- Total: **about $0.11 per 100 tickets**, or roughly a tenth of a cent per ticket

## What I would improve

- Run the reviewer only on drafts that are new or uncertain, to save time and cost.
- Test with more labelled tickets, including tricky ones like prompt injection and mixed languages.
- Add a lock so two runs can never overlap.
- Check menu paths against the FAQ in code instead of asking a model.
- The brief doesn't say what priority a refund should have, so P2 is my guess. I would ask the support team.

## Project layout

```
agent/      run.py (entry point), decide.py (decision and checks), rules.py (client rules, redaction),
            helpdesk.py (API client), journal.py (crash safety), llm.py (model access),
            prompts.py, config.py
eval/       run_eval.py and saved results
tests/      unit tests
tools/      mock_helpdesk.py (fake helpdesk for development)
data/       faq.md (the help centre text)
logs/       one log file per run
state/      journal.jsonl, created at runtime so a run can be resumed (not in the repo)
```
