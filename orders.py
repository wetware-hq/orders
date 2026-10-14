#!/usr/bin/env python3
"""Checklists of jobs with dependencies. List: TOML. Log: append-only JSONL of tick/untick/cancel.
State is never stored; it is rebuilt by replaying the log through the same gate every time."""
import datetime, graphlib, json, re, sys, tomllib

ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
ROLES = {"clinician", "nurse", "patient", "agent"}
ACTIONS = {"tick": {"id", "ts"}, "untick": {"id", "ts"}, "cancel": {"id", "ts"}, "next": set()}


class Invalid(Exception):
    pass


def check_list(d):
    if set(d) != {"list_id", "job"} or not isinstance(d["list_id"], str) or not d["list_id"].strip():
        raise Invalid("list needs exactly: list_id (string) and [[job]]")
    jobs = d["job"]
    if not isinstance(jobs, list) or not jobs:
        raise Invalid("at least one [[job]] is required")
    ids = []
    for i, j in enumerate(jobs, 1):
        if not isinstance(j, dict) or not {"id", "text", "depends_on"} <= set(j) <= {"id", "text", "depends_on", "safety_critical"}:
            raise Invalid(f"job {i}: needs id, text, depends_on (safety_critical optional)")
        if not isinstance(j["id"], str) or not ID_RE.match(j["id"]):
            raise Invalid(f"job {i}: id {j['id']!r} must be lower_snake_case")
        if j["id"] in ids:
            raise Invalid(f"job {i}: duplicate id {j['id']!r}")
        ids.append(j["id"])
        if not isinstance(j["text"], str) or not j["text"].strip():
            raise Invalid(f"job {j['id']}: text must be a non-empty string")
        if type(j.get("safety_critical", False)) is not bool:
            raise Invalid(f"job {j['id']}: safety_critical must be true/false")
        dep = j["depends_on"]
        if not isinstance(dep, list) or not all(isinstance(x, str) for x in dep) or len(set(dep)) != len(dep):
            raise Invalid(f"job {j['id']}: depends_on must be a list of distinct ids")
    for j in jobs:
        for x in j["depends_on"]:
            if x not in ids:
                raise Invalid(f"job {j['id']}: depends on unknown id {x!r}")
    try:
        tuple(graphlib.TopologicalSorter({j["id"]: j["depends_on"] for j in jobs}).static_order())
    except graphlib.CycleError as e:
        raise Invalid(f"dependency cycle: {' -> '.join(e.args[1])}")
    return d


def load_list(path):
    try:
        with open(path, "rb") as f:
            return check_list(tomllib.load(f))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise Invalid(f"cannot read list: {e}")


class OrdersEnv:
    """One episode of working one checklist. step() is the only way state changes."""

    def reset(self, case_file, actor, role, episode_id):
        if role not in ROLES:
            raise Invalid(f"role must be one of {sorted(ROLES)}")
        self.lst = check_list(case_file if isinstance(case_file, dict) else load_list(case_file))
        self.actor, self.role, self.episode_id = actor, role, episode_id
        self.jobs = {j["id"]: j for j in self.lst["job"]}
        self.order = [j["id"] for j in self.lst["job"]]
        self.dependents = {i: [k for k in self.order if i in self.jobs[k]["depends_on"]] for i in self.order}
        self.done, self.cancelled, self.log = set(), set(), []
        return self.observe()

    def _why_not_tick(self, i):
        if i in self.done:
            return f"{i} already done"
        if i in self.cancelled:
            return f"{i} is cancelled"
        wait = [d for d in self.jobs[i]["depends_on"] if d not in self.done]
        return f"{i} waits for: {', '.join(wait)}" if wait else None

    def available(self):
        return [i for i in self.order if self._why_not_tick(i) is None]

    def _open(self):
        return [i for i in self.order if i not in self.done and i not in self.cancelled]

    def _flags(self):
        f = [f"cancelled:{i}" for i in self.order if i in self.cancelled]
        f += [f"blocked_by_cancelled:{i}" for i in self._open()
              if any(d in self.cancelled for d in self.jobs[i]["depends_on"])]
        return f

    def _blocked(self):
        return {i: self._why_not_tick(i) for i in self._open() if self._why_not_tick(i)}

    def observe(self):
        return {"episode_id": self.episode_id, "list_id": self.lst["list_id"], "open": self._open(),
                "next": self.available(), "flags": self._flags(), "blocked": self._blocked()}

    def _gate(self, a, role):
        if not isinstance(a, dict) or a.get("type") not in ACTIONS:
            return f"unknown action type {a.get('type') if isinstance(a, dict) else a!r}"
        t = a["type"]
        if set(a) != ACTIONS[t] | {"type"}:
            return f"{t} needs exactly fields {sorted(ACTIONS[t] | {'type'})}"
        if t == "next":
            return None
        if not isinstance(a["ts"], str):
            return "ts must be an ISO 8601 string"
        try:
            datetime.datetime.fromisoformat(a["ts"])
        except ValueError:
            return f"ts {a['ts']!r} is not ISO 8601"
        i = a["id"]
        if i not in self.jobs:
            return f"unknown job id {i!r}"
        if t == "tick":
            return self._why_not_tick(i)
        if t == "untick":
            if i not in self.done:
                return f"{i} is not done"
            on = [k for k in self.dependents[i] if k in self.done]
            return f"untick {i} blocked: done jobs depend on it: {', '.join(on)}" if on else None
        if self.jobs[i].get("safety_critical") and role != "clinician":
            return f"cancel {i} is clinician only (safety_critical)"
        if i in self.done or i in self.cancelled:
            return f"{i} is already {'done' if i in self.done else 'cancelled'}"
        return None

    def step(self, action, _role=None, _actor=None):
        role = _role or self.role
        why = self._gate(action, role)
        if why:
            return {"accepted": False, "reason": why}
        t = action["type"]
        if t == "next":
            return {"accepted": True, "next": self.available()}
        {"tick": self.done.add, "untick": self.done.discard, "cancel": self.cancelled.add}[t](action["id"])
        self.log.append({"episode_id": self.episode_id, "actor": _actor or self.actor, "role": role,
                         "action": dict(action)})
        return {"accepted": True}

    def check(self):
        return {"done": not self._open(), "open": self._open(), "next": self.available(),
                "blocked": self._blocked(), "flags": self._flags()}

    def state(self):
        return (tuple(sorted(self.done)), tuple(sorted(self.cancelled)))

    def replay(self, log):
        self.done, self.cancelled, self.log = set(), set(), []
        for n, e in enumerate(log, 1):
            if not isinstance(e, dict) or e.get("episode_id") != self.episode_id or e.get("role") not in ROLES:
                raise Invalid(f"log line {n}: wrong episode or role")
            r = self.step(e.get("action"), _role=e["role"], _actor=e.get("actor"))
            if not r["accepted"]:
                raise Invalid(f"log line {n} rejected on replay: {r['reason']}")
        return self.state()


def read_log(path):
    try:
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as e:
        raise Invalid(f"cannot read log: {e}")


USAGE = """usage: orders.py compile LIST
       orders.py status LIST LOG
       orders.py tick|untick|cancel LIST LOG JOB_ID --actor A --role R --ts YYYY-MM-DDTHH:MM"""


def main(argv):
    try:
        cmd, a = (argv[1], argv[2:]) if len(argv) > 1 else ("", [])
        if cmd == "compile" and len(a) == 1:
            l = load_list(a[0])
            print(f"OK {l['list_id']}: {len(l['job'])} jobs, no cycles")
            return 0
        if cmd == "status" and len(a) == 2:
            env = OrdersEnv(); env.reset(a[0], "status", "agent", load_list(a[0])["list_id"])
            env.replay(read_log(a[1]))
        elif cmd in ("tick", "untick", "cancel") and len(a) == 9 and a[3::2] == ["--actor", "--role", "--ts"]:
            lst, logp, jid, actor, role, ts = a[0], a[1], a[2], a[4], a[6], a[8]
            env = OrdersEnv(); env.reset(lst, actor, role, load_list(lst)["list_id"])
            env.replay(read_log(logp))
            r = env.step({"type": cmd, "id": jid, "ts": ts})
            if not r["accepted"]:
                print(f"REJECTED: {r['reason']}", file=sys.stderr)
                return 2
            with open(logp, "a") as f:
                f.write(json.dumps(env.log[-1], ensure_ascii=False) + "\n")
        else:
            print(USAGE, file=sys.stderr)
            return 2
        c = env.check()
        for i in c["next"]:
            print(f"NEXT {i}: {env.jobs[i]['text']}")
        for f in c["flags"]:
            print(f"FLAG {f}")
        print(f"{'DONE' if c['done'] else 'OPEN'} {len(env.order) - len(c['open'])}/{len(env.order)}")
        return 0 if c["done"] else 1
    except Invalid as e:
        print(f"INVALID: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
