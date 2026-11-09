# Calibration Book

**A public forecasting record built one settled question at a time. Scoring is integer arithmetic in the contract; when the resolver stays silent, the validators settle the question from a named web page and must back the verdict with words that are really on that page.**

Anyone can ask a yes/no question, name who will settle it (and, optionally, a public web page that states the answer), and let others
forecast. When the question is settled, every forecast is scored and added to its author's record: how close they were (Brier
score), whether they beat a coin flip (skill), and whether their 80% really means 80% (calibration bands). The record belongs to
the address, is public, and can be recomputed by anyone from the stored questions.

* `contracts/calibration_book.py`: the Intelligent Contract (one Python file, no funds).
* `tests/direct`: 274 direct-mode tests (model and web mocked). `tests/glsim`: 7 tests on a local 5-validator network.
* `scripts/mutation_check.py`: breaks the contract on purpose in 109 ways and checks that the tests notice (106 caught; survivors are listed in `evidence/mutation-checks.txt`).
* `demo/`: the two small pages used as sources in the live run (written by the author; see [Live run](#live-run)).
* `evidence/`: the outputs of those runs.

Status: **v0.2.0, deployed on Studionet at `0xF05D9B69e71Be13556A7651822e921e2E7d37b25`; tested in memory, on a local network and live on Studionet with real validators and models.** See [Live run](#live-run) and [What the record does not show](#what-the-record-does-not-show).

v0.1.0 had no model and no web access, so every validator computed the same numbers and there was nothing for them to judge; the
outcome of every question rested on one trusted resolver. v0.2.0 adds the part that needs validators: settling from a source.

## Why a contract for this

A forecaster's claim "I am usually right" is cheap. A record is only worth something if nobody can rewrite it, choose which
forecasts to show, or change the scoring afterwards. Here the forecast is fixed before the question closes, the scoring is code
every validator runs the same way, and the outcome is fixed once, either by the named resolver or, if the resolver is silent,
by the validators reading the page the question named when it was asked.

## How it works

| Step | Call | Rule |
|---|---|---|
| 1. Ask | `ask(text, closes_at, resolver, source_url, grace_seconds)` | Text 15 to 240 characters. `closes_at` is an ISO time (`2026-12-31T12:00:00Z`), at least 60 seconds and at most 400 days ahead. `resolver` is the address that will settle it (empty = the asker). `source_url` is empty or a public `https://` page (at most 300 characters, no `#`, `@`, spaces or non-ASCII; a plain host name, not `localhost`, `.local`, `.internal`, `.test`, `.invalid` or an address). With a source, `grace_seconds` (60 to 604800) is how long after closing the resolver has before anyone else may settle from the page; without a source it must be 0. |
| 2. Forecast | `forecast(question_id, probability_permille)` | 1 to 999 (the probability of YES in permille). **One forecast per address and question, final.** Only before the closing time. The resolver cannot forecast. At most 40 forecasts per question. |
| 3. Settle | `resolve(question_id, outcome_yes)` | Only the resolver, only after the closing time, once. |
| 3a. Settle from the source | `settle_from_source(question_id)` | Anyone, once closing time + grace has passed and the question is still open. Validators fetch the page and read it; see below. |
| 3b. Void | `void(question_id)` | The resolver can void a closed question at once; anyone can void it 30 days after closing if it is still open. A void question counts in nobody's record. |
| 4. Read | `get_record_json(address)`, `get_board_json(min_resolved)`, `get_question_json(id)` | Forecasts on an open question stay hidden, so nobody can copy them. |

The book holds at most 100 questions and 100 forecasters.

### Settling from the source (the part validators judge)

`settle_from_source` is the only non-deterministic step. Everything else is plain code.

1. The leader fetches the question's page (`gl.nondet.web.render`, text mode, first 8000 characters) and asks a model whether the
   page states that what the question asks about happened (`YES`), did not happen (`NO`), or says nothing conclusive
   (`UNDECIDED`). The question and the page go to the model as untrusted data; the prompt says to ignore instructions inside them
   and to use no outside knowledge. The model must answer with exactly `{"verdict", "quote"}`, where `quote` is 12 to 300
   characters copied from the page.
2. **The contract, not the model, checks the quotation**: after collapsing spaces and ignoring case it must occur in the page text. An
   answer that is not exactly that shape, a quotation that is not on the page, or a page that cannot be fetched is an *error*, never a verdict.
3. Each validator fetches the page itself, reads it with its own model call, and accepts the leader's result only if its own
   verdict is the same and the leader's quotation is on the page it fetched. Validators also agree on "unreadable".
4. `YES`/`NO` settle the question: the state becomes `RESOLVED`, `settled_by` is `SOURCE`, and `evidence` stores the quotation and
   the source address. `UNDECIDED` returns `{"verdict": "UNDECIDED"}` and changes nothing; the resolver (or anyone, later) can still act.
   An error reverts the call and changes nothing.

A resolver settlement and a source settlement are both final: whichever happens first closes the question.

### The numbers (permille, integers, rounded down)

* **brier** of one forecast = `(p - 1000 * outcome)^2 // 1000`. 0 is perfect, 250 is what always answering 500 would score, 998 is a confident miss.
* **skill** = `1000 * (250 * n - sum_brier) // (250 * n)`. Positive beats a coin flip, 0 equals it, negative is worse. It can fall far below zero for a confident miss.
* **buckets**: forecasts fall in five bands (1-200, 201-400, 401-600, 601-800, 801-999). For each band the record shows how many, the average forecast, and the share of YES outcomes. A forecaster who says 900 and is right half the time shows `avg_forecast_permille: 900` against `observed_yes_permille: 500`.
* **distinct_resolvers** = how many different settlers stand behind the questions in the record. A source-settled question counts the source address as its settler (`source:<url>`), so two questions settled from the same page count once. One resolver can favour a friend; several different ones are harder to arrange. `settled_from_source` says how many of the scored forecasts were settled by the validators; each scored forecast also shows `settled_by` (`RESOLVER` or `SOURCE`).
* **board** = forecasters with at least `min_resolved` settled forecasts, best skill first, then more forecasts, then address; at most 50 rows.

## Contract

`CalibrationBook` has no constructor arguments.

| Method | Kind | Purpose |
|---|---|---|
| `ask(text, closes_at, resolver, source_url, grace_seconds)` | write | Returns `{"id": n}`. |
| `forecast(question_id, probability_permille)` | write | One forecast, final. |
| `resolve(question_id, outcome_yes)` | write | The resolver settles YES (`true`) or NO (`false`). |
| `settle_from_source(question_id)` | write | Validators settle from the named page: returns `{"verdict": "YES"\|"NO"\|"UNDECIDED"}`. |
| `void(question_id)` | write | Take the question out of every record. |
| `get_question_json(question_id)` | view | The question, its state and, once it is closed, every forecast; `NONE` if unknown. |
| `get_record_json(address)` | view | Resolved, pending and void counts, mean brier, skill, buckets, distinct resolvers, every scored forecast; `NONE` if the address never forecast. |
| `get_board_json(min_resolved)` | view | The ranking described above. |
| `get_question_count()`, `get_questions_page(offset, limit)` | view | List the questions (pages of at most 50). |

## What the record does not show

These are limits of the idea, not bugs.

* **A question with no source still trusts its resolver.** The contract cannot know what really happened. A resolver who lies decides the outcome. The record shows how each question was settled, so a record settled by one address reads differently from one settled by many pages or addresses.
* **A source is only as good as the page.** The validators read the page when `settle_from_source` is called, not at closing time. A page that is edited, moved or deleted later gives a different reading; the asker should name a durable or archived page. If the person who asks also controls the page, the page proves nothing beyond their word. The stored quotation and source address let anyone judge that afterwards.
* **The check is that the words are on the page, not that they mean YES.** A quotation can be on the page and still be a poor reason. The validators must agree on the verdict, but they all use models; it is evidence, not proof.
* **Settlement can fail.** If validators read the page differently, cannot fetch it, or the model returns something unreadable, the call changes nothing and can be tried again. `UNDECIDED` also changes nothing, and a question nobody settles can still be voided after 30 days.
* **The URL check is narrow, not a sandbox.** It rejects obviously unsuitable addresses; it does not stop a public host from redirecting or serving different content to different readers.
* **Collusion is possible.** A resolver and a forecaster who work together can fake a good record. The contract only prevents the resolver from forecasting on their own question.
* **New addresses are free.** Anyone can start again under another address, so a bad record can be abandoned. A good record proves nothing about the person behind it.
* **Questions are chosen by the people who ask.** Forecasting only easy questions looks good. Look at how many questions and how many settlers stand behind a skill number; the board takes a minimum for that reason.
* **Skill rewards a few lucky calls.** One correct 99% forecast gives a high skill. Read it together with the number of resolved forecasts.
* **Time comes from the transaction.** Closing and settling use the network's timestamp for the transaction, not a clock you control.
* No funds are held and nothing is paid out. This is a record, not a market.

## Tests

Run from the repository root (Python 3.12+, `pip install genlayer-test`).

```
python3 -m pytest tests/direct     # 274 tests, in memory (genlayer-test direct mode; web and model mocked)
python3 -m pytest tests/glsim      # 7 tests on a local 5-validator network (pip install glsim fastapi uvicorn)
python3 scripts/mutation_check.py  # 109 deliberate breakages; 106 are caught and 3 cannot change a result (see evidence)
```

The expected scores come from a second implementation written with exact fractions (`ref_brier`, `ref_record` in the test file),
not from the contract's own formulas. Random books (25 seeded cases with several forecasters, resolvers and voided questions) are
compared with it cell by cell. The settlement tests cover clean and unclean model answers, quotations that are or are not on the page,
the grace period, source validation, and the validator rule with forged leader results (wrong verdict, quotation not on the
validator's page, malformed or oversized results, "unreadable" mismatches). The outputs of the runs are in `evidence/`.

The model and the web are **mocked** in these tests, so they show how the contract handles any answer, not what a real model
answers; that is why there is a live run below. Not covered: many real validators with different models, real wallets.

## Live run

Deployed on Studionet (GenLayer Studio) on 2026-11-09 at `0xF05D9B69e71Be13556A7651822e921e2E7d37b25`; the deploy transaction reached `FINALIZED`. Two questions were
asked from one account with a resolver address that never acts (`0x…0001`), a grace of 60 seconds and a closing time a few
minutes ahead, one forecast each (800 on question 0, 300 on question 1), and after closing time plus grace
`settle_from_source` was called on both. The real validators (12 in the set, running different models: Claude Sonnet, GPT-5.4, Gemini, Grok, Gemma, GLM) did the reading:

| Question | Source page (in `demo/`) | Result | Transaction |
|---|---|---|---|
| 0: "Did the demo test network have more than 100 validators by 8 November 2026?" | `report_published.txt`, which says the network reached 143 validators on 1 November 2026 | **YES**, `settled_by: SOURCE`, quotation stored: *"On 1 November 2026 the demo test network reached 143 validators. The network had more than 100 validators on that date and has kept them since."* | `0x510be2da22ecede37be91e8904a4f1f95797e0c43146287476883383223b5606` |
| 1: the same question | `report_pending.txt`, which says the count has not been published | **UNDECIDED**: question stays `OPEN`, nothing stored. Seven validators with different models agreed on it. | `0x9fc06f3a6b3a9b7c9ff679c3e04e325a431858e605b37290713b12f85b9ceafb` |

The forecast of 800 on question 0 was scored on chain: brier 40, skill 840, `distinct_resolvers` 1 (the source counts as its own settler),
`settled_from_source` 1. The outputs I read back are in `evidence/live-run-studionet.txt`.

What this does **not** show: both pages are written by the contract's author, so the run proves the mechanism (fetch, read,
quotation check, agreement, scoring), not that a source can be trusted. Only these two questions were tried; a page that
contradicts itself, a page that cannot be fetched on the network, a validator disagreement and `void` were not run live (the first
three are covered with mocks in the tests).

## Try it

Open [GenLayer Studio](https://studio.genlayer.com), load `contracts/calibration_book.py`, deploy it with no constructor
arguments, then call `ask` with a closing time a few minutes ahead (UTC, `2026-12-31T12:00:00Z` style), a source page and a
grace of 60, `forecast` from one or two other accounts, and after closing time plus grace call `settle_from_source`
(or `resolve` from the resolver account during the grace period).

## Where each claim is in the code

| Claim | Where |
|---|---|
| One forecast per address and question, none after closing, none by the resolver | `CalibrationBook.forecast` |
| Only the resolver settles, only after closing, once | `CalibrationBook.resolve` |
| Anyone may settle from the source only after closing + grace, only while open | `CalibrationBook.settle_from_source` |
| Source address rules | `_source_url` |
| The model's answer must be exactly verdict + quotation, and the quotation must be on the page | `_source_prompt`, `_judge_page`, `_on_page` |
| Validators fetch the page themselves and must agree | `_read_source` |
| Void rules (resolver at once, anyone after 30 days) | `CalibrationBook.void` |
| Forecasts of an open question are hidden | `CalibrationBook.get_question_json` |
| Brier, skill, buckets, distinct resolvers | `_brier`, `_bucket`, `_record` |
| Board order and minimum | `CalibrationBook.get_board_json` |
| Time is read from the transaction timestamp and parsed by hand | `_now` / `_timestamp` |

## Licence

MIT, see `LICENSE`.
