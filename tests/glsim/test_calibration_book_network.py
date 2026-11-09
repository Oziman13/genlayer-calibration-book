"""Calibration Book on a local GenLayer network (glsim, 5 validators). Questions are asked, forecast and scored through
real consensus rounds, and a question can be settled from a named web page: the leader and four validators each fetch the
page and read it with a (mocked) model, and the contract keeps the verdict only when they agree. Mocks stay installed for
a whole simulator run, so each test that needs them uses its own simulator.
"""
import json

import pytest

from rpc import RpcError, Sim

ASKER = "0x1111111111111111111111111111111111111111"
RESOLVER = "0x2222222222222222222222222222222222222222"
ALICE = "0x3333333333333333333333333333333333333333"
BOB = "0x4444444444444444444444444444444444444444"
T0 = "2026-11-01T10:00:00Z"
CLOSES = "2026-11-02T10:00:00Z"
AFTER = "2026-11-02T10:00:01Z"
TEXT = "Will the test network have more than 100 validators by the end of the year?"


@pytest.fixture
def sim():
    simulator = Sim()
    try:
        yield simulator
    finally:
        simulator.close()


def start(sim):
    sim.set_time(T0)
    contract = sim.deploy("contracts/calibration_book.py", [], ASKER)
    qid = json.loads(sim.call(contract, "ask", [TEXT, CLOSES, RESOLVER, "", 0], ASKER))["id"]
    return contract, qid


def test_a_question_is_asked_forecast_settled_and_scored(sim):
    contract, qid = start(sim)
    sim.call(contract, "forecast", [qid, 800], ALICE)
    sim.call(contract, "forecast", [qid, 300], BOB)
    hidden = sim.read_json(contract, "get_question_json", [qid])
    assert hidden["forecast_count"] == 2 and hidden["forecasts"] is None and hidden["state"] == "OPEN"
    sim.set_time(AFTER)
    sim.call(contract, "resolve", [qid, True], RESOLVER)
    settled = sim.read_json(contract, "get_question_json", [qid])
    assert settled["state"] == "RESOLVED" and settled["outcome"] == 1
    assert settled["forecasts"] == {ALICE: 800, BOB: 300}
    alice = sim.read_json(contract, "get_record_json", [ALICE])
    bob = sim.read_json(contract, "get_record_json", [BOB])
    assert (alice["mean_brier"], alice["skill_permille"]) == (40, 840)         # (800 - 1000)^2 // 1000 = 40
    assert (bob["mean_brier"], bob["skill_permille"]) == (490, -960)           # (300 - 1000)^2 // 1000 = 490
    board = sim.read_json(contract, "get_board_json", [1])
    assert [row["address"] for row in board] == [ALICE, BOB]


def test_the_rules_hold_on_the_network(sim):
    contract, qid = start(sim)
    with pytest.raises(RpcError):
        sim.call(contract, "forecast", [qid, 500], RESOLVER)        # the resolver cannot forecast
    with pytest.raises(RpcError):
        sim.call(contract, "forecast", [qid, 1000], ALICE)          # out of range
    sim.call(contract, "forecast", [qid, 500], ALICE)
    with pytest.raises(RpcError):
        sim.call(contract, "forecast", [qid, 600], ALICE)           # once only
    with pytest.raises(RpcError):
        sim.call(contract, "resolve", [qid, True], RESOLVER)        # not closed yet
    sim.set_time(AFTER)
    with pytest.raises(RpcError):
        sim.call(contract, "resolve", [qid, True], ALICE)           # not the resolver
    with pytest.raises(RpcError):
        sim.call(contract, "forecast", [qid, 500], BOB)             # closed
    sim.call(contract, "resolve", [qid, False], RESOLVER)
    with pytest.raises(RpcError):
        sim.call(contract, "resolve", [qid, True], RESOLVER)        # final


def test_a_voided_question_leaves_the_record(sim):
    contract, qid = start(sim)
    sim.call(contract, "forecast", [qid, 900], ALICE)
    sim.set_time(AFTER)
    sim.call(contract, "void", [qid], RESOLVER)
    record = sim.read_json(contract, "get_record_json", [ALICE])
    assert record["resolved"] == 0 and record["void"] == 1 and record["skill_permille"] is None


SOURCE = "https://records.example.org/results/validators-2026.txt"
PAGE_YES = "Network status report. The test network reached 143 validators on 15 December 2026. All checks completed."
PAGE_NO = "Network status report. The test network stayed at 64 validators through 31 December 2026. All checks completed."
LATER = "2026-11-03T10:00:01Z"                  # closing time + one day of grace + one second


def source_mocks(sim, page, reply):
    sim.rpc("sim_installMocks",
            web_mocks={r"https://records\.example\.org/.*": {"method": "GET", "response": {"status": 200, "headers": {}, "body": page}}},
            llm_mocks={"UNTRUSTED DATA": reply})


def start_sourced(sim):
    sim.set_time(T0)
    contract = sim.deploy("contracts/calibration_book.py", [], ASKER)
    qid = json.loads(sim.call(contract, "ask", [TEXT, CLOSES, RESOLVER, SOURCE, 86400], ASKER))["id"]
    sim.call(contract, "forecast", [qid, 800], ALICE)
    sim.call(contract, "forecast", [qid, 300], BOB)
    return contract, qid


def test_a_question_is_settled_from_its_source_by_the_validators(sim):
    contract, qid = start_sourced(sim)
    source_mocks(sim, PAGE_YES, json.dumps({"verdict": "YES", "quote": "reached 143 validators on 15 December 2026"}))
    sim.set_time(AFTER)
    with pytest.raises(RpcError):
        sim.call(contract, "settle_from_source", [qid], BOB)               # the resolver still has the grace period
    sim.set_time(LATER)
    assert json.loads(sim.call(contract, "settle_from_source", [qid], BOB)) == {"verdict": "YES"}
    question = sim.read_json(contract, "get_question_json", [qid])
    assert question["state"] == "RESOLVED" and question["outcome"] == 1 and question["settled_by"] == "SOURCE"
    assert question["evidence"] == {"quote": "reached 143 validators on 15 December 2026", "source": SOURCE}
    alice = sim.read_json(contract, "get_record_json", [ALICE])
    assert (alice["mean_brier"], alice["settled_from_source"]) == (40, 1)
    with pytest.raises(RpcError):
        sim.call(contract, "resolve", [qid, False], RESOLVER)               # the source settlement is final


def test_a_no_from_the_source_scores_the_other_way(sim):
    contract, qid = start_sourced(sim)
    source_mocks(sim, PAGE_NO, json.dumps({"verdict": "NO", "quote": "stayed at 64 validators through 31 December 2026"}))
    sim.set_time(LATER)
    sim.call(contract, "settle_from_source", [qid], ALICE)
    assert sim.read_json(contract, "get_question_json", [qid])["outcome"] == 0
    assert sim.read_json(contract, "get_record_json", [BOB])["mean_brier"] == 90          # (300 - 0)^2 // 1000


def test_a_quotation_that_is_not_on_the_page_settles_nothing(sim):
    contract, qid = start_sourced(sim)
    source_mocks(sim, PAGE_YES, json.dumps({"verdict": "YES", "quote": "reached 999 validators on 15 December 2026"}))
    sim.set_time(LATER)
    with pytest.raises(RpcError):
        sim.call(contract, "settle_from_source", [qid], BOB)
    assert sim.read_json(contract, "get_question_json", [qid])["state"] == "OPEN"


def test_an_undecided_page_leaves_the_question_open_for_the_resolver(sim):
    contract, qid = start_sourced(sim)
    source_mocks(sim, "Nothing has been published yet.", json.dumps({"verdict": "UNDECIDED", "quote": ""}))
    sim.set_time(LATER)
    assert json.loads(sim.call(contract, "settle_from_source", [qid], BOB)) == {"verdict": "UNDECIDED"}
    assert sim.read_json(contract, "get_question_json", [qid])["state"] == "OPEN"
    sim.call(contract, "resolve", [qid, True], RESOLVER)
    assert sim.read_json(contract, "get_question_json", [qid])["settled_by"] == "RESOLVER"
