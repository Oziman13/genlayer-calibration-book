"""Mutation check: change the contract on a disposable copy, one small edit at a time, and see whether the tests notice.

Run from the repository root:  python3 scripts/mutation_check.py
It first proves that the unmodified contract passes, then runs the tests against every mutant (stopping at the first
failing test). A mutant that survives is either a missing test or an equivalent change; survivors are listed.
Each entry is (label, text to find, replacement, which occurrence of the text to change: 1 = first).
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "contracts/calibration_book.py"

MUTANTS = [
    ("min probability 0", "MIN_P, MAX_P = 1, 999", "MIN_P, MAX_P = 0, 999", 1),
    ("max probability 1000", "MIN_P, MAX_P = 1, 999", "MIN_P, MAX_P = 1, 1000", 1),
    ("question min 16", "QUESTION_MIN, QUESTION_MAX = 15, 240", "QUESTION_MIN, QUESTION_MAX = 16, 240", 1),
    ("question max 241", "QUESTION_MIN, QUESTION_MAX = 15, 240", "QUESTION_MIN, QUESTION_MAX = 15, 241", 1),
    ("101 questions", "MAX_QUESTIONS = 100", "MAX_QUESTIONS = 101", 1),
    ("101 forecasters", "MAX_FORECASTERS = 100", "MAX_FORECASTERS = 101", 1),
    ("41 forecasts per question", "MAX_FORECASTS_PER_QUESTION = 40", "MAX_FORECASTS_PER_QUESTION = 41", 1),
    ("horizon min 61", "MIN_HORIZON = 60 ", "MIN_HORIZON = 61 ", 1),
    ("horizon max 401 days", "MAX_HORIZON = 400 * 86400", "MAX_HORIZON = 401 * 86400", 1),
    ("void after 29 days", "VOID_AFTER = 30 * 86400", "VOID_AFTER = 29 * 86400", 1),
    ("page limit 51", "PAGE_LIMIT = 50", "PAGE_LIMIT = 51", 1),
    ("board limit 51", "BOARD_LIMIT = 50", "BOARD_LIMIT = 51", 1),
    ("baseline 251", "BASELINE_BRIER = 250", "BASELINE_BRIER = 251", 1),
    ("brier divisor", "** 2 // 1000", "** 2 // 1001", 1),
    ("brier without outcome", "(p - 1000 * outcome) ** 2", "(p - 500 * outcome) ** 2", 1),
    ("bucket edge", "return (p - 1) // 200", "return p // 200", 1),
    ("forecast allowed at closing", 'or _now() >= q["closes"]:', 'or _now() > q["closes"]:', 1),
    ("resolver may forecast", 'if who == q["resolver"]:', "if False:", 1),
    ("double forecast allowed", 'if who in q["forecasts"]:', "if False:", 1),
    ("forecast cap off by one", 'if len(q["forecasts"]) >= MAX_FORECASTS_PER_QUESTION:', 'if len(q["forecasts"]) > MAX_FORECASTS_PER_QUESTION:', 1),
    ("forecaster cap off by one", "if int(self.forecaster_count) >= MAX_FORECASTERS:", "if int(self.forecaster_count) > MAX_FORECASTERS:", 1),
    ("question cap off by one", "if qid >= MAX_QUESTIONS:", "if qid > MAX_QUESTIONS:", 1),
    ("anyone resolves", 'if _sender() != q["resolver"]:\n            raise gl.vm.UserError("only the resolver can settle")', "if False:\n            raise gl.vm.UserError(\"only the resolver can settle\")", 1),
    ("resolve before closing", 'if now < q["closes"]:\n            raise gl.vm.UserError("not closed yet")', "if False:\n            raise gl.vm.UserError(\"not closed yet\")", 1),
    ("void before closing", 'if now < q["closes"]:\n            raise gl.vm.UserError("not closed yet")', "if False:\n            raise gl.vm.UserError(\"not closed yet\")", 2),
    ("resolve twice", 'if q["state"] != "OPEN":\n            raise gl.vm.UserError("already settled")', "if False:\n            raise gl.vm.UserError(\"already settled\")", 1),
    ("void a settled question", 'if q["state"] != "OPEN":\n            raise gl.vm.UserError("already settled")', "if False:\n            raise gl.vm.UserError(\"already settled\")", 2),
    ("outcome inverted", 'q["outcome"] = 1 if outcome_yes else 0', 'q["outcome"] = 0 if outcome_yes else 1', 1),
    ("outcome type unchecked", "if type(outcome_yes) is not bool:", "if False:", 1),
    ("anyone voids at once", 'if _sender() != q["resolver"] and now < q["closes"] + VOID_AFTER:', 'if _sender() != q["resolver"] or now < q["closes"] + VOID_AFTER:', 1),
    ("void by anyone never", 'if _sender() != q["resolver"] and now < q["closes"] + VOID_AFTER:', 'if _sender() != q["resolver"]:', 1),
    ("forecasts always visible", 'visible = q["state"] != "OPEN"', "visible = True", 1),
    ("board ascending", '-r["skill_permille"], -r["resolved"], r["address"]', 'r["skill_permille"], -r["resolved"], r["address"]', 1),
    ("board volume ascending", '-r["skill_permille"], -r["resolved"], r["address"]', '-r["skill_permille"], r["resolved"], r["address"]', 1),
    ("board address descending", '-r["skill_permille"], -r["resolved"], r["address"]', '-r["skill_permille"], -r["resolved"], r["address"][::-1]', 1),
    ("board minimum exclusive", 'if rec["resolved"] >= min_resolved:', 'if rec["resolved"] > min_resolved:', 1),
    ("mean rounds up", '"mean_brier": total // n if n else None', '"mean_brier": (total + n - 1) // n if n else None', 1),
    ("skill denominator", "// (BASELINE_BRIER * n) if n else None", "// (BASELINE_BRIER * n + 1) if n else None", 1),
    ("resolvers counted per forecast", 'len({r["resolver"] for r in resolved})', "len(resolved)", 1),
    ("void not counted", '        elif q["state"] == "VOID":\n            voided += 1\n', '        elif q["state"] == "VOID":\n            pass\n', 1),
    ("resolver ignored", 'settler = asker if resolver == "" else _address(resolver)', "settler = asker", 1),
    ("closing window inclusive", "if not now + MIN_HORIZON <= closes <= now + MAX_HORIZON:", "if not now + MIN_HORIZON < closes < now + MAX_HORIZON:", 1),
    ("page limit unchecked", "or not 1 <= limit <= PAGE_LIMIT:", "or False:", 1),
    ("leap years ignored", "leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)", "leap = year % 4 == 0", 1),
    ("leap years always", "leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)", "leap = True", 1),
    ("second 60 allowed", 'int(clock[6:8]) > 59', 'int(clock[6:8]) > 60', 1),
    ("offset sign ignored", '* (1 if rest[0] == "+" else -1)', "* 1", 1),
    ("address case sensitive", "value = value.strip().lower()", "value = value.strip()", 1),
    ("address length", "len(value) != 42", "len(value) != 41", 1),
    ("probability type unchecked", "if type(probability_permille) is not int or not MIN_P <= probability_permille <= MAX_P:", "if not MIN_P <= probability_permille <= MAX_P:", 1),
    ("question id type unchecked", "if type(qid) is not int or qid < 0 or qid >= int(self.question_count):", "if qid >= int(self.question_count):", 1),
    ("grace min 61", "GRACE_MIN = 60 ", "GRACE_MIN = 61 ", 1),
    ("grace max +1", "GRACE_MAX = 7 * 86400 ", "GRACE_MAX = 7 * 86400 + 1 ", 1),
    ("grace unchecked without source", 'if source == "" else not GRACE_MIN', 'if False else not GRACE_MIN', 1),
    ("grace type unchecked", "if type(grace_seconds) is not int or (", "if (", 1),
    ("grace allowed without source", "(grace_seconds != 0 if source", "(False if source", 1),
    ("source max 301", "SOURCE_MAX = 300", "SOURCE_MAX = 301", 1),
    ("source http allowed", 'not value.startswith("https://")', 'not value.startswith("http")', 1),
    ("source fragment allowed", 'any(c in value for c in ("\\\\", "#", "@"))', 'any(c in value for c in ("\\\\", "@"))', 1),
    ("source userinfo allowed", 'any(c in value for c in ("\\\\", "#", "@"))', 'any(c in value for c in ("\\\\", "#"))', 1),
    ("source non-ascii allowed", "or ord(c) >= 127 for c in value", "for c in value", 1),
    ("source single label allowed", "len(labels) < 2 or", "len(labels) < 1 or", 1),
    ("source local tld allowed", '"localhost", "internal", "test", "invalid"', '"internal", "test", "invalid"', 1),
    ("source numeric tld allowed", "not labels[-1].isalpha() or", "False or", 1),
    ("source tld one letter", "len(labels[-1]) < 2", "len(labels[-1]) < 1", 1),
    ("source label length", "len(label) > 63", "len(label) > 64", 1),
    ("source label hyphen start", "label[0] == \"-\"", "False", 1),
    ("source label chars", "any(c not in \"abcdefghijklmnopqrstuvwxyz0123456789-\" for c in label)", "False", 1),
    ("source host case", 'host = value[8:].split("/")[0].split("?")[0].lower()', 'host = value[8:].split("/")[0].split("?")[0]', 1),
    ("source query in host", '.split("/")[0].split("?")[0]', '.split("/")[0]', 1),
    ("page cap 8001", "MAX_PAGE = 8000", "MAX_PAGE = 8001", 1),
    ("page not capped", "return body[:MAX_PAGE]", "return body", 1),
    ("empty page accepted", "or not body.strip():", "or False:", 1),
    ("quote min 11", "QUOTE_MIN, QUOTE_MAX = 12, 300", "QUOTE_MIN, QUOTE_MAX = 11, 300", 1),
    ("quote max 301", "QUOTE_MIN, QUOTE_MAX = 12, 300", "QUOTE_MIN, QUOTE_MAX = 12, 301", 1),
    ("quote case sensitive", "_squash(quote).lower() in _squash(page).lower()", "_squash(quote) in _squash(page)", 1),
    ("quote spacing sensitive", "_squash(quote).lower() in _squash(page).lower()", "quote.lower() in page.lower()", 1),
    ("model answer size 2001", "MAX_MODEL = 2000", "MAX_MODEL = 2001", 1),
    ("extra keys allowed", 'set(raw) != {"verdict", "quote"}', 'not {"verdict"} <= set(raw)', 1),
    ("maybe verdict allowed", 'verdict not in ("YES", "NO", "UNDECIDED") or', 'False or', 1),
    ("undecided may quote", 'if quote == "" else {"state": "ERROR"}', 'if True else {"state": "ERROR"}', 1),
    ("quote not checked on page", "        if not _on_page(quote, page):\n            return {\"state\": \"ERROR\"}\n", "", 1),
    ("quote not squashed", "        quote = _squash(quote)\n        if not _on_page", "        if not _on_page", 1),
    ("validator takes any verdict", "        if parsed[\"verdict\"] != mine[\"verdict\"]:\n            return False\n", "", 1),
    ("validator skips quote check", 'return parsed["verdict"] == "UNDECIDED" or _on_page(parsed["quote"], page)', "return True", 1),
    ("validator error mismatch", 'return parsed["state"] == mine["state"]', "return True", 1),
    ("validator skips shape check", "        if not _valid_reading(parsed):\n            return False\n", "", 1),
    ("validator size 2001", "len(proposed) > MAX_MODEL:\n            return False", "len(proposed) > MAX_MODEL + 1:\n            return False", 1),
    ("validator ignores non-return", "        if not isinstance(result, gl.vm.Return):\n            return False\n", "", 1),
    ("validator non-text accepted", "not isinstance(proposed, str) or ", "", 1),
    ("reading accepts odd state", 'or value["state"] != "OK":\n        return False', ':\n        return False', 1),
    ("reading undecided with quote", '        return quote == ""\n', '        return True\n', 1),
    ("reading quote shape unchecked", 'isinstance(quote, str) and quote == _squash(quote)\n            and QUOTE_MIN', 'isinstance(quote, str)\n            and QUOTE_MIN', 1),
    ("settle needs no source", '        if q["source"] == "":\n            raise gl.vm.UserError("this question names no source")\n', "", 1),
    ("settle ignores grace", 'if now < q["closes"] + q["grace"]:', 'if now < q["closes"]:', 1),
    ("settle grace inclusive", 'if now < q["closes"] + q["grace"]:', 'if now <= q["closes"] + q["grace"]:', 1),
    ("settle twice", '        if q["state"] != "OPEN":\n            raise gl.vm.UserError("already settled")\n        if q["source"]', '        if q["source"]', 1),
    ("unreadable source accepted", 'if reading.get("state") != "OK":', "if False:", 1),
    ("undecided settles", 'if reading["verdict"] == "UNDECIDED":\n            return json.dumps({"verdict": "UNDECIDED"})\n', "", 1),
    ("source outcome inverted", 'q["outcome"] = 1 if reading["verdict"] == "YES" else 0', 'q["outcome"] = 0 if reading["verdict"] == "YES" else 1', 1),
    ("source settled_by wrong", 'q["settled_by"] = "SOURCE"', 'q["settled_by"] = "RESOLVER"', 1),
    ("evidence dropped", 'q["evidence"] = {"quote": reading["quote"], "source": q["source"]}', 'q["evidence"] = None', 1),
    ("settle time not stored", '        q["settled"] = now\n        q["settled_by"] = "SOURCE"', '        q["settled_by"] = "SOURCE"', 1),
    ("resolver settled_by missing", '        q["settled_by"] = "RESOLVER"\n', "", 1),
    ("calibration observed share", '"observed_yes_permille": 1000 * yes // count if count else None', '"observed_yes_permille": 1000 * yes // (count + 1) if count else None', 1),
    ("calibration average", '"avg_forecast_permille": sum(r["p"] for r in items) // count if count else None', '"avg_forecast_permille": sum(r["p"] for r in items) // (count + 1) if count else None', 1),
]


def run_tests(directory: Path) -> bool:
    result = subprocess.run([sys.executable, "-m", "pytest", "tests/direct", "-q", "-x", "-p", "no:cacheprovider"],
                            cwd=directory, capture_output=True, text=True)
    return result.returncode == 0


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mutation-") as tmp:
        work = Path(tmp)
        shutil.copytree(ROOT / "contracts", work / "contracts")
        shutil.copytree(ROOT / "tests", work / "tests")
        source = (ROOT / CONTRACT).read_text()
        if not run_tests(work):
            print("The unmodified contract does not pass its tests; stopping.")
            return 2
        print("base: tests pass on the unmodified contract")
        killed, survived = 0, []
        for label, old, new, nth in MUTANTS:
            if source.count(old) < nth:
                print("ANCHOR MISSING:", label)
                return 3
            parts = source.split(old)
            mutated = old.join(parts[:nth]) + new + old.join(parts[nth:])
            (work / CONTRACT).write_text(mutated)
            if run_tests(work):
                survived.append(label)
                print("SURVIVED:", label)
            else:
                killed += 1
        print("mutants: %d, killed: %d, survived: %d" % (len(MUTANTS), killed, len(survived)))
        for label in survived:
            print(" -", label)
        return 0


if __name__ == "__main__":
    sys.exit(main())
