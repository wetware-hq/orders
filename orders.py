#!/usr/bin/env python3
"""Checklists of jobs with dependencies, as an agent environment.
List: TOML. Store: append-only JSONL log of tick/untick/cancel. State is only ever rebuilt by
replaying the log through the same gate. Done and cancelled are separate; cancelled is never done.
All user-facing text comes from messages.py."""
import datetime, graphlib, json, re, sys, tomllib
from messages import msg, code

ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
DUE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
JOB_KEYS = {"id", "text", "depends_on", "owner", "due", "safety_critical"}
ROLES = {"clinician", "nurse", "patient", "agent"}
ACTIONS = {"tick": {"id", "ts", "evidence"}, "untick": {"id", "ts"}, "cancel": {"id", "ts", "reason"}, "next": set()}


class Invalid(Exception):
    pass


def parse_due(s):
    if not isinstance(s, str) or not DUE_RE.match(s):
        raise ValueError
    return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M")


def check_list(d):
    if not isinstance(d, dict) or set(d) != {"list_id", "job"} or not isinstance(d["list_id"], str) or not d["list_id"].strip():
        raise Invalid(code("list_fields"))
    jobs = d["job"]
    if not isinstance(jobs, list) or not jobs:
        raise Invalid(code("no_jobs"))
    ids = []
    for j in jobs:
        if not isinstance(j, dict) or set(j) != JOB_KEYS:
            raise Invalid(code("job_fields"))
        if not isinstance(j["id"], str) or not ID_RE.match(j["id"]):
            raise Invalid(code("job_id"))
        if j["id"] in ids:
            raise Invalid(code("duplicate_job_id"))
        ids.append(j["id"])
        if not isinstance(j["text"], str) or not j["text"].strip() or not isinstance(j["owner"], str) or not j["owner"].strip():
            raise Invalid(code("job_text_or_owner"))
        try:
            parse_due(j["due"])
        except ValueError:
            raise Invalid(code("job_due"))
        if type(j["safety_critical"]) is not bool:
            raise Invalid(code("job_safety_critical"))
        dep = j["depends_on"]
        if not isinstance(dep, list) or not all(isinstance(x, str) for x in dep) or len(set(dep)) != len(dep):
            raise Invalid(code("job_depends_on"))
    text = {j["id"]: j["text"] for j in jobs}
    for j in jobs:
        if any(x not in ids for x in j["depends_on"]):
            raise Invalid(code("unknown_dependency"))
    try:
        tuple(graphlib.TopologicalSorter({j["id"]: j["depends_on"] for j in jobs}).static_order())
    except graphlib.CycleError as e:
        cyc = e.args[1]
        raise Invalid(msg("CK-LOOP", a=text[cyc[0]], b=text[cyc[1]]))
    return d


def load_list(path):
    try:
        with open(path, "rb") as f:
            return check_list(tomllib.load(f))
    except (OSError, tomllib.TOMLDecodeError):
        raise Invalid(code("list_unreadable"))


class OrdersEnv:
    def reset(self, case_file, actor, role, episode_id):
        if role not in ROLES:
            raise Invalid(code("role"))
        self.lst = check_list(case_file if isinstance(case_file, dict) else load_list(case_file))
        self.actor, self.role, self.episode_id = actor, role, episode_id
        self.jobs = {j["id"]: j for j in self.lst["job"]}
        self.order = list(self.jobs)
        self.dependents = {i: [k for k in self.order if i in self.jobs[k]["depends_on"]] for i in self.order}
        self.done, self.cancelled, self.log = set(), set(), []
        return self.observe()

    def _waiting(self, i):
        return [d for d in self.jobs[i]["depends_on"] if d not in self.done]

    def available(self):
        return [i for i in self._open() if not self._waiting(i)]

    def _open(self):
        return [i for i in self.order if i not in self.done and i not in self.cancelled]

    def check(self, now=None):
        now = parse_due(now) if now else None
        blocked = [msg("RF-WAITING", job_text=self.jobs[self._waiting(i)[0]]["text"])
                   for i in self._open() if self._waiting(i)]
        flags = [msg("FL-OVERDUE", job_text=self.jobs[i]["text"], owner=self.jobs[i]["owner"], time=self.jobs[i]["due"])
                 for i in self._open() if now and parse_due(self.jobs[i]["due"]) < now]
        return {"all_done": len(self.done) == len(self.order), "done": sorted(self.done),
                "cancelled": sorted(self.cancelled), "open": self._open(), "next": self.available(),
                "blocked": blocked, "flags": flags}

    def observe(self):
        c = self.check()
        return {"episode_id": self.episode_id, "list_id": self.lst["list_id"], "open": c["open"],
                "next": c["next"], "blocked": c["blocked"], "flags": c["flags"]}

    def _gate(self, a, role):
        t = a.get("type") if isinstance(a, dict) else None
        if t not in ACTIONS:
            return code("action_type")
        if t == "cancel" and not str(a.get("reason") or "").strip():
            return msg("RF-CANCEL-REASON")
        if t == "tick" and not str(a.get("evidence") or "").strip():
            return msg("RF-EVIDENCE")
        if set(a) != ACTIONS[t] | {"type"}:
            return code("fields")
        if t == "next":
            return None
        try:
            datetime.datetime.fromisoformat(a["ts"])
        except (TypeError, ValueError):
            return code("timestamp")
        i = a["id"]
        if i not in self.jobs:
            return code("job_id")
        if t == "tick":
            if i in self.done or i in self.cancelled:
                return code("already_closed")
            w = self._waiting(i)
            return msg("RF-WAITING", job_text=self.jobs[w[0]]["text"]) if w else None
        if t == "untick":
            if i not in self.done:
                return code("not_done")
            return code("dependent_done") if any(k in self.done for k in self.dependents[i]) else None
        if self.jobs[i]["safety_critical"] and role != "clinician":
            return msg("RF-ROLE-CANCEL")
        return code("already_closed") if i in self.done or i in self.cancelled else None

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

    def state(self):
        return (tuple(sorted(self.done)), tuple(sorted(self.cancelled)))

    def replay(self, log, now=None):
        self.done, self.cancelled, self.log = set(), set(), []
        for e in log:
            if not isinstance(e, dict) or e.get("episode_id") != self.episode_id or e.get("role") not in ROLES:
                raise Invalid(code("log_episode_or_role"))
            r = self.step(e.get("action"), _role=e["role"], _actor=e.get("actor"))
            if not r["accepted"]:
                raise Invalid(r["reason"])
        return self.check(now)


def read_log(path):
    try:
        with open(path) as f:
            return [json.loads(x) for x in f if x.strip()]
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError):
        raise Invalid(code("log_unreadable"))


def opts(rest):
    if len(rest) % 2 or not all(k.startswith("--") for k in rest[::2]):
        raise Invalid(code("usage"))
    return {k[2:]: v for k, v in zip(rest[::2], rest[1::2])}


def main(argv):
    """compile LIST | status LIST LOG [--now 'YYYY-MM-DD HH:MM'] |
    tick|untick|cancel LIST LOG JOB --actor A --role R --ts ISO [--evidence ID | --reason TEXT]"""
    try:
        cmd, a = (argv[1], argv[2:]) if len(argv) > 1 else ("", [])
        if cmd == "compile" and len(a) == 1:
            load_list(a[0])
            return 0
        if cmd == "status" and len(a) >= 2:
            o = opts(a[2:])
            env = OrdersEnv(); env.reset(a[0], "status", "agent", load_list(a[0])["list_id"])
        elif cmd in ("tick", "untick", "cancel") and len(a) >= 3:
            o = opts(a[3:])
            if not {"actor", "role", "ts"} <= set(o):
                raise Invalid(code("usage"))
            env = OrdersEnv(); env.reset(a[0], o["actor"], o["role"], load_list(a[0])["list_id"])
        else:
            print(code("usage"), file=sys.stderr)
            return 2
        try:
            c = env.replay(read_log(a[1]), o.get("now"))
        except ValueError:
            raise Invalid(code("now"))
        if cmd != "status":
            act = {"type": cmd, "id": a[2], "ts": o["ts"]}
            for k in ("evidence", "reason"):
                if k in o:
                    act[k] = o[k]
            if cmd == "tick":
                act.setdefault("evidence", "")
            if cmd == "cancel":
                act.setdefault("reason", "")
            r = env.step(act)
            if not r["accepted"]:
                raise Invalid(r["reason"])
            with open(a[1], "a") as f:
                f.write(json.dumps(env.log[-1], ensure_ascii=False) + "\n")
            c = env.check()
        for i in c["next"]:
            print(code("next:" + i))
        for x in c["blocked"] + c["flags"]:
            print(x)
        return 0 if c["all_done"] else 1
    except Invalid as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
