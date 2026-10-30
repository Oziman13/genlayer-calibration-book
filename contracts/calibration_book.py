# v0.1.0
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
import json

# Calibration Book -- a public forecasting record that builds up address by address, question by question.
#
# Anyone can ask a yes/no question with a closing time and name who will settle it (the RESOLVER). Anyone except the
# resolver can give ONE forecast per question before it closes: the probability of YES, in permille (1..999). After
# the closing time the resolver settles the question YES or NO, or declares it VOID. For every settled question the
# contract scores each forecast with integer arithmetic and adds it to the forecaster's record:
#
#   brier       (p - 1000 * outcome)^2 // 1000, 0 is perfect, 250 is what always answering 500 would score, 1000 is worst
#   skill       how far the mean brier sits below that 250 baseline, in permille (negative = worse than a coin flip)
#   buckets     five probability bands, each with the average forecast and the share of YES outcomes (calibration)
#   resolvers   how many different resolvers settled the questions in the record
#
# There is no model and no web access: every number can be recomputed from the stored questions, so every validator
# reaches the identical result. Nothing is held or paid. What the record can and cannot show is in the README.

MIN_P, MAX_P = 1, 999
QUESTION_MIN, QUESTION_MAX = 15, 240
MAX_QUESTIONS = 100
MAX_FORECASTERS = 100
MAX_FORECASTS_PER_QUESTION = 40
MIN_HORIZON = 60                      # a question must close at least a minute after it is asked
MAX_HORIZON = 400 * 86400             # and at most 400 days after
VOID_AFTER = 30 * 86400               # anyone may void a question the resolver left open this long after closing
PAGE_LIMIT = 50
BOARD_LIMIT = 50
BUCKETS = ("1-200", "201-400", "401-600", "601-800", "801-999")
BASELINE_BRIER = 250


# -- strings, addresses, time ---------------------------------------------------------------------------------------

def _squash(text: str) -> str:
    return " ".join(text.split())


def _question_text(value) -> str:
    if not isinstance(value, str):
        raise gl.vm.UserError("invalid question")
    value = _squash(value)
    if not QUESTION_MIN <= len(value) <= QUESTION_MAX or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise gl.vm.UserError("invalid question")
    return value


def _address(value) -> str:
    if not isinstance(value, str):
        raise gl.vm.UserError("invalid address")
    value = value.strip().lower()
    if len(value) != 42 or not value.startswith("0x") or any(c not in "0123456789abcdef" for c in value[2:]):
        raise gl.vm.UserError("invalid address")
    return value


def _sender() -> str:
    return str(gl.message.sender_address).lower()


def _digits(text: str, size: int) -> bool:
    return len(text) == size and all(c in "0123456789" for c in text)


def _days_in_month(year: int, month: int) -> int:
    if month == 2:
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        return 29 if leap else 28
    return 30 if month in (4, 6, 9, 11) else 31


def _epoch_days(year: int, month: int, day: int) -> int:
    year -= 1 if month <= 2 else 0
    era = year // 400
    yoe = year - era * 400
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _timestamp(raw) -> int:
    """Parse YYYY-MM-DDTHH:MM:SS[.fff](Z|+hh:mm). Raises UserError for anything else."""
    if not isinstance(raw, str) or len(raw) < 19 or raw[4] != "-" or raw[7] != "-" or raw[10] not in ("T", " "):
        raise gl.vm.UserError("unreadable time")
    y, m, d, clock = raw[0:4], raw[5:7], raw[8:10], raw[11:19]
    if not (_digits(y, 4) and _digits(m, 2) and _digits(d, 2) and _digits(clock[0:2], 2) and clock[2] == ":"
            and _digits(clock[3:5], 2) and clock[5] == ":" and _digits(clock[6:8], 2)):
        raise gl.vm.UserError("unreadable time")
    year, month, day = int(y), int(m), int(d)
    if year < 1970 or not 1 <= month <= 12 or not 1 <= day <= _days_in_month(year, month):
        raise gl.vm.UserError("unreadable time")
    if int(clock[0:2]) > 23 or int(clock[3:5]) > 59 or int(clock[6:8]) > 59:
        raise gl.vm.UserError("unreadable time")
    rest = raw[19:]
    if rest.startswith("."):
        end = 1
        while end < len(rest) and rest[end] in "0123456789":
            end += 1
        rest = rest[end:]
    offset = 0
    if rest in ("Z", "z", ""):
        offset = 0
    elif len(rest) == 6 and rest[0] in "+-" and rest[3] == ":" and _digits(rest[1:3], 2) and _digits(rest[4:6], 2):
        offset = (int(rest[1:3]) * 3600 + int(rest[4:6]) * 60) * (1 if rest[0] == "+" else -1)
    else:
        raise gl.vm.UserError("unreadable time")
    return _epoch_days(year, month, day) * 86400 + int(clock[0:2]) * 3600 + int(clock[3:5]) * 60 + int(clock[6:8]) - offset


def _now() -> int:
    """Seconds since the epoch from the transaction's consensus timestamp."""
    return _timestamp(gl.message_raw["datetime"])


# -- scoring, integer arithmetic only ---------------------------------------------------------------------------------

def _brier(p: int, outcome: int) -> int:
    return (p - 1000 * outcome) ** 2 // 1000


def _bucket(p: int) -> int:
    return (p - 1) // 200


def _record(address: str, qids: list, questions: dict) -> dict:
    """The record of one forecaster, computed from the stored questions."""
    resolved, pending, voided = [], 0, 0
    for qid in qids:
        q = questions.get(qid)
        if q is None:
            continue
        p = q["forecasts"].get(address)
        if p is None:
            continue
        if q["state"] == "OPEN":
            pending += 1
        elif q["state"] == "VOID":
            voided += 1
        else:
            resolved.append({"question": qid, "p": p, "outcome": q["outcome"], "brier": _brier(p, q["outcome"]),
                             "resolver": q["resolver"]})
    n = len(resolved)
    total = sum(r["brier"] for r in resolved)
    buckets = []
    for b in range(5):
        items = [r for r in resolved if _bucket(r["p"]) == b]
        count = len(items)
        yes = sum(r["outcome"] for r in items)
        buckets.append({"range": BUCKETS[b], "n": count,
                        "avg_forecast_permille": sum(r["p"] for r in items) // count if count else None,
                        "observed_yes_permille": 1000 * yes // count if count else None})
    return {
        "address": address, "resolved": n, "pending": pending, "void": voided,
        "mean_brier": total // n if n else None,
        "skill_permille": 1000 * (BASELINE_BRIER * n - total) // (BASELINE_BRIER * n) if n else None,
        "distinct_resolvers": len({r["resolver"] for r in resolved}),
        "buckets": buckets,
        "forecasts": resolved,
    }


def _public_question(q: dict) -> dict:
    out = {k: q[k] for k in ("id", "text", "asker", "resolver", "opened", "closes", "state", "outcome", "settled")}
    out["forecast_count"] = len(q["forecasts"])
    return out


class CalibrationBook(gl.Contract):
    """A public, address-by-address forecasting record scored with integer arithmetic. Holds no funds."""
    questions: TreeMap[u32, str]
    question_count: u32
    forecasters: TreeMap[str, str]          # address -> JSON list of question ids
    forecaster_index: TreeMap[u32, str]
    forecaster_count: u32

    def __init__(self):
        self.question_count = u32(0)
        self.forecaster_count = u32(0)

    def _load(self, qid: int) -> dict:
        if type(qid) is not int or qid < 0 or qid >= int(self.question_count):
            raise gl.vm.UserError("unknown question")
        return json.loads(self.questions[u32(qid)])

    def _save(self, q: dict) -> None:
        self.questions[u32(q["id"])] = json.dumps(q, sort_keys=True)

    def _all_questions(self) -> dict:
        return {i: json.loads(self.questions[u32(i)]) for i in range(int(self.question_count))}

    @gl.public.write
    def ask(self, text: str, closes_at: str, resolver: str) -> str:
        """Ask a yes/no question. closes_at is an ISO time (2026-12-31T12:00:00Z); resolver is the address that will
        settle it (empty means the asker). Returns {"id": n}."""
        text = _question_text(text)
        now = _now()
        closes = _timestamp(closes_at)
        if not now + MIN_HORIZON <= closes <= now + MAX_HORIZON:
            raise gl.vm.UserError("closing time out of range")
        asker = _sender()
        settler = asker if resolver == "" else _address(resolver)
        qid = int(self.question_count)
        if qid >= MAX_QUESTIONS:
            raise gl.vm.UserError("the book is full")
        q = {"id": qid, "text": text, "asker": asker, "resolver": settler, "opened": now, "closes": closes,
             "state": "OPEN", "outcome": None, "settled": None, "forecasts": {}}
        self._save(q)
        self.question_count = u32(qid + 1)
        return json.dumps({"id": qid})

    @gl.public.write
    def forecast(self, question_id: int, probability_permille: int) -> None:
        """Give your one forecast on an open question: the probability of YES, 1 to 999 permille."""
        if type(probability_permille) is not int or not MIN_P <= probability_permille <= MAX_P:
            raise gl.vm.UserError("probability must be 1 to 999")
        q = self._load(question_id)
        who = _sender()
        if q["state"] != "OPEN" or _now() >= q["closes"]:
            raise gl.vm.UserError("question is closed")
        if who == q["resolver"]:
            raise gl.vm.UserError("the resolver cannot forecast")
        if who in q["forecasts"]:
            raise gl.vm.UserError("already forecast")
        if len(q["forecasts"]) >= MAX_FORECASTS_PER_QUESTION:
            raise gl.vm.UserError("too many forecasts")
        raw = self.forecasters.get(who)
        if raw is None:
            if int(self.forecaster_count) >= MAX_FORECASTERS:
                raise gl.vm.UserError("too many forecasters")
            self.forecaster_index[self.forecaster_count] = who
            self.forecaster_count = u32(int(self.forecaster_count) + 1)
            ids = []
        else:
            ids = json.loads(raw)
        ids.append(q["id"])
        self.forecasters[who] = json.dumps(ids)
        q["forecasts"][who] = probability_permille
        self._save(q)

    @gl.public.write
    def resolve(self, question_id: int, outcome_yes: bool) -> None:
        """The resolver settles a closed question YES (true) or NO (false). Final."""
        q = self._load(question_id)
        now = _now()
        if _sender() != q["resolver"]:
            raise gl.vm.UserError("only the resolver can settle")
        if q["state"] != "OPEN":
            raise gl.vm.UserError("already settled")
        if now < q["closes"]:
            raise gl.vm.UserError("not closed yet")
        if type(outcome_yes) is not bool:
            raise gl.vm.UserError("outcome must be true or false")
        q["state"] = "RESOLVED"
        q["outcome"] = 1 if outcome_yes else 0
        q["settled"] = now
        self._save(q)

    @gl.public.write
    def void(self, question_id: int) -> None:
        """Take a question out of every record. The resolver may do it once the question has closed; anyone may do it
        30 days after closing if it is still open."""
        q = self._load(question_id)
        now = _now()
        if q["state"] != "OPEN":
            raise gl.vm.UserError("already settled")
        if now < q["closes"]:
            raise gl.vm.UserError("not closed yet")
        if _sender() != q["resolver"] and now < q["closes"] + VOID_AFTER:
            raise gl.vm.UserError("only the resolver can void yet")
        q["state"] = "VOID"
        q["settled"] = now
        self._save(q)

    @gl.public.view
    def get_question_json(self, question_id: int) -> str:
        """The question, its state and (once it is closed) every forecast; or NONE. Forecasts of an open question stay
        hidden so nobody copies them."""
        if type(question_id) is not int or question_id < 0 or question_id >= int(self.question_count):
            return "NONE"
        q = json.loads(self.questions[u32(question_id)])
        out = _public_question(q)
        visible = q["state"] != "OPEN"
        out["forecasts"] = dict(sorted(q["forecasts"].items())) if visible else None
        return json.dumps(out, sort_keys=True)

    @gl.public.view
    def get_record_json(self, address: str) -> str:
        """The record of one address: resolved count, mean brier, skill, calibration buckets, distinct resolvers and
        every resolved forecast; NONE if the address never forecast."""
        who = _address(address)
        raw = self.forecasters.get(who)
        if raw is None:
            return "NONE"
        return json.dumps(_record(who, json.loads(raw), self._all_questions()), sort_keys=True)

    @gl.public.view
    def get_board_json(self, min_resolved: int) -> str:
        """Forecasters with at least min_resolved settled forecasts (min 1), best skill first, then more forecasts,
        then address. At most 50 rows."""
        if type(min_resolved) is not int or min_resolved < 1 or min_resolved > 1000:
            raise gl.vm.UserError("bad minimum")
        questions = self._all_questions()
        rows = []
        for i in range(int(self.forecaster_count)):
            who = self.forecaster_index[u32(i)]
            rec = _record(who, json.loads(self.forecasters[who]), questions)
            if rec["resolved"] >= min_resolved:
                rows.append({"address": who, "resolved": rec["resolved"], "mean_brier": rec["mean_brier"],
                             "skill_permille": rec["skill_permille"], "distinct_resolvers": rec["distinct_resolvers"]})
        rows.sort(key=lambda r: (-r["skill_permille"], -r["resolved"], r["address"]))
        return json.dumps(rows[:BOARD_LIMIT], sort_keys=True)

    @gl.public.view
    def get_question_count(self) -> int:
        return int(self.question_count)

    @gl.public.view
    def get_questions_page(self, offset: int, limit: int) -> str:
        """A page of questions (without forecasts), newest last."""
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= PAGE_LIMIT:
            raise gl.vm.UserError("bad page")
        total = int(self.question_count)
        items = [_public_question(json.loads(self.questions[u32(i)])) for i in range(offset, min(total, offset + limit))]
        return json.dumps({"total": total, "items": items}, sort_keys=True)
