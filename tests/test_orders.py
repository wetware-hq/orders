import copy, json, pathlib, sys, tomllib
import pytest
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import orders as O

EX = str(ROOT / "examples" / "ward_discharge.toml")
L = tomllib.loads(pathlib.Path(EX).read_text())

def bad(fn, why):
    d = copy.deepcopy(L); fn(d)
    with pytest.raises(O.Invalid, match=why):
        O.check_list(d)

def test_compile_example():
    assert O.main(["o", "compile", EX]) == 0

def test_list_rejections():
    bad(lambda d: d["job"].append(dict(d["job"][0])), "duplicate")
    bad(lambda d: d["job"][0]["depends_on"].append("ghost"), "unknown id")
    bad(lambda d: d["job"][0]["depends_on"].append("discharge"), "cycle")
    bad(lambda d: d["job"][0]["depends_on"].append("review_meds"), "cycle")
    bad(lambda d: d["job"][0].update(safety_critical="yes"), "safety_critical")
    bad(lambda d: d["job"][0].update(depends_on="x"), "depends_on")
    bad(lambda d: d["job"][0].update(text=""), "text")
    bad(lambda d: d["job"][0].update(id="Bad"), "snake")
    bad(lambda d: d["job"][0].pop("depends_on"), "needs")
    bad(lambda d: d.update(job=[]), "at least one")
    bad(lambda d: d.pop("list_id"), "list_id")

def cli(log, cmd, jid, role="clinician", ts="1936-05-02T09:00"):
    return O.main(["o", cmd, EX, str(log), jid, "--actor", "SYNTH-dr-01", "--role", role, "--ts", ts])

def test_cli_flow(tmp_path):
    log = tmp_path / "log.jsonl"
    assert O.main(["o", "status", EX, str(log)]) == 1
    assert cli(log, "tick", "write_summary") == 2            # waits for review_meds
    assert not log.exists()
    for j in ["review_meds", "write_summary", "book_followup", "remove_cannula", "teach_meds", "send_summary"]:
        assert cli(log, "tick", j) == 1
    assert cli(log, "untick", "review_meds") == 2            # dependents done
    assert cli(log, "tick", "discharge") == 0
    assert O.main(["o", "status", EX, str(log)]) == 0
    assert len(log.read_text().splitlines()) == 7

def test_cli_invalid(tmp_path):
    log = tmp_path / "log.jsonl"
    assert O.main(["o"]) == 2
    assert cli(log, "tick", "review_meds", ts="yesterday") == 2
    assert cli(log, "tick", "review_meds", role="boss") == 2
    assert cli(log, "untick", "review_meds") == 2
    log.write_text('{"episode_id": "SYNTH-discharge-0001", "actor": "x", "role": "nurse", "action": {"type": "tick", "id": "send_summary", "ts": "1936-05-02T09:00"}}\n')
    assert O.main(["o", "status", EX, str(log)]) == 2         # tampered log fails replay
    log.write_text("{oops\n")
    assert O.main(["o", "status", EX, str(log)]) == 2
