# Calibration Book

**A public forecasting record that is built one settled question at a time, and scored with integer arithmetic by the contract.**

Anyone can ask a yes/no question, name who will settle it, and let others forecast. When the resolver settles it, every forecast
is scored and added to its author's record: how close they were (Brier score), whether they beat a coin flip (skill), and
whether their 80% really means 80% (calibration bands). The record belongs to the address, is public, and can be recomputed by
anyone from the stored questions.

* `contracts/calibration_book.py`: the Intelligent Contract (one Python file, no funds, **no model, no web access**).
* `tests/direct`: 118 direct-mode tests. `tests/glsim`: 3 tests on a local 5-validator network.
* `scripts/mutation_check.py`: breaks the contract on purpose in 53 ways and checks that the tests notice (53 of 53 do).
* `evidence/`: the outputs of those runs.

Status: **v0.1.0, tested in memory and on a local network; not deployed to any public network yet.** See [Live run](#live-run) and [What the record does not show](#what-the-record-does-not-show).

## Why a contract for this

A forecaster's claim "I am usually right" is cheap. A record is only worth something if nobody can rewrite it, choose which
forecasts to show, or change the scoring afterwards. Here the forecast is fixed before the question closes, the outcome is
fixed once by a named resolver, and the scoring is code every validator runs the same way. There is no model in the loop, so
there is nothing for validators to disagree about: they all compute the identical number.

## How it works

| Step | Call | Rule |
|---|---|---|
| 1. Ask | `ask(text, closes_at, resolver)` | Text 15 to 240 characters. `closes_at` is an ISO time (`2026-12-31T12:00:00Z`), at least 60 seconds and at most 400 days ahead. `resolver` is the address that will settle it (empty = the asker). |
| 2. Forecast | `forecast(question_id, probability_permille)` | 1 to 999 (the probability of YES in permille). **One forecast per address and question, final.** Only before the closing time. The resolver cannot forecast. At most 40 forecasts per question. |
| 3. Settle | `resolve(question_id, outcome_yes)` | Only the resolver, only after the closing time, once. |
| 3b. Void | `void(question_id)` | The resolver can void a closed question at once; anyone can void it 30 days after closing if it is still open. A void question counts in nobody's record. |
| 4. Read | `get_record_json(address)`, `get_board_json(min_resolved)`, `get_question_json(id)` | Forecasts on an open question stay hidden, so nobody can copy them. |

The book holds at most 100 questions and 100 forecasters.

### The numbers (permille, integers, rounded down)

* **brier** of one forecast = `(p - 1000 * outcome)^2 // 1000`. 0 is perfect, 250 is what always answering 500 would score, 998 is a confident miss.
* **skill** = `1000 * (250 * n - sum_brier) // (250 * n)`. Positive beats a coin flip, 0 equals it, negative is worse. It can fall far below zero for a confident miss.
* **buckets**: forecasts fall in five bands (1-200, 201-400, 401-600, 601-800, 801-999). For each band the record shows how many, the average forecast, and the share of YES outcomes. A forecaster who says 900 and is right half the time shows `avg_forecast_permille: 900` against `observed_yes_permille: 500`.
* **distinct_resolvers** = how many different addresses settled the questions in the record. One resolver can favour a friend; three different ones are harder to arrange.
* **board** = forecasters with at least `min_resolved` settled forecasts, best skill first, then more forecasts, then address; at most 50 rows.

## Contract

`CalibrationBook` has no constructor arguments.

| Method | Kind | Purpose |
|---|---|---|
| `ask(text, closes_at, resolver)` | write | Returns `{"id": n}`. |
| `forecast(question_id, probability_permille)` | write | One forecast, final. |
| `resolve(question_id, outcome_yes)` | write | The resolver settles YES (`true`) or NO (`false`). |
| `void(question_id)` | write | Take the question out of every record. |
| `get_question_json(question_id)` | view | The question, its state and, once it is closed, every forecast; `NONE` if unknown. |
| `get_record_json(address)` | view | Resolved, pending and void counts, mean brier, skill, buckets, distinct resolvers, every scored forecast; `NONE` if the address never forecast. |
| `get_board_json(min_resolved)` | view | The ranking described above. |
| `get_question_count()`, `get_questions_page(offset, limit)` | view | List the questions (pages of at most 50). |

## What the record does not show

These are limits of the idea, not bugs.

* **The resolver is trusted.** The contract cannot know what really happened. A resolver who lies decides the outcome. The record
  shows the resolver of every question and `distinct_resolvers` per forecaster, so a record settled by one address reads
  differently from one settled by many.
* **Collusion is possible.** A resolver and a forecaster who work together can fake a good record. The contract only prevents the
  resolver from forecasting on their own question.
* **New addresses are free.** Anyone can start again under another address, so a bad record can be abandoned. A good record proves
  nothing about the person behind it.
* **Questions are chosen by the people who ask.** Forecasting only easy questions looks good. Look at how many questions and how many
  resolvers stand behind a skill number; the board takes a minimum for that reason.
* **Skill rewards a few lucky calls.** One correct 99% forecast gives a high skill. Read it together with the number of resolved forecasts.
* **Time comes from the transaction.** Closing and settling use the network's timestamp for the transaction, not a clock you control.
* No funds are held and nothing is paid out. This is a record, not a market.

## Tests

Run from the repository root (Python 3.12+, `pip install genlayer-test`).

```
python3 -m pytest tests/direct     # 118 tests, in memory (genlayer-test direct mode)
python3 -m pytest tests/glsim      # 3 tests on a local 5-validator network (pip install glsim fastapi uvicorn)
python3 scripts/mutation_check.py  # 53 deliberate breakages; the tests must notice every one
```

The expected scores come from a second implementation written with exact fractions (`ref_brier`, `ref_record` in the test file),
not from the contract's own formulas. Random books (25 seeded cases with several forecasters, resolvers and voided questions) are
compared with it cell by cell. The outputs of the runs are in `evidence/`.

Not covered: real validators (the tests run in memory or on a local simulator), real wallets, any model (there is none).

## Live run

None yet. The contract has not been deployed to Studionet or any other public network, so there is no address and there are no
transaction hashes. What exists: 118 in-memory tests, 3 tests on a local 5-validator simulator, and the mutation check (outputs in
`evidence/`). Nothing here shows how the contract behaves on a public network, with real wallets, or under real validator timing.

## Try it

Open [GenLayer Studio](https://studio.genlayer.com), load `contracts/calibration_book.py`, deploy it with no constructor
arguments, then call `ask` with a closing time a few minutes ahead (UTC, `2026-12-31T12:00:00Z` style), `forecast` from one or two
other accounts, and `resolve` from the resolver account after the closing time.

## Where each claim is in the code

| Claim | Where |
|---|---|
| One forecast per address and question, none after closing, none by the resolver | `CalibrationBook.forecast` |
| Only the resolver settles, only after closing, once | `CalibrationBook.resolve` |
| Void rules (resolver at once, anyone after 30 days) | `CalibrationBook.void` |
| Forecasts of an open question are hidden | `CalibrationBook.get_question_json` |
| Brier, skill, buckets, distinct resolvers | `_brier`, `_bucket`, `_record` |
| Board order and minimum | `CalibrationBook.get_board_json` |
| Time is read from the transaction timestamp and parsed by hand | `_now` / `_timestamp` |

## Licence

MIT, see `LICENSE`.
