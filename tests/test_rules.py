"""One test per register row (RULES.md). Synthetic data only."""
import copy, json, pathlib, sys, tomllib
import pytest
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import orders as O
from messages import msg

EX = str(ROOT / "examples" / "ward_discharge.toml")
L = tomllib.loads(pathlib.Path(EX).read_text())
TS = "1936-05-02T10:00"
ORDER = ["review_meds", "write_summary", "book_followup", "remove_cannula", "teach_meds", "send_summary", "discharge"]

def env(role="clinician", ep="SYNTH-EP-0001"):
    e = O.OrdersEnv(); e.reset(EX, "SYNTH-dr-01", role, ep); return e

def tick(i, ev="SYNTH-REC-0001"):
    return {"type": "tick", "id": i, "ts": TS, "evidence": ev}

def bad(fn):
    d = copy.deepcopy(L); fn(d)
    with pytest.raises(O.Invalid) as e:
        O.check_list(d)
    return str(e.value)

def cli(log, cmd, jid, role="clinician", **kw):
    extra = sum(([f"--{k}", v] for k, v in kw.items()), [])
    return O.main(["o", cmd, EX, str(log), jid, "--actor", "SYNTH-dr-01", "--role", role, "--ts", TS] + extra)

def scripted_agent(e):
    """Deterministic agent: tick the first available job, citing a synthetic record id."""
    n = 0
    while (nxt := e.step({"type": "next"})["next"]):
        n += 1
        assert e.step(tick(nxt[0], f"SYNTH-REC-{n:04d}"))["accepted"]

def test_job_fields_owner_due_safety():
    for k in ("owner", "due", "safety_critical"):
        assert bad(lambda d: d["job"][0].pop(k)) == "ERR:job_fields"
    assert bad(lambda d: d["job"][0].update(due="1936-05-02T10:00")) == "ERR:job_due"
    assert bad(lambda d: d["job"][0].update(safety_critical="yes")) == "ERR:job_safety_critical"

def test_tick_requires_evidence():
    e = env()
    for a in ({"type": "tick", "id": "review_meds", "ts": TS}, tick("review_meds", "  ")):
        assert e.step(a)["reason"] == msg("RF-EVIDENCE")
    assert e.log == []

def test_cancel_requires_reason():
    e = env()
    assert e.step({"type": "cancel", "id": "book_followup", "ts": TS})["reason"] == msg("RF-CANCEL-REASON")
    assert e.step({"type": "cancel", "id": "book_followup", "ts": TS, "reason": ""})["reason"] == msg("RF-CANCEL-REASON")

def test_cancelled_never_counts_as_done():
    e = env(); e.step({"type": "cancel", "id": "book_followup", "ts": TS, "reason": "SYNTH booked elsewhere"})
    for i in ORDER:
        if i != "book_followup" and i != "discharge":
            e.step(tick(i))
    c = e.check()
    assert "book_followup" not in c["done"] and c["cancelled"] == ["book_followup"] and not c["all_done"]
    assert e.step(tick("discharge"))["reason"] == msg("RF-WAITING", job_text="Book follow-up clinic")

def test_only_clinician_cancels_safety_critical():
    for role in ("nurse", "agent", "patient"):
        assert env(role).step({"type": "cancel", "id": "remove_cannula", "ts": TS, "reason": "x"})["reason"] == msg("RF-ROLE-CANCEL")
    assert env("clinician").step({"type": "cancel", "id": "remove_cannula", "ts": TS, "reason": "x"})["accepted"]
    assert env("nurse").step({"type": "cancel", "id": "book_followup", "ts": TS, "reason": "x"})["accepted"]

def test_status_now_flags_overdue(tmp_path, capsys):
    log = tmp_path / "l.jsonl"
    assert O.main(["o", "status", EX, str(log), "--now", "1936-05-02 12:30"]) == 1
    out = capsys.readouterr().out
    assert msg("FL-OVERDUE", job_text="Review discharge medicines", owner="SYNTH-dr-01", time="1936-05-02 10:00") in out
    assert "Remove cannula" not in out          # due 13:00, not yet overdue
    assert O.main(["o", "status", EX, str(log), "--now", "yesterday"]) == 2

def test_cli_cancel(tmp_path):
    log = tmp_path / "l.jsonl"
    assert cli(log, "cancel", "remove_cannula", role="nurse", reason="SYNTH not needed") == 2
    assert cli(log, "cancel", "remove_cannula", role="clinician") == 2           # no reason
    assert not log.exists()
    assert cli(log, "cancel", "remove_cannula", role="clinician", reason="SYNTH not needed") == 1
    assert json.loads(log.read_text())["action"]["reason"] == "SYNTH not needed"

def test_compile_rejects_duplicate_ids():
    assert bad(lambda d: d["job"].append(dict(d["job"][0]))) == "ERR:duplicate_job_id"

def test_compile_rejects_cycles():
    assert bad(lambda d: d["job"][0]["depends_on"].append("write_summary")).startswith("Jobs ")

def test_compile_rejects_unknown_dependency():
    assert bad(lambda d: d["job"][0]["depends_on"].append("ghost")) == "ERR:unknown_dependency"

def test_log_append_only(tmp_path):
    log = tmp_path / "l.jsonl"
    cli(log, "tick", "review_meds", evidence="SYNTH-REC-1")
    first = log.read_text()
    cli(log, "tick", "write_summary", evidence="SYNTH-REC-2")
    t = log.read_text()
    assert t.startswith(first) and len(t.splitlines()) == 2
    ev = json.loads(t.splitlines()[0])
    assert ev["action"]["id"] == "review_meds" and ev["actor"] == "SYNTH-dr-01" and ev["action"]["ts"] == TS

def test_state_from_replay_only(tmp_path):
    log = tmp_path / "l.jsonl"
    cli(log, "tick", "review_meds", evidence="SYNTH-REC-1")
    assert list(tmp_path.iterdir()) == [log]          # no state file
    e = env(ep=L["list_id"]); e.replay(O.read_log(log))
    assert e.state() == (("review_meds",), ())

def test_tick_waits_for_dependencies():
    assert env().step(tick("write_summary"))["reason"] == msg("RF-WAITING", job_text="Review discharge medicines")

def test_untick_blocked_by_done_dependent():
    e = env(); e.step(tick("review_meds")); e.step(tick("teach_meds"))
    assert e.step({"type": "untick", "id": "review_meds", "ts": TS})["reason"] == "ERR:dependent_done"
    assert e.step({"type": "untick", "id": "teach_meds", "ts": TS})["accepted"]
    assert e.step({"type": "untick", "id": "review_meds", "ts": TS})["accepted"]

def test_status_exit_codes(tmp_path):
    log = tmp_path / "l.jsonl"
    assert O.main(["o", "status", EX, str(log)]) == 1
    for i in ORDER[:-1]:
        assert cli(log, "tick", i, evidence="SYNTH-REC-1") == 1
    assert cli(log, "tick", "discharge", evidence="SYNTH-REC-1") == 0
    assert O.main(["o", "status", EX, str(log)]) == 0
    log.write_text("{oops\n")
    assert O.main(["o", "status", EX, str(log)]) == 2 and O.main(["o"]) == 2

def test_status_shows_next_jobs(tmp_path, capsys):
    O.main(["o", "status", EX, str(tmp_path / "l.jsonl")])
    out = capsys.readouterr().out
    assert "ERR:next:review_meds" in out and "ERR:next:book_followup" in out and "next:write_summary" not in out

def test_rejected_actions_not_logged():
    e = env()
    for a in ({"type": "erase"}, tick("ghost"), {"type": "tick", "id": "review_meds", "ts": 1936, "evidence": "R"}):
        assert not e.step(a)["accepted"]
    assert e.log == []

def test_replay_equality():
    e = env(); scripted_agent(e)
    f = env(); assert f.replay(e.log) == e.check() and f.log == e.log
    forged = [{"episode_id": "SYNTH-EP-0001", "actor": "x", "role": "nurse",
               "action": {"type": "cancel", "id": "discharge", "ts": TS, "reason": "x"}}]
    with pytest.raises(O.Invalid):
        env().replay(forged)

def test_observe_isolated_episode():
    a, b = env(ep="SYNTH-EP-A"), env(ep="SYNTH-EP-B")
    a.step(tick("review_meds"))
    assert "review_meds" in b.observe()["open"]
    with pytest.raises(O.Invalid):
        b.replay(a.log)

def test_example_compiles():
    assert O.main(["o", "compile", EX]) == 0

def test_scripted_agent_episode():
    e = env(); scripted_agent(e)
    assert e.check()["all_done"] and [x["action"]["id"] for x in e.log][-1] == "discharge"

# ---------- wording lines ----------
def test_line_rf_role_cancel():
    assert env("nurse").step({"type": "cancel", "id": "discharge", "ts": TS, "reason": "x"})["reason"] == \
        "Refused: only a clinician's own session can cancel a safety job."

def test_line_rf_evidence():
    assert env().step(tick("review_meds", ""))["reason"] == "Refused: a tick needs a record id, such as a result or a sent document."

def test_line_rf_waiting():
    assert env().step(tick("send_summary"))["reason"] == "Refused: this job waits on Write discharge summary, which is not done yet."

def test_line_rf_cancel_reason():
    assert env().step({"type": "cancel", "id": "discharge", "ts": TS})["reason"] == "Refused: cancelling needs a reason."

def test_line_ck_loop():
    assert bad(lambda d: d["job"][0]["depends_on"].append("write_summary")) in (
        msg("CK-LOOP", a="Review discharge medicines", b="Write discharge summary"),
        msg("CK-LOOP", a="Write discharge summary", b="Review discharge medicines"))

def test_line_fl_overdue():
    e = env()
    assert "Overdue: Patient leaves the ward, owner SYNTH-dr-01, due 1936-05-02 15:00." in e.check("1936-05-02 16:00")["flags"]
