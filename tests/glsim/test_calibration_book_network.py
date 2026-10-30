"""Calibration Book on a local GenLayer network (glsim, 5 validators). The contract uses no model, so every validator
computes the same answer; these tests show it runs through real consensus rounds and that the numbers match a hand count.
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
    qid = json.loads(sim.call(contract, "ask", [TEXT, CLOSES, RESOLVER], ASKER))["id"]
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
