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
    a = O.OrdersEnv(); a.reset(case, "SYNTH-dr-01", "clinician", ep, disc); a.open_episode(True, TS)
    assert a.step({"type": "approve", "timestamp": TS})["accepted"]
    return a.log

AGENT, PATIENT = "SYNTH-person-01", "SYNTH-pt-01"

NAME = "Robin (assistant)"

def grant_ev(t="grant", agent=AGENT, patient=PATIENT, ts=TS, name=NAME):
    e = {"type": t, "timestamp": ts, "agent": agent, "patient": patient}
    if t == "grant":
        e["agent_name"] = name
    return e

def granted(log, case=EX, ep=EP):
    p = O.OrdersEnv(); p.reset(case, PATIENT, "patient", ep); p.replay(log)
    assert p.step(grant_ev())["accepted"]
    return p.log

def env(role="clinician", discipline="medicine", scope=(), case=EX, ep=EP, approve="medicine", log=None, grant=None):
    if role != "clinician":
        discipline = None
    base = approval(case, approve, ep) if approve else []
    if approve and (grant if grant is not None else role == "patient_agent"):
        base = granted(base, case, ep)
    actor = PATIENT if role == "patient" else AGENT
    e = O.OrdersEnv(); e.reset(case, actor, role, ep, discipline, scope)
    e.replay(base + (log or []))
    return e

def tick(i, ev=REC):
    return {"type": "tick", "id": i, "timestamp": TS, "evidence": ev}

def cancel(i, reason="SYNTH reason"):
    return {"type": "cancel", "id": i, "timestamp": TS, "reason": reason}

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
    for a in ({"type": "tick", "id": "review_meds", "timestamp": TS}, tick("review_meds", "  ")):
        assert e.step(a)["reason"] == msg("RF-EVIDENCE")
    assert len(e.log) == n

def test_cancel_requires_reason():
    e = env()
    assert e.step({"type": "cancel", "id": "book_followup", "timestamp": TS})["reason"] == msg("RF-CANCEL-REASON")
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
    assert ev["id"] == "review_meds" and ev["actor"] == "SYNTH-dr-01" and ev["timestamp"] == TS

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
    assert e.step({"type": "untick", "id": "review_meds", "timestamp": TS})["reason"] == "ERR:dependent_done"
    assert e.step({"type": "untick", "id": "teach_meds", "timestamp": TS})["accepted"]
    assert e.step({"type": "untick", "id": "review_meds", "timestamp": TS})["accepted"]

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
    for a in ({"type": "erase"}, tick("ghost"), {"type": "tick", "id": "review_meds", "timestamp": 1936, "evidence": REC}):
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
        "patient": {"tick", "untick", "result", "grant", "revoke", "submit", "next"},
        "patient_agent": {"tick", "result", "submit", "next"},
        "clinician": {"tick", "untick", "cancel", "result", "approve", "submit", "review", "next"},
        "clinician_agent": {"tick", "result", "submit", "next"}}
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
    assert a.step({"type": "approve", "timestamp": TS})["reason"] == msg("RF-CLIN-AGENT")
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
    assert env("patient").step(dict(tick("book_followup"), role="clinician"))["reason"] == msg("PV-CARE-TEAM-STEP")

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
    assert env().step({"type": "cancel", "id": "discharge", "timestamp": TS})["reason"] == "Refused: cancelling needs a reason."

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
    assert e.step({"type": "untick", "id": "book_followup", "timestamp": TS})["reason"] == "ERR:not_done"
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
    return {"type": t, "timestamp": TS}

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
    assert e.check()["final"] and e.step({"type": "untick", "id": "discharge", "timestamp": TS})["accepted"] is False

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
    texts = "; ".join(f"{n}. {e.jobs[i]['text']}" for n, i in enumerate(ORDER, 1) if i != "book_followup")
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

def opened(synthetic, ep=EP):
    e = O.OrdersEnv(); e.reset(EX, "SYNTH-dr-01", "clinician", ep, "medicine"); e.open_episode(synthetic, TS)
    assert e.step({"type": "approve", "timestamp": TS})["accepted"]
    return e

def test_r8_01_export_needs_synthetic_flag_not_id():
    e = env(approve=None, log=[x for x in opened(True).log if x["type"] != "open"])
    with pytest.raises(Invalid) as x:
        e.export()
    assert str(x.value) == msg("RF-EXPORT-REAL")
    with pytest.raises(Invalid) as x:
        opened(False).export()
    assert str(x.value) == msg("RF-EXPORT-REAL")
    assert opened(True, "EP-0001").export()["synthetic"] is True     # the field decides, never the id

def test_r8_01_flag_set_once_first_and_never_changed():
    e = opened(True)
    for v in (True, False):
        with pytest.raises(Invalid):
            e.open_episode(v, TS)
    for v in (1, "true", None):
        f = O.OrdersEnv(); f.reset(EX, "SYNTH-x", "patient", EP)
        with pytest.raises(Invalid):
            f.open_episode(v, TS)

@pytest.mark.parametrize("change", ["second_open", "flip", "not_first", "extra_key", "other_episode"])
def test_r8_01_replay_refuses_changed_flag(change):
    log = [dict(x) for x in opened(False).log]
    if change == "second_open":
        log.append(dict(log[0], synthetic=True))
    elif change == "flip":
        log.insert(1, dict(log[0], synthetic=True))
    elif change == "not_first":
        log = log[1:] + log[:1]
    elif change == "extra_key":
        log[0]["actor"] = "SYNTH-x"
    else:
        log[0]["episode_id"] = "SYNTH-EP-9"
    with pytest.raises(Invalid):
        env(approve=None, log=log)

@pytest.mark.parametrize("role,disc,scope", ALL_ROLES)
def test_r8_01_only_harness_sets_flag(role, disc, scope):
    e = env(role, disc, scope); n = len(e.log)
    assert e.call("open_episode", True, TS) == {"accepted": False, "reason": msg("RF-HARNESS-ONLY")}
    assert e.step({"type": "open", "synthetic": True, "timestamp": TS})["accepted"] is False and len(e.log) == n

def test_r8_01_cli_open_and_export(tmp_path, capsys):
    log = str(tmp_path / "l.jsonl"); open(log, "w").close()
    assert O.main(["o", "export", EX, log, "SYNTH-EP-1"]) == 2 and capsys.readouterr().err.strip() == msg("RF-EXPORT-REAL")
    assert O.main(["o", "open", EX, log, "SYNTH-EP-1", "--synthetic", "yes", "--ts", TS]) == 2
    assert O.main(["o", "open", EX, log, "SYNTH-EP-1", "--synthetic", "true", "--ts", TS]) == 0
    assert O.main(["o", "open", EX, log, "SYNTH-EP-1", "--synthetic", "false", "--ts", TS]) == 2
    assert O.main(["o", "export", EX, log, "SYNTH-EP-1"]) == 0 and json.loads(capsys.readouterr().out)["synthetic"] is True

# ---- grants on lists ----
def test_r8_02_patient_agent_without_grant_sees_and_does_nothing():
    e = env("patient_agent", grant=False)
    assert e.observe() == {"accepted": False, "reason": msg("RF-NOT-YOURS")}
    assert e.observe("book_followup") == e.observe("no_such_job")
    assert e.step(tick("book_followup")) == {"accepted": False, "reason": msg("RF-NOT-YOURS")}
    assert e.step(act("submit"))["reason"] == msg("RF-NOT-YOURS")

def test_r8_02_grant_shows_only_agent_doable_non_safety_jobs():
    e = env("patient_agent")
    assert e.observe()["open"] == ["write_summary", "book_followup", "send_summary"]
    assert e.observe("review_meds")["reason"] == msg("RF-NOT-YOURS") and e.step(tick("book_followup"))["accepted"]

def test_r8_02_grant_names_agent_and_patient():
    p = env("patient", approve="medicine")
    assert p.step(grant_ev(agent=""))["accepted"] is False
    assert p.step(grant_ev(patient="SYNTH-pt-99"))["accepted"] is False
    assert p.step(grant_ev())["accepted"] and p.log[-1]["agent"] == AGENT and p.log[-1]["patient"] == PATIENT
    q = O.OrdersEnv(); q.reset(EX, "SYNTH-pt-99", "patient", EP); q.replay(p.log)
    assert q.step(grant_ev(agent="SYNTH-agent-09", patient="SYNTH-pt-99"))["accepted"] is False

def test_r8_02_other_agent_not_covered():
    e = env("patient_agent")
    o = O.OrdersEnv(); o.reset(EX, "SYNTH-agent-02", "patient_agent", EP); o.replay(e.log)
    assert o.step(tick("book_followup"))["reason"] == msg("RF-NOT-YOURS")
    assert o.observe()["reason"] == msg("RF-NOT-YOURS")

def test_r8_02_only_patient_grants():
    for role, disc, scope in (("clinician", "medicine", ()), ("clinician_agent", None, ("medicine",)), ("patient_agent", None, ())):
        e = env(role, disc, scope); n = len(e.log)
        assert e.step(grant_ev())["accepted"] is False and e.step(grant_ev("revoke"))["accepted"] is False and len(e.log) == n

def test_r8_02_revoke_works_the_same_way():
    e = env("patient_agent")
    p = env("patient", approve=None, log=e.log)
    assert p.step(grant_ev("revoke", agent="SYNTH-agent-02"))["accepted"] is False
    assert p.step(grant_ev("revoke", ts="1936-05-03T09:00"))["accepted"]
    assert p.step(grant_ev("revoke"))["accepted"] is False
    a = env("patient_agent", approve=None, log=p.log)
    assert a.step(tick("book_followup")) == {"accepted": False, "reason": msg("RF-REVOKED", date="1936-05-03")}
    assert a.observe()["reason"] == msg("RF-NOT-YOURS")
    assert p.next_view()[0] == msg("PV-REVOKE-LIST", agent_name=NAME, date="1936-05-03")

def test_r8_02_patient_sees_grant_line():
    e = env("patient_agent")
    p = env("patient", approve=None, log=e.log)
    assert p.next_view()[0] == msg("PV-GRANT-LIST", agent_name=NAME, date="1936-05-02")
    assert env("patient").next_view()[0] != msg("PV-GRANT-LIST", agent_name=NAME, date="1936-05-02")

def test_r8_02_cli_grant_names_agent(tmp_path):
    log = str(tmp_path / "l.jsonl")
    with open(log, "w") as f:
        f.writelines(json.dumps(x) + "\n" for x in approval())
    base = ["o", "grant", EX, log, EP, "--role", "patient", "--actor", PATIENT, "--ts", TS]
    assert O.main(base) == 2 and O.main(base + ["--agent", AGENT]) == 2
    assert O.main(base + ["--agent", AGENT, "--agent-name", NAME]) == 1
    assert O.read_log(log)[-1]["agent"] == AGENT and O.read_log(log)[-1]["agent_name"] == NAME

# ---- queue text ----
def test_r8_03_queue_lists_number_and_text():
    e = env(); fail_n(e, 3)
    want = "; ".join(f"{n}. {e.jobs[i]['text']}" for n, i in enumerate(ORDER, 1))
    assert e.check()["clinician_queue"] == [msg("CQ-THREE-FAILS", items=want)]

# ---- interpreter ----
def test_r8_04_interpreter_clinician_only():
    c = env(); assert c.step(dict(tick("review_meds"), interpreter=True))["accepted"] and c.log[-1]["interpreter"] is True
    for role, disc, scope in (("patient", None, ()), ("patient_agent", None, ()), ("clinician_agent", None, ("medicine",))):
        e = env(role, disc, scope); n = len(e.log)
        assert e.step(dict(tick("book_followup"), interpreter=False))["accepted"] is False and len(e.log) == n

@pytest.mark.parametrize("v", [1, 0, "true", None])
def test_r8_04_interpreter_strict_bool(v):
    assert env().step(dict(tick("review_meds"), interpreter=v)) == {"accepted": False, "reason": msg("RF-NOT-BOOL")}

def test_r8_04_interpreter_only_on_tick():
    assert env().step(dict(cancel("book_followup"), interpreter=True))["accepted"] is False

def test_r8_04_export_carries_interpreter_and_replays(tmp_path, capsys):
    e = opened(True); e.step(dict(tick("review_meds"), interpreter=True)); e.step(tick("book_followup"))
    assert e.export()["interpreter"] == {"review_meds": True, "book_followup": False}
    e.step({"type": "untick", "id": "book_followup", "timestamp": TS})
    assert e.export()["interpreter"] == {"review_meds": True}
    f = env(approve=None, log=e.log); assert f.export()["interpreter"] == {"review_meds": True}
    log = str(tmp_path / "l.jsonl")
    with open(log, "w") as fh:
        fh.writelines(json.dumps(x) + "\n" for x in e.log[:2])
    assert cli(log, "tick", "review_meds", evidence=REC, interpreter="true") == 1; capsys.readouterr()
    assert cli(log, "tick", "book_followup", evidence=REC, interpreter="yes") == 2
    assert O.main(["o", "export", EX, log, EP]) == 0
    assert json.loads(capsys.readouterr().out)["interpreter"] == {"review_meds": True}


def test_unapproved_list_blocked_line_first():
    assert env(approve=None).check()["blocked"][0] == msg("RF-NOT-APPROVED")
    assert msg("RF-NOT-APPROVED") not in env().check()["blocked"]

def test_r5_03_observe_item_shows_done_and_cancelled():
    e = env(); e.step(tick("book_followup")); e.step(cancel("teach_meds"))
    assert e.observe("book_followup")["done"] is True and e.observe("book_followup")["cancelled"] is False
    assert e.observe("teach_meds")["cancelled"] is True and e.observe("teach_meds")["done"] is False


# ---- record a result on a job ----
def result(i, v="SYNTH result: within range"):
    return {"type": "result", "id": i, "value": v, "timestamp": TS}

@pytest.mark.parametrize("role,disc,scope", ALL_ROLES)
def test_r8_08_result_recorded_via_step_and_logged(role, disc, scope):
    e = env(role, disc, scope); n = len(e.log)
    assert e.call("step", result("book_followup"))["accepted"]
    assert len(e.log) == n + 1 and e.log[-1]["type"] == "result" and e.log[-1]["role"] == role
    assert e.results["book_followup"][0]["value"] == "SYNTH result: within range"

def test_r8_08_results_append_and_replay():
    e = env(); e.step(result("review_meds", "SYNTH first")); e.step(tick("review_meds")); e.step(result("review_meds", "SYNTH second"))
    f = env(approve=None, log=e.log)
    assert [r["value"] for r in f.results["review_meds"]] == ["SYNTH first", "SYNTH second"] and f.log == e.log
    bad = [dict(x) for x in e.log]; bad[-1]["value"] = ""
    with pytest.raises(Invalid):
        env(approve=None, log=bad)

@pytest.mark.parametrize("v", ["", "   ", None, 7, True, "x" * 501])
def test_r8_08_result_value_must_be_text(v):
    e = env(); n = len(e.log)
    assert e.step(result("book_followup", v))["accepted"] is False and len(e.log) == n
    assert e.step(result("book_followup", "x" * 500))["accepted"]

def test_r8_08_result_refusals():
    assert env("patient_agent").step(result("review_meds"))["reason"] == msg("RF-PERSON-JOB")
    assert env("clinician_agent", scope=("medicine",)).step(result("teach_meds"))["reason"] == msg("RF-PERSON-JOB")
    d = copy.deepcopy(L); d["job"][1]["safety_critical"] = True
    assert env("patient_agent", case=d).step(result("write_summary"))["reason"] == msg("RF-NOT-YOURS")
    assert env("patient_agent", grant=False).step(result("book_followup"))["reason"] == msg("RF-NOT-YOURS")
    assert env("clinician", "nursing").step(result("review_meds"))["reason"] == msg("RF-SCOPE", disciplines="medicine")
    assert env(approve=None).step(result("book_followup"))["reason"] == msg("RF-NOT-APPROVED")
    assert env().step(result("ghost"))["reason"] == "ERR:job_id"
    c = env(); c.step(cancel("book_followup")); assert c.step(result("book_followup"))["reason"] == "ERR:already_closed"
    assert env().step({"type": "result", "id": "book_followup", "timestamp": TS})["accepted"] is False

def test_r8_08_export_carries_results_and_cli(tmp_path, capsys):
    e = opened(True); e.step(result("review_meds", "SYNTH ok"))
    assert e.export()["results"]["review_meds"][0]["value"] == "SYNTH ok"
    log = str(tmp_path / "l.jsonl")
    with open(log, "w") as fh:
        fh.writelines(json.dumps(x) + "\n" for x in e.log[:2])
    assert cli(log, "result", "review_meds", value="SYNTH cli") == 1
    assert cli(log, "result", "review_meds") == 2; capsys.readouterr()
    assert O.main(["o", "export", EX, log, EP]) == 0
    assert json.loads(capsys.readouterr().out)["results"]["review_meds"][0]["value"] == "SYNTH cli"

@pytest.mark.parametrize("bad", [42, "tick", None, {"type": "erase"}])
def test_replay_refuses_malformed_log_lines(bad):
    with pytest.raises(Invalid):
        env(approve=None, log=approval() + [bad])

def test_replay_reports_the_refusal_line():
    e = env(); e.step(tick("review_meds"))
    log = [x for x in e.log if x["type"] != "approve"]
    with pytest.raises(Invalid) as x:
        env(approve=None, log=log)
    assert str(x.value) == msg("RF-NOT-APPROVED")


# ---- grant lines carry the display name stored at grant time ----
@pytest.mark.parametrize("bad", ["", "  ", 5, None, AGENT])
def test_r10_grant_needs_display_name_not_id(bad):
    p = env("patient")
    assert p.step(grant_ev(name=bad))["accepted"] is False

def test_r10_grant_missing_display_name_refused():
    p = env("patient"); ev = grant_ev(); del ev["agent_name"]
    assert p.step(ev)["accepted"] is False

def test_r10_revoke_line_uses_name_stored_at_grant():
    e = env("patient_agent")
    p = env("patient", approve=None, log=e.log)
    assert p.step(grant_ev("revoke", ts="1936-05-03T09:00"))["accepted"]
    line = p.next_view()[0]
    assert NAME in line and AGENT not in line

def test_r10_no_grant_refusal_names_agent_from_stored_name():
    e = env("patient_agent")
    p = env("patient", approve=None, log=e.log)
    assert p.step(grant_ev("revoke", ts="1936-05-03T09:00"))["accepted"]
    a = env("patient_agent", approve=None, log=p.log)
    assert a.no_grant_line(AGENT) == msg("RF-NO-GRANT-LIST", agent_name=NAME)
    assert a.no_grant_line("SYNTH-agent-02") == msg("RF-NOT-YOURS")


# ---- build checks refuse wrong types with a line, never a crash (mutation) ----
@pytest.mark.parametrize("field,item,mut", [
    ("list_id", "list", lambda d: d.update(list_id=7)),
    ("version", "list", lambda d: d.update(version=7)),
    ("source_lines", "list", lambda d: d.update(source_lines="7")),
    ("source_lines", "list", lambda d: d.update(source_lines=-1)),
    ("record", "list", lambda d: d.update(record=5)),
    ("record", "list", lambda d: d.update(record=[5])),
    ("record", "list", lambda d: d.update(record=[{"id": "x"}])),
    ("record", "list", lambda d: d.update(record=[{"id": "", "kind": "k"}])),
    ("record", "list", lambda d: d.update(record=[{"id": 5, "kind": "k"}])),
    ("job", "list", lambda d: d.update(job=5)),
    ("job", "list", lambda d: d.update(job=[])),
    ("id", "job 1", lambda d: d["job"][0].update(id=7)),
    ("text", "job 1", lambda d: d["job"][0].update(text=7)),
    ("owner", "job 1", lambda d: d["job"][0].update(owner="  ")),
    ("due", "job 1", lambda d: d["job"][0].update(due=5)),
    ("due", "job 1", lambda d: d["job"][0].update(due="soon")),
    ("disciplines", "job 2", lambda d: d["job"][1].update(disciplines=["x"])),
    ("disciplines", "job 1", lambda d: d["job"][0].update(disciplines="x")),
    ("disciplines", "job 1", lambda d: d["job"][0].update(disciplines=[])),
    ("disciplines", "job 1", lambda d: d["job"][0].update(disciplines=[""])),
    ("disciplines", "job 1", lambda d: d["job"][0].update(disciplines=[5])),
    ("depends_on", "job 1", lambda d: d["job"][0].update(depends_on="ab")),
    ("depends_on", "job 1", lambda d: d["job"][0].update(depends_on=[5])),
    ("depends_on", "job 2", lambda d: d["job"][1].update(depends_on=["review_meds", "review_meds"])),
])
def test_build_refuses_wrong_types(field, item, mut):
    assert bad(mut) == msg("ER-BAD-FIELD", field=field, item=item)

def test_list_hash_is_key_order_free_and_utf8():
    want = __import__("hashlib").sha256('{"a": 1, "b": "é"}'.encode()).hexdigest()
    assert O.list_hash({"b": "é", "a": 1}) == want

def test_cli_commands_need_exact_positions():
    assert O.main(["o", "compile", EX, "extra"]) == 2
    assert O.main(["o", "status", EX]) == 2

def test_step_return_shapes():
    e = env("clinician")
    assert e.step({"type": "next"})["accepted"] is True
    f = e.step(act("submit"))
    assert f["accepted"] is True and f["final"] is False


# ---- round 11: no machine code ever reaches a patient screen ----
PEX = sorted(str(p) for p in (ROOT / "examples").glob("*.toml"))

def _patient_attempts(e):
    jobs = list(e.jobs) + ["no_such_job"]
    yield "next", {"type": "next"}
    for t in sorted(O.FIELDS):
        if t in ("grant", "revoke"):
            for label, kw in (("valid", {}), ("no agent", {"agent": ""}), ("other patient", {"patient": "SYNTH-pt-99"}),
                              ("bad display name", {"name": ""}), ("id as display name", {"name": AGENT})):
                if t == "revoke" and "name" in kw:
                    continue
                yield f"{t} ({label})", grant_ev(t, **kw)
        elif "id" in O.FIELDS[t]:
            for j in jobs:
                a = {"type": t, "timestamp": TS, "id": j}
                if t == "tick":
                    a["evidence"] = REC
                if t == "cancel":
                    a["reason"] = "SYNTH reason"
                if t == "result":
                    a["value"] = "SYNTH result"
                yield f"{t} {j}", a
            if t == "tick":
                yield "tick with unknown evidence", {"type": t, "timestamp": TS, "id": jobs[0], "evidence": "SYNTH-REC-9999"}
            if t == "result":
                yield "result empty value", {"type": t, "timestamp": TS, "id": jobs[0], "value": ""}
        else:
            yield t, {"type": t, "timestamp": TS}

def _patient_refusals():
    out = []
    for case in PEX:
        for approved in (True, False):
            e = env("patient", case=case, approve="medicine" if approved else None)
            j0 = list(e.jobs)[0]
            malformed = [("bad type", {"type": "zzz", "timestamp": TS}), ("extra field", dict(tick(j0), zzz=1)),
                         ("bad timestamp", dict(tick(j0), timestamp="x")), ("role key", dict(tick(j0), role="clinician")),
                         ("interpreter", dict(tick(j0), interpreter=True))]
            for how, a in list(_patient_attempts(e)) + malformed:
                f = env("patient", case=case, approve=None, log=list(e.log))
                r = f.step(a)
                if not r["accepted"]:
                    out.append((pathlib.Path(case).name, "approved" if approved else "unapproved", how, r["reason"]))
    return out

def test_r11_no_err_on_patient_screens():
    bad = [x for x in _patient_refusals() if x[3].startswith("ERR:") or not x[3]]   # a blank reason hides a code
    assert bad == []


def test_r11_synth_display_name_accepted_and_id_refused():
    p = env("patient")
    assert p.step(grant_ev(name=AGENT))["reason"] == msg("PV-GRANT-NAMES-AGENT")
    assert p.step(grant_ev(name="SYNTH-Robin"))["accepted"] and p.log[-1]["agent_name"] == "SYNTH-Robin"

OTHER_ID, OTHER_NAME = "SYNTH-pt-77", "SYNTH-Other Person"

def test_r11_other_patient_never_shown():
    p = env("patient")
    for a in (grant_ev(patient=OTHER_ID, name=OTHER_NAME), grant_ev("revoke", patient=OTHER_ID),
              grant_ev(agent=OTHER_ID, name=OTHER_NAME, patient=OTHER_ID)):
        r = p.step(a)
        assert r == {"accepted": False, "reason": msg("PV-ONLY-YOU")}
    seen = json.dumps([p.observe(), p.next_view(), p.check(), p.log])
    assert OTHER_ID not in seen and OTHER_NAME not in seen

def test_r11_patient_lines_for_each_code():
    p = env("patient")
    assert p.step(grant_ev("revoke"))["reason"] == msg("PV-NOTHING-TO-REVOKE")
    assert p.step(grant_ev(name=""))["reason"] == msg("PV-GRANT-NAMES-AGENT")
    assert p.step(grant_ev(agent=""))["reason"] == msg("PV-GRANT-NAMES-AGENT")
    assert p.step({"type": "untick", "timestamp": TS, "id": "no_such_job"})["reason"] == msg("PV-JOB-NOT-FOUND")
    assert p.step({"type": "untick", "timestamp": TS, "id": "review_meds"})["reason"] == msg("PV-NOT-DONE")
    assert p.step({"type": "result", "timestamp": TS, "id": "book_followup", "value": ""})["reason"] == msg("PV-RESULT-EMPTY")
    c = env("clinician")
    assert c.step({"type": "untick", "timestamp": TS, "id": "review_meds"})["reason"] == O.code("not_done")


def test_r11_role_key_refusal_changes_nothing():
    p = env("patient")
    before = (copy.deepcopy(p.log), p.session, p.check(), p.next_view())
    for k, v in (("role", "clinician"), ("actor", "SYNTH-dr-01"), ("discipline", "medicine"), ("scope", ["medicine"])):
        r = p.step(dict(tick("book_followup"), **{k: v}))
        assert r == {"accepted": False, "reason": msg("PV-CARE-TEAM-STEP")}
    assert (p.log, p.session, p.check(), p.next_view()) == before and p.session.role == "patient"

def test_r11_sweep_malformed_role_key_shows_signed_line():
    for case in PEX:
        r = env("patient", case=case).step(dict(act("submit"), role="clinician"))
        assert r["reason"] and not r["reason"].startswith("ERR:")


# ---- round 11: every patient refusal leaves everything unchanged; malformed actions never echo ----
BADF, BADV = "zzz_secret_field", "SYNTH-bad-value-937"

def _as_patient(e):
    return env("patient", approve=None, log=list(e.log))

def _clin_ticked(ids):
    c = env()
    for i in ids:
        assert c.step(tick(i))["accepted"]
    return c

def _passed():
    c = _clin_ticked(ORDER); assert c.step(act("submit"))["final"]; return _as_patient(c)

def _granted():
    p = env("patient"); p.step(grant_ev()); return p

UNT = lambda i: {"type": "untick", "id": i, "timestamp": TS}
CASES = {
    "action_type": (lambda: env("patient"), {"type": BADV, "timestamp": TS}),
    "fields": (lambda: env("patient"), dict(tick("book_followup"), **{BADF: BADV})),
    "timestamp": (lambda: env("patient"), dict(tick("book_followup"), timestamp=BADV)),
    "interpreter_clinician_only": (lambda: env("patient"), dict(tick("book_followup"), interpreter=True)),
    "final": (_passed, UNT("discharge")),
    "already_closed": (lambda: _as_patient(_clin_ticked(["review_meds", "write_summary"])), tick("write_summary")),
    "dependent_done": (lambda: _as_patient(_clin_ticked(["review_meds", "write_summary"])), UNT("review_meds")),
    "role_fixed_at_session_open": (lambda: env("patient"), dict(tick("book_followup"), role="clinician")),
    "nothing_to_revoke": (lambda: env("patient"), grant_ev("revoke")),
    "grant_needs_display_name": (lambda: env("patient"), grant_ev(name="")),
    "grant_names_agent": (lambda: env("patient"), grant_ev(agent="")),
    "job_id": (lambda: env("patient"), UNT("no_such_job")),
    "not_done": (lambda: env("patient"), UNT("book_followup")),
    "grant_names_this_patient": (lambda: env("patient"), grant_ev(patient="SYNTH-pt-77", name="SYNTH-Other")),
    "result_value": (lambda: env("patient"), {"type": "result", "timestamp": TS, "id": "book_followup", "value": ""}),
}

def test_r11_cases_cover_every_mapped_code():
    assert set(CASES) == set(O.PATIENT_LINE)

@pytest.mark.parametrize("c", sorted(CASES))
def test_r11_patient_refusal_changes_nothing(c):
    mk, a = CASES[c]
    p = mk()
    before = (copy.deepcopy(p.log), p.session, p.check(), p.next_view(), p.observe())
    r = p.step(a)
    assert r == {"accepted": False, "reason": msg(O.PATIENT_LINE[c])}
    assert (p.log, p.session, p.check(), p.next_view(), p.observe()) == before and p.session.role == "patient"

@pytest.mark.parametrize("c", ["action_type", "fields", "timestamp"])
def test_r11_malformed_action_never_echoes(c):
    p = CASES[c][0](); r = p.step(CASES[c][1])
    seen = json.dumps([r, p.next_view(), p.observe(), p.check(), p.log])
    assert BADF not in seen and BADV not in seen and "timestamp" not in json.dumps([r, p.next_view()])


# The signed code -> patient line mapping, pinned here independently of PATIENT_LINE (ids only).
EXPECTED_LINE = {
    "role_fixed_at_session_open": "PV-CARE-TEAM-STEP",
    "action_type": "PV-TRY-AGAIN",
    "fields": "PV-TRY-AGAIN",
    "timestamp": "PV-TRY-AGAIN",
    "interpreter_clinician_only": "PV-CARE-TEAM-STEP",
    "final": "PV-ALREADY-FINAL",
    "already_closed": "PV-ALREADY-FINAL",
    "dependent_done": "PV-STEP-AFTER-DONE",
    "nothing_to_revoke": "PV-NOTHING-TO-REVOKE",
    "grant_needs_display_name": "PV-GRANT-NAMES-AGENT",
    "grant_names_agent": "PV-GRANT-NAMES-AGENT",
    "job_id": "PV-JOB-NOT-FOUND",
    "not_done": "PV-NOT-DONE",
    "grant_names_this_patient": "PV-ONLY-YOU",
    "result_value": "PV-RESULT-EMPTY",
}

def test_r11_mapping_pinned():
    assert O.PATIENT_LINE == EXPECTED_LINE

@pytest.mark.parametrize("c", sorted(EXPECTED_LINE))
def test_r11_each_code_shows_its_signed_line(c):
    mk, a = CASES[c]
    p = mk()
    if a is None:
        a = ans(p.ids[0], True)
    assert p.step(a)["reason"] == msg(EXPECTED_LINE[c])

def test_r11_refuse_passes_unmapped_codes_through_unchanged():
    p = env("patient")
    assert p._refuse("ERR:zzz_unmapped") == {"accepted": False, "reason": "ERR:zzz_unmapped"}
    assert p._refuse(msg("RF-NOT-YOURS")) == {"accepted": False, "reason": msg("RF-NOT-YOURS")}
