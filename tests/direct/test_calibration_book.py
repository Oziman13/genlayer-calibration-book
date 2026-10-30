"""Calibration Book v0.1 tests (genlayer-test direct mode, in-memory; the contract uses no model and no web).

Run from the repository root:  python3 -m pytest tests/direct
The expected scores come from a second implementation written with fractions, not from the contract's formulas.
"""

import json
import random
import sys
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from math import floor

import pytest
from gltest.direct import create_address

CONTRACT = "contracts/calibration_book.py"
T0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)
HOUR, DAY = 3600, 86400


def iso(seconds_from_t0):
    return (T0 + timedelta(seconds=seconds_from_t0)).strftime("%Y-%m-%dT%H:%M:%SZ")


def set_time(vm, seconds=0):
    stamp = iso(seconds)
    vm.warp(stamp)
    module = sys.modules.get("genlayer.gl")
    if module is not None:
        module.message_raw["datetime"] = stamp


def acct(name):
    return create_address(name)


def low(address):
    """The address as the contract spells it: 0x, 40 lower-case hex digits."""
    return "0x" + bytes(address).hex() if isinstance(address, (bytes, bytearray)) else str(address).lower()


def deploy(vm, deploy_fn, owner):
    vm.sender = owner
    set_time(vm, 0)
    c = deploy_fn(CONTRACT)
    set_time(vm, 0)
    return c


def ask(vm, c, who, text="Will the test network have more than 100 validators by the end of the year?",
        closes=DAY, resolver="", now=0):
    vm.sender = who
    set_time(vm, now)
    out = json.loads(c.ask(text, iso(closes), resolver if isinstance(resolver, str) else low(resolver)))
    return out["id"]


def forecast(vm, c, who, qid, p, now=HOUR):
    vm.sender = who
    set_time(vm, now)
    c.forecast(qid, p)


def resolve(vm, c, who, qid, yes, now=2 * DAY):
    vm.sender = who
    set_time(vm, now)
    c.resolve(qid, yes)


def record(c, who):
    raw = c.get_record_json(low(who))
    return None if raw == "NONE" else json.loads(raw)


# -- an independent scoring implementation ------------------------------------------------------------------------------

def ref_brier(p, outcome):
    return floor(Fraction((p - 1000 * outcome) ** 2, 1000))


def ref_bucket(p):
    for i, top in enumerate((200, 400, 600, 800, 999)):
        if p <= top:
            return i


def ref_record(entries):
    """entries: list of (p, outcome, resolver)."""
    n = len(entries)
    total = sum(ref_brier(p, o) for p, o, _ in entries)
    out = {"resolved": n, "mean_brier": floor(Fraction(total, n)) if n else None,
           "skill": floor(Fraction(1000 * (250 * n - total), 250 * n)) if n else None,
           "resolvers": len({r for _, _, r in entries})}
    buckets = []
    for b in range(5):
        items = [(p, o) for p, o, _ in entries if ref_bucket(p) == b]
        buckets.append((len(items),
                        floor(Fraction(sum(p for p, _ in items), len(items))) if items else None,
                        floor(Fraction(1000 * sum(o for _, o in items), len(items))) if items else None))
    out["buckets"] = buckets
    return out


def check_record(c, who, entries):
    got = record(c, who)
    ref = ref_record(entries)
    assert got["resolved"] == ref["resolved"]
    assert got["mean_brier"] == ref["mean_brier"] and got["skill_permille"] == ref["skill"]
    assert got["distinct_resolvers"] == ref["resolvers"]
    assert [(b["n"], b["avg_forecast_permille"], b["observed_yes_permille"]) for b in got["buckets"]] == ref["buckets"]


# -- deployment and empty views ---------------------------------------------------------------------------------------

def test_deploy_is_empty(direct_vm, direct_deploy, direct_owner):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    assert c.get_question_count() == 0
    assert json.loads(c.get_questions_page(0, 10)) == {"items": [], "total": 0}
    assert c.get_question_json(0) == "NONE" and c.get_question_json(-1) == "NONE"
    assert c.get_record_json(low(direct_owner)) == "NONE"
    assert json.loads(c.get_board_json(1)) == []


# -- asking ---------------------------------------------------------------------------------------------------------------

def test_ask_stores_the_question_and_defaults_the_resolver_to_the_asker(direct_vm, direct_deploy, direct_owner, direct_alice):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    qid = ask(direct_vm, c, direct_alice, text="  Will   it rain\n in Istanbul on the first of December?  ")
    assert qid == 0 and c.get_question_count() == 1
    q = json.loads(c.get_question_json(0))
    assert q["text"] == "Will it rain in Istanbul on the first of December?"
    assert q["asker"] == q["resolver"] == low(direct_alice)
    assert q["state"] == "OPEN" and q["outcome"] is None and q["forecast_count"] == 0 and q["forecasts"] is None
    assert q["opened"] == int(T0.timestamp()) and q["closes"] == int(T0.timestamp()) + DAY


def test_ask_can_name_another_resolver_in_any_letter_case(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    ask(direct_vm, c, direct_alice, resolver="  " + low(direct_bob).upper().replace("0X", "0x") + " ")
    assert json.loads(c.get_question_json(0))["resolver"] == low(direct_bob)


@pytest.mark.parametrize("text", ["short?", "x" * 241, "", "   ", "A question with a control\x07 character in it", 12345])
def test_bad_question_text_is_refused(direct_vm, direct_deploy, direct_owner, text):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    with direct_vm.expect_revert("invalid question"):
        c.ask(text, iso(DAY), "")


def test_question_length_limits_are_inclusive(direct_vm, direct_deploy, direct_owner):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    c.ask("x" * 15, iso(DAY), "")
    c.ask("y" * 240, iso(DAY), "")
    assert c.get_question_count() == 2


@pytest.mark.parametrize("seconds,ok", [(59, False), (60, True), (400 * DAY, True), (400 * DAY + 1, False), (-DAY, False), (0, False)])
def test_closing_time_window(direct_vm, direct_deploy, direct_owner, seconds, ok):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    if ok:
        c.ask("Will this closing time be accepted by the book?", iso(seconds), "")
    else:
        with direct_vm.expect_revert("closing time out of range"):
            c.ask("Will this closing time be accepted by the book?", iso(seconds), "")


@pytest.mark.parametrize("bad", ["", "tomorrow", "2026-11-02", "2026-13-02T10:00:00Z", "2026-02-30T10:00:00Z", "2026-11-02T25:00:00Z",
                                 "2026-11-02T10:00:00+0100", "2026-11-02T10:00:60Z", "2026/11/02T10:00:00Z", 5])
def test_unreadable_closing_times_are_refused(direct_vm, direct_deploy, direct_owner, bad):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    with direct_vm.expect_revert("unreadable time"):
        c.ask("Will this closing time be readable by the book?", bad, "")


def test_time_zone_offsets_and_fractions_are_read(direct_vm, direct_deploy, direct_owner):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    c.ask("Will an offset time be read the same as a UTC time?", "2026-11-02T13:00:00.250+03:00", "")
    c.ask("Will an offset time be read the same as a UTC time?", "2026-11-02T10:00:00Z", "")
    a, b = (json.loads(c.get_question_json(i)) for i in (0, 1))
    assert a["closes"] == b["closes"]


def test_leap_day_is_a_valid_date(direct_vm, direct_deploy, direct_owner):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    c.ask("Will a leap day close this question in time?", "2027-02-28T10:00:00Z", "")
    with direct_vm.expect_revert("unreadable time"):
        c.ask("Will a leap day close this question in time?", "2027-02-29T10:00:00Z", "")


def test_a_century_year_that_is_not_divisible_by_400_has_no_leap_day(direct_vm, direct_deploy, direct_owner):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    with direct_vm.expect_revert("unreadable time"):
        c.ask("Will the year two thousand have a leap day in it?", "2100-02-29T00:00:00Z", "")


def test_a_negative_offset_is_added_not_subtracted(direct_vm, direct_deploy, direct_owner):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    c.ask("Will a western offset be read as later than UTC?", "2026-11-02T05:00:00-05:00", "")
    c.ask("Will a western offset be read as later than UTC?", "2026-11-02T10:00:00Z", "")
    a, b = (json.loads(c.get_question_json(i)) for i in (0, 1))
    assert a["closes"] == b["closes"] == int(T0.timestamp()) + DAY


@pytest.mark.parametrize("resolver", ["0x1234", "alice", "0x" + "g" * 40, "0x" + "a" * 41, 7])
def test_bad_resolver_is_refused(direct_vm, direct_deploy, direct_owner, resolver):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    with direct_vm.expect_revert("invalid address"):
        c.ask("Will this resolver address be accepted by the book?", iso(DAY), resolver)


def test_the_book_holds_one_hundred_questions(direct_vm, direct_deploy, direct_owner):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    for _ in range(100):
        c.ask("Will the hundredth question still fit in the book?", iso(DAY), "")
    with direct_vm.expect_revert("the book is full"):
        c.ask("Will the hundred and first question fit in the book?", iso(DAY), "")


# -- forecasting ----------------------------------------------------------------------------------------------------------

def test_forecasts_are_hidden_until_the_question_is_settled(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    qid = ask(direct_vm, c, direct_owner)
    forecast(direct_vm, c, direct_alice, qid, 700)
    q = json.loads(c.get_question_json(qid))
    assert q["forecast_count"] == 1 and q["forecasts"] is None
    assert "700" not in c.get_question_json(qid)
    resolve(direct_vm, c, direct_owner, qid, True)
    assert json.loads(c.get_question_json(qid))["forecasts"] == {low(direct_alice): 700}


@pytest.mark.parametrize("p", [0, 1000, -5, 10 ** 6, True, "500", None])
def test_probability_must_be_one_to_nine_ninety_nine(direct_vm, direct_deploy, direct_owner, direct_alice, p):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    qid = ask(direct_vm, c, direct_owner)
    direct_vm.sender = direct_alice
    set_time(direct_vm, HOUR)
    with direct_vm.expect_revert("probability must be 1 to 999"):
        c.forecast(qid, p)


@pytest.mark.parametrize("p", [1, 999])
def test_the_extreme_probabilities_are_allowed(direct_vm, direct_deploy, direct_owner, direct_alice, p):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    qid = ask(direct_vm, c, direct_owner)
    forecast(direct_vm, c, direct_alice, qid, p)
    assert json.loads(c.get_record_json(low(direct_alice)))["pending"] == 1


def test_one_forecast_per_address_and_question(direct_vm, direct_deploy, direct_owner, direct_alice):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    qid = ask(direct_vm, c, direct_owner)
    forecast(direct_vm, c, direct_alice, qid, 400)
    with direct_vm.expect_revert("already forecast"):
        forecast(direct_vm, c, direct_alice, qid, 600)
    assert record(c, direct_alice)["pending"] == 1


def test_the_resolver_cannot_forecast_but_the_asker_can(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    qid = ask(direct_vm, c, direct_alice, resolver=low(direct_bob))
    with direct_vm.expect_revert("the resolver cannot forecast"):
        forecast(direct_vm, c, direct_bob, qid, 500)
    forecast(direct_vm, c, direct_alice, qid, 500)


def test_no_forecast_at_or_after_the_closing_time(direct_vm, direct_deploy, direct_owner, direct_alice):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    qid = ask(direct_vm, c, direct_owner)
    forecast(direct_vm, c, direct_alice, qid, 500, now=DAY - 1)
    with direct_vm.expect_revert("question is closed"):
        forecast(direct_vm, c, acct("late"), qid, 500, now=DAY)
    with direct_vm.expect_revert("question is closed"):
        forecast(direct_vm, c, acct("later"), qid, 500, now=DAY + 5)


def test_no_forecast_on_a_voided_or_settled_question(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    a = ask(direct_vm, c, direct_owner)
    b = ask(direct_vm, c, direct_owner)
    direct_vm.sender = direct_owner
    set_time(direct_vm, 2 * DAY)
    c.resolve(a, True)
    c.void(b)
    for qid in (a, b):
        with direct_vm.expect_revert("question is closed"):
            forecast(direct_vm, c, direct_alice, qid, 500, now=2 * DAY)


def test_unknown_questions_are_refused(direct_vm, direct_deploy, direct_owner, direct_alice):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    direct_vm.sender = direct_alice
    for qid in (0, 5, -1, "0"):
        with direct_vm.expect_revert("unknown question"):
            c.forecast(qid, 500)
        with direct_vm.expect_revert("unknown question"):
            c.resolve(qid, True)
        with direct_vm.expect_revert("unknown question"):
            c.void(qid)


def test_a_question_takes_forty_forecasts(direct_vm, direct_deploy, direct_owner):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    qid = ask(direct_vm, c, direct_owner)
    for i in range(40):
        forecast(direct_vm, c, acct("f%d" % i), qid, 100 + i)
    with direct_vm.expect_revert("too many forecasts"):
        forecast(direct_vm, c, acct("f40"), qid, 500)
    assert json.loads(c.get_question_json(qid))["forecast_count"] == 40


def test_the_book_takes_one_hundred_forecasters(direct_vm, direct_deploy, direct_owner):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    for q in range(3):
        ask(direct_vm, c, direct_owner)
    for i in range(100):
        forecast(direct_vm, c, acct("g%d" % i), i % 3, 300)
    with direct_vm.expect_revert("too many forecasters"):
        forecast(direct_vm, c, acct("g100"), 0, 300)
    forecast(direct_vm, c, acct("g0"), 1, 300)       # an address that is already in the book is never turned away


# -- settling ---------------------------------------------------------------------------------------------------------------

def test_only_the_resolver_settles_and_only_after_closing(direct_vm, direct_deploy, direct_owner, direct_alice, direct_bob):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    qid = ask(direct_vm, c, direct_owner, resolver=low(direct_bob))
    with direct_vm.expect_revert("not closed yet"):
        resolve(direct_vm, c, direct_bob, qid, True, now=DAY - 1)
    with direct_vm.expect_revert("only the resolver can settle"):
        resolve(direct_vm, c, direct_alice, qid, True)
    with direct_vm.expect_revert("only the resolver can settle"):
        resolve(direct_vm, c, direct_owner, qid, True)
    resolve(direct_vm, c, direct_bob, qid, False, now=DAY)
    q = json.loads(c.get_question_json(qid))
    assert q["state"] == "RESOLVED" and q["outcome"] == 0 and q["settled"] == int(T0.timestamp()) + DAY


def test_settling_is_final(direct_vm, direct_deploy, direct_owner):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    qid = ask(direct_vm, c, direct_owner)
    resolve(direct_vm, c, direct_owner, qid, True)
    with direct_vm.expect_revert("already settled"):
        resolve(direct_vm, c, direct_owner, qid, False)
    with direct_vm.expect_revert("already settled"):
        direct_vm.sender = direct_owner
        c.void(qid)
    assert json.loads(c.get_question_json(qid))["outcome"] == 1


@pytest.mark.parametrize("value", [1, 0, "yes", None])
def test_the_outcome_must_be_a_real_boolean(direct_vm, direct_deploy, direct_owner, value):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    qid = ask(direct_vm, c, direct_owner)
    direct_vm.sender = direct_owner
    set_time(direct_vm, 2 * DAY)
    with direct_vm.expect_revert("outcome must be true or false"):
        c.resolve(qid, value)


# -- voiding --------------------------------------------------------------------------------------------------------------

def test_the_resolver_can_void_a_closed_question_and_it_leaves_every_record(direct_vm, direct_deploy, direct_owner, direct_alice):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    a, b = ask(direct_vm, c, direct_owner), ask(direct_vm, c, direct_owner)
    forecast(direct_vm, c, direct_alice, a, 900)
    forecast(direct_vm, c, direct_alice, b, 800)
    with direct_vm.expect_revert("not closed yet"):
        direct_vm.sender = direct_owner
        set_time(direct_vm, DAY - 1)
        c.void(a)
    resolve(direct_vm, c, direct_owner, a, True)
    direct_vm.sender = direct_owner
    set_time(direct_vm, 2 * DAY)
    c.void(b)
    rec = record(c, direct_alice)
    assert rec["resolved"] == 1 and rec["void"] == 1 and rec["pending"] == 0
    check_record(c, direct_alice, [(900, 1, low(direct_owner))])
    assert json.loads(c.get_question_json(b))["state"] == "VOID"


def test_anyone_may_void_after_thirty_days_but_not_before(direct_vm, direct_deploy, direct_owner, direct_alice):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    qid = ask(direct_vm, c, direct_owner)
    direct_vm.sender = direct_alice
    set_time(direct_vm, DAY + 30 * DAY - 1)
    with direct_vm.expect_revert("only the resolver can void yet"):
        c.void(qid)
    set_time(direct_vm, DAY + 30 * DAY)
    c.void(qid)
    assert json.loads(c.get_question_json(qid))["state"] == "VOID"


# -- scoring --------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("p,outcome,expected", [(1000 - 1, 1, 0), (999, 0, 998), (500, 1, 250), (500, 0, 250), (1, 1, 998),
                                                 (800, 1, 40), (800, 0, 640), (200, 1, 640), (333, 1, 444), (667, 0, 444)])
def test_single_forecast_scores(direct_vm, direct_deploy, direct_owner, direct_alice, p, outcome, expected):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    qid = ask(direct_vm, c, direct_owner)
    forecast(direct_vm, c, direct_alice, qid, p)
    resolve(direct_vm, c, direct_owner, qid, bool(outcome))
    rec = record(c, direct_alice)
    assert rec["forecasts"][0]["brier"] == ref_brier(p, outcome) == expected
    check_record(c, direct_alice, [(p, outcome, low(direct_owner))])


def test_a_coin_flip_forecaster_scores_zero_skill(direct_vm, direct_deploy, direct_owner, direct_alice):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    for outcome in (True, False, True):
        qid = ask(direct_vm, c, direct_owner)
        forecast(direct_vm, c, direct_alice, qid, 500)
        resolve(direct_vm, c, direct_owner, qid, outcome)
    rec = record(c, direct_alice)
    assert rec["mean_brier"] == 250 and rec["skill_permille"] == 0


def test_a_confident_wrong_forecaster_has_negative_skill(direct_vm, direct_deploy, direct_owner, direct_alice):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    qid = ask(direct_vm, c, direct_owner)
    forecast(direct_vm, c, direct_alice, qid, 950)
    resolve(direct_vm, c, direct_owner, qid, False)
    rec = record(c, direct_alice)
    assert rec["mean_brier"] == 902 and rec["skill_permille"] == -2608 and ref_record([(950, 0, "x")])["skill"] == -2608


def test_pending_forecasts_are_counted_but_not_scored(direct_vm, direct_deploy, direct_owner, direct_alice):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    a, b = ask(direct_vm, c, direct_owner), ask(direct_vm, c, direct_owner, closes=10 * DAY)
    forecast(direct_vm, c, direct_alice, a, 600)
    forecast(direct_vm, c, direct_alice, b, 600)
    resolve(direct_vm, c, direct_owner, a, True)
    rec = record(c, direct_alice)
    assert rec["resolved"] == 1 and rec["pending"] == 1
    assert record(c, acct("nobody")) is None


def test_a_record_with_nothing_settled_has_no_scores(direct_vm, direct_deploy, direct_owner, direct_alice):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    forecast(direct_vm, c, direct_alice, ask(direct_vm, c, direct_owner), 600)
    rec = record(c, direct_alice)
    assert rec["resolved"] == 0 and rec["mean_brier"] is None and rec["skill_permille"] is None
    assert all(b["n"] == 0 and b["avg_forecast_permille"] is None for b in rec["buckets"])


@pytest.mark.parametrize("p,bucket", [(1, 0), (200, 0), (201, 1), (400, 1), (401, 2), (600, 2), (601, 3), (800, 3), (801, 4), (999, 4)])
def test_bucket_edges(direct_vm, direct_deploy, direct_owner, direct_alice, p, bucket):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    qid = ask(direct_vm, c, direct_owner)
    forecast(direct_vm, c, direct_alice, qid, p)
    resolve(direct_vm, c, direct_owner, qid, True)
    buckets = record(c, direct_alice)["buckets"]
    assert [b["n"] for b in buckets] == [1 if i == bucket else 0 for i in range(5)]
    assert ref_bucket(p) == bucket


def test_calibration_shows_overconfidence(direct_vm, direct_deploy, direct_owner, direct_alice):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    for outcome in (True, True, False, False):          # says 90% each time, right half of the time
        qid = ask(direct_vm, c, direct_owner)
        forecast(direct_vm, c, direct_alice, qid, 900)
        resolve(direct_vm, c, direct_owner, qid, outcome)
    top = record(c, direct_alice)["buckets"][4]
    assert top == {"range": "801-999", "n": 4, "avg_forecast_permille": 900, "observed_yes_permille": 500}


@pytest.mark.parametrize("seed", range(25))
def test_random_books_match_the_reference(direct_vm, direct_deploy, direct_owner, seed):
    rnd = random.Random(seed)
    c = deploy(direct_vm, direct_deploy, direct_owner)
    people = [acct("p%d-%d" % (seed, i)) for i in range(rnd.randint(2, 5))]
    resolvers = [acct("r%d-%d" % (seed, i)) for i in range(rnd.randint(1, 3))]
    expected = {low(p): [] for p in people}
    for _ in range(rnd.randint(4, 10)):
        res = rnd.choice(resolvers)
        qid = ask(direct_vm, c, direct_owner, resolver=low(res))
        outcome = rnd.random() < 0.5
        voided = rnd.random() < 0.15
        given = {}
        for person in people:
            if rnd.random() < 0.75:
                p = rnd.choice([rnd.randint(1, 999), 1, 999, 500, 200, 201])
                forecast(direct_vm, c, person, qid, p)
                given[low(person)] = p
        direct_vm.sender = res
        set_time(direct_vm, 2 * DAY)
        if voided:
            c.void(qid)
        else:
            c.resolve(qid, outcome)
            for who, p in given.items():
                expected[who].append((p, 1 if outcome else 0, low(res)))
    for person in people:
        got = record(c, person)
        if got is None:
            assert not expected[low(person)]
            continue
        if expected[low(person)]:
            check_record(c, person, expected[low(person)])
        else:
            assert got["resolved"] == 0 and got["skill_permille"] is None


# -- the board --------------------------------------------------------------------------------------------------------------

def test_the_board_ranks_by_skill_then_volume_then_address(direct_vm, direct_deploy, direct_owner):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    sharp, dull, mid, lone = acct("sharp"), acct("dull"), acct("mid"), acct("lone")
    for outcome in (True, False, True):
        qid = ask(direct_vm, c, direct_owner)
        forecast(direct_vm, c, sharp, qid, 900 if outcome else 100)       # brier 10 every time, skill 960 over 3
        forecast(direct_vm, c, dull, qid, 500)                            # brier 250, skill 0
        forecast(direct_vm, c, mid, qid, 700 if outcome else 300)         # brier 90, skill 640
        if outcome:
            forecast(direct_vm, c, lone, qid, 900)                        # brier 10 twice: skill 960 over 2
        resolve(direct_vm, c, direct_owner, qid, outcome)
    board = json.loads(c.get_board_json(1))
    assert [(r["address"], r["resolved"], r["skill_permille"]) for r in board] == [
        (low(sharp), 3, 960), (low(lone), 2, 960), (low(mid), 3, 640), (low(dull), 3, 0)]
    assert [r["address"] for r in json.loads(c.get_board_json(3))] == [low(sharp), low(mid), low(dull)]
    assert json.loads(c.get_board_json(4)) == []
    assert board[0]["mean_brier"] == 10 and board[0]["distinct_resolvers"] == 1


def test_equal_skill_is_ordered_by_volume_then_address(direct_vm, direct_deploy, direct_owner):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    x, y, z = acct("x"), acct("y"), acct("z")
    for i in range(3):
        qid = ask(direct_vm, c, direct_owner)
        for who in (x, y, z):
            if who == z and i == 2:
                continue
            forecast(direct_vm, c, who, qid, 500)
        resolve(direct_vm, c, direct_owner, qid, True)
    board = json.loads(c.get_board_json(1))
    assert all(r["skill_permille"] == 0 for r in board)
    assert [r["address"] for r in board] == sorted([low(x), low(y)]) + [low(z)]


def test_bad_board_minimums_are_refused(direct_vm, direct_deploy, direct_owner):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    for bad in (0, -1, 1001, "1", True):
        with direct_vm.expect_revert("bad minimum"):
            c.get_board_json(bad)


def test_the_board_shows_at_most_fifty_rows(direct_vm, direct_deploy, direct_owner):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    first, second = ask(direct_vm, c, direct_owner), ask(direct_vm, c, direct_owner)
    for i in range(40):
        forecast(direct_vm, c, acct("b%d" % i), first, 100 + 10 * i)
    for i in range(20, 60):
        forecast(direct_vm, c, acct("b%d" % i), second, 100 + 10 * i)
    resolve(direct_vm, c, direct_owner, first, True)
    resolve(direct_vm, c, direct_owner, second, True)
    assert c.get_board_json(1).count('"address"') == 50
    assert len(json.loads(c.get_board_json(2))) == 20


# -- reading --------------------------------------------------------------------------------------------------------------

def test_pages(direct_vm, direct_deploy, direct_owner):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    for i in range(5):
        c.ask("Will page test question number %d be listed?" % i, iso(DAY), "")
    page = json.loads(c.get_questions_page(1, 2))
    assert page["total"] == 5 and [q["id"] for q in page["items"]] == [1, 2]
    assert all("forecasts" not in q for q in page["items"])
    assert json.loads(c.get_questions_page(4, 50))["items"][0]["id"] == 4
    assert json.loads(c.get_questions_page(9, 5))["items"] == []
    for offset, limit in ((-1, 5), (0, 0), (0, 51), ("0", 5)):
        with direct_vm.expect_revert("bad page"):
            c.get_questions_page(offset, limit)


def test_record_lookup_rejects_malformed_addresses(direct_vm, direct_deploy, direct_owner):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    with direct_vm.expect_revert("invalid address"):
        c.get_record_json("0x12")


def test_a_failed_call_changes_nothing(direct_vm, direct_deploy, direct_owner, direct_alice):
    c = deploy(direct_vm, direct_deploy, direct_owner)
    qid = ask(direct_vm, c, direct_owner)
    forecast(direct_vm, c, direct_alice, qid, 400)
    before = c.get_question_json(qid), c.get_record_json(low(direct_alice)), c.get_question_count()
    with direct_vm.expect_revert("already forecast"):
        forecast(direct_vm, c, direct_alice, qid, 800)
    with direct_vm.expect_revert("invalid question"):
        c.ask("x", iso(DAY), "")
    assert (c.get_question_json(qid), c.get_record_json(low(direct_alice)), c.get_question_count()) == before
