import pathlib, sys
import pytest
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import orders as O

CASE = str(ROOT / "examples" / "ward_discharge.toml")
TS = "1936-05-02T10:00"

def scripted_agent(env):
    """Deterministic agent: always tick the first available job until none are left."""
    while (nxt := env.step({"type": "next"})["next"]):
        assert env.step({"type": "tick", "id": nxt[0], "ts": TS})["accepted"]

def test_scripted_episode_and_replay_equality():
    env = O.OrdersEnv(); env.reset(CASE, "SYNTH-dr-01", "clinician", "SYNTH-EP-0001")
    scripted_agent(env)
    c = env.check()
    assert c["done"] and c["open"] == [] and c["blocked"] == {}
    assert [e["action"]["id"] for e in env.log][-1] == "discharge"
    fresh = O.OrdersEnv(); fresh.reset(CASE, "SYNTH-dr-01", "clinician", "SYNTH-EP-0001")
    assert fresh.replay(env.log) == env.state() and fresh.check() == c and fresh.log == env.log

def test_nurse_cannot_cancel_safety_critical():
    env = O.OrdersEnv(); env.reset(CASE, "SYNTH-rn-01", "nurse", "SYNTH-EP-0002")
    r = env.step({"type": "cancel", "id": "remove_cannula", "ts": TS})
    assert not r["accepted"] and "clinician only" in r["reason"] and env.log == []
    assert env.step({"type": "cancel", "id": "book_followup", "ts": TS})["accepted"]
    assert "blocked_by_cancelled:discharge" in env.check()["flags"]  # discharge needs book_followup
    assert "cancelled:book_followup" in env.check()["flags"]

def test_clinician_cancel_flags_dependents():
    env = O.OrdersEnv(); env.reset(CASE, "SYNTH-dr-01", "clinician", "SYNTH-EP-0003")
    assert env.step({"type": "cancel", "id": "remove_cannula", "ts": TS})["accepted"]
    assert "blocked_by_cancelled:discharge" in env.check()["flags"]

@pytest.mark.parametrize("a,why", [
    ({"type": "tick", "id": "discharge", "ts": TS}, "waits for"),
    ({"type": "untick", "id": "review_meds", "ts": TS}, "not done"),
    ({"type": "tick", "id": "ghost", "ts": TS}, "unknown job"),
    ({"type": "tick", "id": "review_meds"}, "exactly"),
    ({"type": "tick", "id": "review_meds", "ts": 1936}, "ISO"),
    ({"type": "erase"}, "unknown action"),
])
def test_gate_rejections_do_not_log(a, why):
    env = O.OrdersEnv(); env.reset(CASE, "x", "agent", "SYNTH-EP-0004")
    r = env.step(a)
    assert not r["accepted"] and why in r["reason"] and env.log == []

def test_untick_blocked_by_done_dependent():
    env = O.OrdersEnv(); env.reset(CASE, "x", "agent", "SYNTH-EP-0005")
    env.step({"type": "tick", "id": "review_meds", "ts": TS}); env.step({"type": "tick", "id": "teach_meds", "ts": TS})
    assert "blocked" in env.step({"type": "untick", "id": "review_meds", "ts": TS})["reason"]
    assert env.step({"type": "untick", "id": "teach_meds", "ts": TS})["accepted"]
    assert env.step({"type": "untick", "id": "review_meds", "ts": TS})["accepted"]

def test_episode_isolation_and_foreign_log():
    a = O.OrdersEnv(); a.reset(CASE, "x", "agent", "SYNTH-EP-A")
    b = O.OrdersEnv(); b.reset(CASE, "y", "agent", "SYNTH-EP-B")
    a.step({"type": "tick", "id": "review_meds", "ts": TS})
    assert "review_meds" in b.observe()["open"]
    with pytest.raises(O.Invalid):
        b.replay(a.log)

def test_replay_rechecks_role():
    env = O.OrdersEnv(); env.reset(CASE, "x", "nurse", "SYNTH-EP-C")
    forged = [{"episode_id": "SYNTH-EP-C", "actor": "x", "role": "nurse",
               "action": {"type": "cancel", "id": "discharge", "ts": TS}}]
    with pytest.raises(O.Invalid, match="clinician only"):
        env.replay(forged)
