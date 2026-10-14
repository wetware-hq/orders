"""One test per register row (RULES.md). Synthetic data only."""
import copy, json, pathlib, sys, tomllib
import pytest
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import orders as O
from access import Invalid, open_session
from messages import msg

EX = str(ROOT / "examples" / "ward_discharge.toml")
PX = str(ROOT / "examples" / "pharmacy_discharge_reconciliation.toml")
L = tomllib.loads(pathlib.Path(EX).read_text())
TS = "1936-05-02T10:00"
EP = "SYNTH-EP-0001"
ORDER = ["review_meds", "write_summary", "book_followup", "remove_cannula", "teach_meds", "send_summary", "discharge"]
REC = "SYNTH-REC-0001"

def approval(case=EX, disc="medicine", ep=EP):
    a = O.OrdersEnv(); a.reset(case, "SYNTH-dr-01", "clinician", ep, disc)
    assert a.step({"type": "approve", "ts": TS})["accepted"]
    return a.log

def env(role="clinician", discipline="medicine", scope=(), case=EX, ep=EP, approve="medicine", log=None):
    if role != "clinician":
        discipline = None
    e = O.OrdersEnv(); e.reset(case, "SYNTH-person-01", role, ep, discipline, scope)
    e.replay((approval(case, approve, ep) if approve else []) + (log or []))
    return e

def tick(i, ev=REC):
    return {"type": "tick", "id": i, "ts": TS, "evidence": ev}

def cancel(i, reason="SYNTH reason"):
    return {"type": "cancel", "id": i, "ts": TS, "reason": reason}

def bad(fn):
    d = copy.deepcopy(L); fn(d)
    with pytest.raises(Invalid) as e:
        O.check_list(d)
    return str(e.value)

def cli(log, cmd, jid=None, role="clinician", disc="medicine", **kw):
    extra = sum(([f"--{k}", v] for k, v in kw.items()), []) + (["--discipline", disc] if disc else [])
    return O.main(["o", cmd, EX, str(log), EP] + ([jid] if jid else []) +
                  ["--actor", "SYNTH-dr-01", "--role", role, "--ts", TS] + extra)

def scripted_agent(e):
    """Deterministic clinician: tick the first available job, citing a registered record."""
    while (nxt := e.step({"type": "next"})["next"]):
        assert e.step(tick(nxt[0]))["accepted"], nxt

# ---------- earlier signed rules (Mathew) ----------
def test_job_fields_owner_due_safety():
    for k in ("owner", "due", "safety_critical"):
        assert bad(lambda d: d["job"][0].pop(k)) == msg("ER-BAD-FIELD", field=k, item="job 1")
    assert bad(lambda d: d["job"][0].update(due="1936-05-02T10:00")) == msg("ER-BAD-FIELD", field="due", item="job 1")

def test_tick_requires_evidence():
    e = env(); n = len(e.log)
    for a in ({"type": "tick", "id": "review_meds", "ts": TS}, tick("review_meds", "  ")):
        assert e.step(a)["reason"] == msg("RF-EVIDENCE")
    assert len(e.log) == n

def test_cancel_requires_reason():
    e = env()
    assert e.step({"type": "cancel", "id": "book_followup", "ts": TS})["reason"] == msg("RF-CANCEL-REASON")
    assert e.step(cancel("book_followup", " "))["reason"] == msg("RF-CANCEL-REASON")

def test_cancelled_never_counts_as_done():
    e = env(); e.step(cancel("book_followup"))
    for i in ORDER[:-1]:
        e.step(tick(i))
    c = e.check()
    assert "book_followup" not in c["done"] and not c["all_done"]
    assert e.step(tick("discharge"))["reason"] == msg("WT-WAITING", job_text="Book follow-up clinic")

def test_only_clinician_cancels_safety_critical():
    assert env("patient").step(cancel("remove_cannula"))["reason"] == msg("RF-ROLE-CANCEL")
    assert env("clinician_agent", scope=("nursing",)).step(cancel("remove_cannula"))["reason"] == msg("RF-CLIN-AGENT")
    assert env("clinician", "nursing").step(cancel("remove_cannula"))["accepted"]

def test_status_now_flags_overdue(tmp_path, capsys):
    log = tmp_path / "l.jsonl"
    assert O.main(["o", "status", EX, str(log), EP, "--now", "1936-05-02 12:30"]) == 1
    out = capsys.readouterr().out
    assert msg("FL-OVERDUE", job_text="Review discharge medicines", owner="SYNTH-dr-01", time="1936-05-02 10:00") in out
    assert "Overdue: Remove cannula" not in out
    assert O.main(["o", "status", EX, str(log), EP, "--now", "yesterday"]) == 2

def test_cli_cancel(tmp_path):
    log = tmp_path / "l.jsonl"
    cli(log, "approve")
    assert cli(log, "cancel", "remove_cannula", role="patient", disc=None, reason="SYNTH not needed") == 2
    assert cli(log, "cancel", "remove_cannula") == 2                       # no reason
    assert len(log.read_text().splitlines()) == 1
    assert cli(log, "cancel", "remove_cannula", reason="SYNTH not needed") == 1
    assert json.loads(log.read_text().splitlines()[-1])["reason"] == "SYNTH not needed"

def test_compile_rejects_duplicate_ids():
    assert bad(lambda d: d["job"].append(dict(d["job"][0]))) == msg("ER-DUP-ID", id="review_meds")

def test_compile_rejects_cycles():
    assert bad(lambda d: d["job"][0]["depends_on"].append("write_summary")).startswith("Jobs ")

def test_compile_rejects_unknown_dependency():
    assert bad(lambda d: d["job"][0]["depends_on"].append("ghost")) == msg("ER-BAD-FIELD", field="depends_on", item="job 1")

def test_log_append_only(tmp_path):
    log = tmp_path / "l.jsonl"
    cli(log, "approve"); first = log.read_text()
    cli(log, "tick", "review_meds", evidence=REC)
    t = log.read_text()
    assert t.startswith(first) and len(t.splitlines()) == 2
    ev = json.loads(t.splitlines()[1])
    assert ev["id"] == "review_meds" and ev["actor"] == "SYNTH-dr-01" and ev["ts"] == TS

def test_state_from_replay_only(tmp_path):
    log = tmp_path / "l.jsonl"
    cli(log, "approve"); cli(log, "tick", "review_meds", evidence=REC)
    assert list(tmp_path.iterdir()) == [log]
    e = O.OrdersEnv(); e.reset(EX, "x", "patient", EP); e.replay(O.read_log(log))
    assert e.state() == (("review_meds",), (), "medicine")

def test_tick_waits_for_dependencies():
    assert env().step(tick("write_summary"))["reason"] == msg("WT-WAITING", job_text="Review discharge medicines")

def test_untick_blocked_by_done_dependent():
    e = env(); e.step(tick("review_meds")); e.step(tick("teach_meds"))
    assert e.step({"type": "untick", "id": "review_meds", "ts": TS})["reason"] == "ERR:dependent_done"
    assert e.step({"type": "untick", "id": "teach_meds", "ts": TS})["accepted"]
    assert e.step({"type": "untick", "id": "review_meds", "ts": TS})["accepted"]

def test_status_exit_codes(tmp_path):
    log = tmp_path / "l.jsonl"
    assert O.main(["o", "status", EX, str(log), EP]) == 1
    cli(log, "approve")
    for i in ORDER[:-1]:
        assert cli(log, "tick", i, evidence=REC) == 1
    assert cli(log, "tick", "discharge", evidence=REC) == 0
    assert O.main(["o", "status", EX, str(log), EP]) == 0
    log.write_text("{oops\n")
    assert O.main(["o", "status", EX, str(log), EP]) == 2 and O.main(["o"]) == 2

def test_status_shows_next_jobs(tmp_path, capsys):
    log = tmp_path / "l.jsonl"; cli(log, "approve"); capsys.readouterr()
    O.main(["o", "status", EX, str(log), EP])
    out = capsys.readouterr().out
    assert msg("PV-NEXT", plain="Review discharge medicines") in out and "Write discharge summary\n" not in out

def test_rejected_actions_not_logged():
    e = env(); n = len(e.log)
    for a in ({"type": "erase"}, tick("ghost"), {"type": "tick", "id": "review_meds", "ts": 1936, "evidence": REC}):
        assert not e.step(a)["accepted"]
    assert len(e.log) == n

def test_replay_equality():
    e = env(); scripted_agent(e)
    f = env(approve=None); assert f.replay(e.log) == e.check() and f.log == e.log
    forged = dict(e.log[1], role="patient", discipline=None)
    with pytest.raises(Invalid):
        env(approve=None).replay([e.log[0], forged])

def test_observe_isolated_episode():
    a, b = env(ep="SYNTH-EP-A"), env(ep="SYNTH-EP-B")
    a.step(tick("review_meds"))
    assert "review_meds" in b.observe()["open"]
    with pytest.raises(Invalid):
        b.replay(a.log)

def test_example_compiles():
    assert O.main(["o", "compile", EX]) == 0 and O.main(["o", "compile", PX]) == 0

def test_scripted_agent_episode():
    e = env(); scripted_agent(e)
    assert e.check()["all_done"] and e.log[-1]["id"] == "discharge"

# ---------- round 4 (Physician) ----------
def test_closed_state_exit_3():
    e = env(); e.step(cancel("book_followup"))
    for i in ORDER:
        e.step(tick(i))
    e.step(cancel("discharge"))
    c = e.check()
    assert c["closed"] and not c["all_done"] and e.exit_code(c) == 3 and c["status"] == msg("ST-CLOSED")
    assert e.exit_code(env().check()) == 1

def test_closed_needs_clinician_cancel_on_safety_job():
    e = env(); e.step(cancel("remove_cannula"))
    assert e._closed_ok("remove_cannula")
    e.cancelled["remove_cannula"]["role"] = "clinician_agent"          # forged state: never via the gate
    assert not e._closed_ok("remove_cannula")
    e.cancelled["book_followup"] = {"reason": "", "role": "clinician"}
    assert not e._closed_ok("book_followup")

def test_evidence_must_be_registered_record():
    assert env().step(tick("review_meds", "SYNTH-REC-9999"))["reason"] == msg("RF-EVIDENCE-NOT-FOUND")
    assert env().step(tick("review_meds", "SYNTH-REC-0002"))["accepted"]

# ---------- generality (Mathew) ----------
def test_roles_are_four_with_discipline():
    for args in (("clinician", "  "), ("patient", "medicine"), ("clinician_agent", None, ("",)),
                 ("patient", None, ("medicine",)), ("clinician", 3)):
        with pytest.raises(Invalid):
            open_session("SYNTH-c", *args)
    with pytest.raises(Invalid):
        open_session(" ", "patient")
    assert O.PERMS.keys() == {"patient", "patient_agent", "clinician", "clinician_agent"}
    for d in ("medicine", "nursing", "physiotherapy", "pharmacy"):
        assert open_session("SYNTH-c", "clinician", d).discipline == d

def test_permissions_table_by_role():
    assert O.PERMS == {
        "patient": {"tick", "untick", "submit", "next"},
        "patient_agent": {"tick", "submit", "next"},
        "clinician": {"tick", "untick", "cancel", "approve", "submit", "review", "next"},
        "clinician_agent": {"tick", "submit", "next"}}
    for role, allowed in O.PERMS.items():
        assert allowed <= set(O.FIELDS) | {"next"} and role in O.DENY
    assert "cancel" not in O.PERMS["clinician_agent"] and "approve" not in O.PERMS["clinician_agent"]

def test_allied_health_example():
    e = env("clinician", "pharmacy", case=PX, approve="pharmacy")
    for i, r in (("get_home_list", "SYNTH-RX-0001"), ("reconcile", "SYNTH-RX-0002"), ("counsel_patient", "SYNTH-RX-0002"), ("send_list", "SYNTH-RX-0003")):
        assert e.step(tick(i, r))["accepted"], i
    assert e.check()["all_done"]

# ---------- round 5 (Physician) ----------
def test_scope_of_practice_refuses_other_discipline():
    e = env("clinician", "physiotherapy")
    assert e.step(cancel("remove_cannula"))["reason"] == msg("RF-SCOPE", disciplines="nursing, medicine")
    assert e.step(tick("review_meds"))["reason"] == msg("RF-SCOPE", disciplines="medicine")

def test_scope_defaults_to_approver_discipline():
    e = env("clinician", "nursing", approve="medicine")
    assert e.step(tick("review_meds"))["reason"] == msg("RF-SCOPE", disciplines="medicine")
    assert env("clinician", "medicine").step(tick("review_meds"))["accepted"]

def test_unregistered_role_refused():
    for r in ("carer", "assistant", "family", "nurse"):
        with pytest.raises(Invalid, match="only registered health professionals"):
            O.OrdersEnv().reset(EX, "SYNTH-x", r, EP)

def test_clinician_agent_rules():
    a = env("clinician_agent", scope=("medicine",))
    assert a.step(cancel("book_followup"))["reason"] == msg("RF-CLIN-AGENT")
    assert a.step({"type": "approve", "ts": TS})["reason"] == msg("RF-CLIN-AGENT")
    assert a.step(tick("review_meds"))["reason"] == msg("RF-PERSON-JOB")
    assert a.step(tick("book_followup", "SYNTH-made-up"))["reason"] == msg("RF-EVIDENCE-NOT-FOUND")
    assert a.step(tick("book_followup"))["accepted"]

def test_clinician_agent_limited_to_granter_scope():
    d = copy.deepcopy(L); d["job"][2].update(safety_critical=True, disciplines=["medicine"])
    a = env("clinician_agent", scope=("pharmacy",), case=d)
    assert a.step(tick("book_followup"))["reason"] == msg("RF-SCOPE", disciplines="medicine")
    assert env("clinician_agent", scope=("medicine",), case=d).step(tick("book_followup"))["accepted"]

# ---------- P rows (Physician, safety) ----------
def test_p03_cancel_own_session_only():
    assert env("patient_agent").step(cancel("book_followup"))["reason"] == msg("RF-ROLE-CANCEL")

def test_p04_agent_doable_jobs():
    assert bad(lambda d: d["job"][0].pop("agent_doable")) == msg("ER-BAD-FIELD", field="agent_doable", item="job 1")
    assert env("patient_agent").step(tick("review_meds"))["reason"] == msg("RF-PERSON-JOB")
    assert env("patient_agent").step(tick("book_followup"))["accepted"]

def test_p05_evidence_is_findable_record():
    a = env("clinician_agent", scope=("medicine",))
    assert a.step(tick("book_followup", "I booked it"))["reason"] == msg("RF-EVIDENCE-NOT-FOUND")

def test_p06_version_lock_and_diff_view():
    log = approval()
    d = copy.deepcopy(L); d["job"][2]["text"] = "Book follow-up physiotherapy"
    e = O.OrdersEnv(); e.reset(d, "SYNTH-dr", "clinician", EP, "medicine"); e.replay(log)
    assert e.step(tick("book_followup"))["reason"] == msg("RF-NOT-APPROVED")
    assert O.diff_view(L, d) == {"added": [], "removed": [], "changed": ["book_followup"]}

def test_p07_propose_needs_checker_and_approval():
    with pytest.raises(Invalid):
        O.propose_list(dict(L, job=L["job"] + [dict(L["job"][0])]))
    assert O.propose_list(L)["approved"] is False
    assert env(approve=None).step(tick("review_meds"))["reason"] == msg("RF-NOT-APPROVED")

def test_p08_source_line_citations_and_gap_view():
    assert bad(lambda d: d["job"][0].pop("source_line")) == msg("CK-NO-SOURCE", n=1)
    assert bad(lambda d: d["job"][0].update(source_line=99)) == msg("CK-NO-SOURCE", n=1)
    assert O.gap_view(dict(L, source_lines=8)) == [msg("FL-GAP", n=8)]

def test_p09_roles_fixed_at_session_open():
    e = env()
    for k in ("role", "actor", "discipline", "episode_id", "scope"):
        assert e.step(dict(tick("review_meds"), **{k: "x"}))["reason"] == "ERR:role_fixed_at_session_open"
    assert env("patient").step(dict(tick("book_followup"), role="clinician"))["reason"] == ""

# ---------- wording lines ----------
def test_line_rf_role_cancel():
    assert env("patient").step(cancel("discharge"))["reason"] == "Refused: only a clinician's own session can cancel a safety job."

def test_line_rf_person_job():
    assert env("patient_agent").step(tick("review_meds"))["reason"] == "Refused: a person must do this job and tick it in their own session."

def test_line_rf_evidence():
    assert env().step(tick("review_meds", ""))["reason"] == "Refused: a tick needs a record id, such as a result or a sent document."

def test_line_wt_waiting():
    assert env().step(tick("send_summary"))["reason"] == "Waiting: this job can start once Write discharge summary is done."

def test_line_rf_cancel_reason():
    assert env().step({"type": "cancel", "id": "discharge", "ts": TS})["reason"] == "Refused: cancelling needs a reason."

def test_line_rf_not_approved():
    assert env(approve=None).step(tick("review_meds"))["reason"] == "Refused: this form or list is not approved by a clinician yet."

def test_line_ck_loop():
    assert bad(lambda d: d["job"][0]["depends_on"].append("write_summary")) in (
        msg("CK-LOOP", a="Review discharge medicines", b="Write discharge summary"),
        msg("CK-LOOP", a="Write discharge summary", b="Review discharge medicines"))

def test_line_ck_no_source():
    assert bad(lambda d: d["job"][1].pop("source_line")) == "Job 2 does not say which line it came from."

def test_line_fl_overdue():
    assert "Overdue: Patient leaves the ward, owner SYNTH-dr-01, due 1936-05-02 15:00." in env().check("1936-05-02 16:00")["flags"]

def test_line_fl_gap(capsys):
    assert O.main(["o", "compile", PX]) == 0
    assert capsys.readouterr().out.strip() == "No job was made from source line 5. Approve or add one."

def test_line_rf_evidence_not_found():
    assert env().step(tick("review_meds", "nope"))["reason"] == "Refused: no record with this id was found."

def test_line_st_done():
    e = env(); scripted_agent(e); assert e.check()["status"] == "Done: every job is finished."

def test_line_st_closed():
    test_closed_state_exit_3()

def test_line_pv_next():
    assert env().next_view()[0] == "Next for you: Review discharge medicines"

def test_line_pv_nothing_next():
    e = env(); scripted_agent(e); assert e.next_view() == ["There's nothing for you to do right now."]

def test_line_er_dup_id():
    test_compile_rejects_duplicate_ids()

def test_line_er_bad_field():
    test_job_fields_owner_due_safety()

def test_line_er_bad_command(capsys):
    assert O.main(["o", "frobnicate"]) == 2
    assert capsys.readouterr().err.strip() == "Command not recognised. Run with --help to see the commands."

def test_line_rf_scope():
    test_scope_of_practice_refuses_other_discipline()

def test_line_rf_unregistered():
    test_unregistered_role_refused()

def test_line_rf_clin_agent():
    test_clinician_agent_rules()

def test_untick_and_cancel_need_right_state():
    e = env()
    assert e.step({"type": "untick", "id": "book_followup", "ts": TS})["reason"] == "ERR:not_done"
    e.step(tick("book_followup"))
    assert e.step(cancel("book_followup"))["reason"] == "ERR:already_closed"
    e.step(cancel("write_summary"))
    assert e.step(cancel("write_summary"))["reason"] == "ERR:already_closed"
    assert e.step(tick("write_summary"))["reason"] == "ERR:already_closed"


STRICT_WRONG = [1, 0, "yes", "false", "true", None, 1.0, 0.0, "", [], {}]

@pytest.mark.parametrize("k", ["safety_critical", "agent_doable"])
@pytest.mark.parametrize("v", STRICT_WRONG, ids=repr)
def test_s06_yes_no_fields_strict_bool(k, v):
    assert bad(lambda d: d["job"][0].update({k: v})) == msg("RF-NOT-BOOL")

@pytest.mark.parametrize("k", ["safety_critical", "agent_doable"])
@pytest.mark.parametrize("v", [True, False])
def test_s06_yes_no_fields_real_bool_accepted(k, v):
    d = copy.deepcopy(L); d["job"][0][k] = v
    if k == "safety_critical" and not v:
        d["job"][0].pop("disciplines", None)
    O.check_list(d)

def test_not_overdue_at_exact_due_time():
    flags = env().check("1936-05-02 15:00")["flags"]
    assert not any(f.startswith("Overdue: Patient leaves the ward") for f in flags)


# ---- round 5 ----
def act(t):
    return {"type": t, "ts": TS}

def fail_n(e, n):
    for _ in range(n):
        r = e.step(act("submit")); assert r["accepted"] and r["final"] is False

def relog(e, role, discipline=None, scope=()):
    return env(role, discipline, scope, approve=None, log=e.log)

AGENT_ROLES = [("patient_agent", None, ()), ("clinician_agent", None, ("medicine",))]
ALL_ROLES = AGENT_ROLES + [("patient", None, ()), ("clinician", "medicine", ())]

def test_r5_01_call_permissions_table():
    assert O.CALL_PERMS == {r: {"observe", "step"} for r in ("patient", "patient_agent", "clinician", "clinician_agent")}

@pytest.mark.parametrize("role,disc,scope", ALL_ROLES)
@pytest.mark.parametrize("method", ["reset", "replay", "check", "export", "state"])
def test_r5_01_harness_only_calls_refused(role, disc, scope, method):
    e = env(role, disc, scope); n = len(e.log)
    assert e.call(method, []) == {"accepted": False, "reason": msg("RF-HARNESS-ONLY")} and len(e.log) == n

@pytest.mark.parametrize("role,disc,scope", AGENT_ROLES)
def test_r5_01_agents_may_observe_and_step(role, disc, scope):
    e = env(role, disc, scope)
    assert e.call("observe")["list_id"] and e.call("step", tick("book_followup"))["accepted"]

def test_r5_02_failed_submission_continues_and_is_logged():
    e = env(); r = e.step(act("submit"))
    assert r == {"accepted": True, "result": msg("ST-CHECK-FAIL"), "final": False, "missing": e.check()["blocked"]}
    assert e.log[-1]["result"] == "fail" and e.log[-1]["missing"] == ORDER
    assert e.step(tick("review_meds"))["accepted"] and e.check()["submissions"] == 1

def test_r5_02_pass_is_final():
    e = env()
    for i in ORDER:
        assert e.step(tick(i))["accepted"]
    assert e.step(act("submit")) == {"accepted": True, "result": msg("ST-CHECK-PASS"), "final": True}
    assert e.check()["final"] and e.step({"type": "untick", "id": "discharge", "ts": TS})["accepted"] is False

def test_r5_02_closed_counts_as_pass():
    e = env()
    for i in ORDER[:-1]:
        e.step(tick(i))
    e.step(cancel("discharge"))
    assert e.step(act("submit"))["final"] is True

@pytest.mark.parametrize("role,disc,scope", ALL_ROLES)
def test_r5_02_every_role_may_submit(role, disc, scope):
    e = env(role, disc, scope); assert e.step(act("submit"))["accepted"] and e.log[-1]["role"] == role

def test_r5_02_three_fails_queue_and_wait():
    e = env("clinician_agent", scope=("medicine",)); fail_n(e, 2)
    assert e.check()["clinician_queue"] == [] and e.step(tick("book_followup"))["accepted"]
    fail_n(e, 1)
    texts = ", ".join(e.jobs[i]["text"] for i in ORDER if i != "book_followup")
    assert e.check()["clinician_queue"] == [msg("CQ-THREE-FAILS", items=texts)]
    for a in (act("submit"), tick("write_summary")):
        assert e.step(a) == {"accepted": False, "reason": msg("RF-WAIT-CLINICIAN")}
    assert e.check()["submissions"] == 3

def test_r5_02_clinician_review_unblocks_and_clinician_can_finish():
    e = env("patient_agent"); fail_n(e, 3)
    c = relog(e, "clinician", "medicine")
    assert c.step(tick("review_meds"))["accepted"]                 # a clinician can always finish
    assert c.step(act("review"))["accepted"] and c.check()["clinician_queue"] == []
    assert relog(c, "patient_agent").step(act("submit"))["accepted"]
    assert env().step(act("review"))["reason"] == "ERR:nothing_to_review"
    assert relog(e, "patient").step(act("review"))["accepted"] is False

def test_r5_02_submission_result_cannot_be_forged_and_replays():
    e = env(); assert e.step(dict(act("submit"), result="pass"))["accepted"] is False
    e.step(act("submit")); assert relog(e, "patient").log == e.log
    bad = [dict(x) for x in e.log]; bad[-1]["result"] = "pass"
    with pytest.raises(Invalid):
        env(approve=None, log=bad)

def test_r5_03_observe_patient_sees_whole_list():
    e = env("patient"); assert e.observe()["open"] == ORDER and e.observe("review_meds")["accepted"]

def test_r5_03_observe_patient_agent_only_agent_doable_jobs():
    e = env("patient_agent")
    assert e.observe()["open"] == ["write_summary", "book_followup", "send_summary"]
    assert e.observe()["next"] == ["book_followup"]
    assert e.observe("review_meds") == {"accepted": False, "reason": msg("RF-NOT-YOURS")}
    assert e.observe("book_followup")["accepted"]

def test_r5_03_observe_clinician_sees_all():
    e = env("clinician", "nursing", approve="medicine"); assert e.observe()["open"] == ORDER

def test_r5_03_observe_clinician_agent_only_its_scope():
    d = copy.deepcopy(L); d["job"][3]["disciplines"] = ["nursing"]
    e = env("clinician_agent", scope=("medicine",), case=d)
    assert "remove_cannula" not in e.observe()["open"] and e.observe("remove_cannula")["reason"] == msg("RF-NOT-YOURS")
    assert e.observe("review_meds")["accepted"]
    assert env("clinician_agent", scope=("nursing",)).observe() == {"accepted": False, "reason": msg("RF-NOT-YOURS")}
    assert env("clinician_agent", scope=("nursing",), case=d).observe("remove_cannula")["reason"] == msg("RF-NOT-YOURS")

def test_r5_03_not_yours_does_not_reveal_existence():
    e = env("patient_agent")
    assert e.observe("review_meds") == e.observe("no_such_job") == env("clinician").observe("no_such_job")

def test_r5_04_export_refuses_real_episode():
    with pytest.raises(Invalid) as x:
        env(ep="EP-0001").export()
    assert str(x.value) == msg("RF-EXPORT-REAL")
    assert env().export()["log"]

def test_r5_04_cli_export_refuses_real_episode(tmp_path, capsys):
    log = str(tmp_path / "l.jsonl"); open(log, "w").close()
    assert O.main(["o", "export", EX, log, "EP-1"]) == 2 and capsys.readouterr().err.strip() == msg("RF-EXPORT-REAL")
    assert O.main(["o", "export", EX, log, "SYNTH-EP-1"]) == 0

def test_unapproved_list_blocked_line_first():
    assert env(approve=None).check()["blocked"][0] == msg("RF-NOT-APPROVED")
    assert msg("RF-NOT-APPROVED") not in env().check()["blocked"]

def test_r5_03_observe_item_shows_done_and_cancelled():
    e = env(); e.step(tick("book_followup")); e.step(cancel("teach_meds"))
    assert e.observe("book_followup")["done"] is True and e.observe("book_followup")["cancelled"] is False
    assert e.observe("teach_meds")["cancelled"] is True and e.observe("teach_meds")["done"] is False
