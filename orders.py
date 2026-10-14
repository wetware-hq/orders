#!/usr/bin/env python3
"""Checklists of jobs with dependencies, as an agent environment, for any clinical setting.
List: TOML (jobs + records registry). Store: append-only JSONL log. State is only ever rebuilt by
replaying the log through the same gate. Done, cancelled and closed are separate.
All user-facing text comes from messages.py."""
import datetime, graphlib, hashlib, json, re, sys, tomllib
from messages import msg, code
from access import Invalid, AGENTS, open_session, in_scope

ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
DUE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
JOB_REQ = {"id", "text", "depends_on", "owner", "due", "safety_critical", "agent_doable", "source_line"}
JOB_OPT = {"disciplines"}
LIST_KEYS = {"list_id", "version", "source_lines", "job", "record"}

# ---- permission table: role -> actions. Refusal line when an action is not allowed. ----
PERMS = {
    "patient": {"tick", "untick", "next"},
    "patient_agent": {"tick", "next"},
    "clinician": {"tick", "untick", "cancel", "approve", "next"},
    "clinician_agent": {"tick", "next"},
}
DENY = {"patient": "RF-ROLE-CANCEL", "patient_agent": "RF-ROLE-CANCEL", "clinician": None,
        "clinician_agent": "RF-CLIN-AGENT"}
SCOPED_ACTIONS = {"tick", "cancel"}           # on safety_critical jobs: role + discipline checked together
FIELDS = {"tick": {"id", "evidence"}, "untick": {"id"}, "cancel": {"id", "reason"}, "approve": set()}
SESSION_KEYS = {"actor", "role", "discipline", "scope", "episode_id"}


def parse_due(s):
    if not isinstance(s, str) or not DUE_RE.match(s):
        raise ValueError
    return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M")


def bad(field, item):
    return Invalid(msg("ER-BAD-FIELD", field=field, item=item))


def check_list(d):
    if not isinstance(d, dict):
        raise bad("list", "file")
    extra = sorted((LIST_KEYS - set(d)) | (set(d) - LIST_KEYS))
    if extra:
        raise bad(extra[0], "list")
    if not isinstance(d["list_id"], str) or not d["list_id"].strip():
        raise bad("list_id", "list")
    if not isinstance(d["version"], str) or not d["version"].strip():
        raise bad("version", "list")
    if type(d["source_lines"]) is not int or d["source_lines"] < 0:
        raise bad("source_lines", "list")
    recs = d["record"]
    if not isinstance(recs, list) or not all(isinstance(r, dict) and set(r) == {"id", "kind"}
                                             and isinstance(r["id"], str) and r["id"] for r in recs):
        raise bad("record", "list")
    jobs = d["job"]
    if not isinstance(jobs, list) or not jobs:
        raise bad("job", "list")
    ids = []
    for n, j in enumerate(jobs, 1):
        item = f"job {n}"
        if not isinstance(j, dict):
            raise bad("job", item)
        if "source_line" not in j:
            raise Invalid(msg("CK-NO-SOURCE", n=n))
        miss = sorted((JOB_REQ - set(j)) | (set(j) - JOB_REQ - JOB_OPT))
        if miss:
            raise bad(miss[0], item)
        if not isinstance(j["id"], str) or not ID_RE.match(j["id"]):
            raise bad("id", item)
        if j["id"] in ids:
            raise Invalid(msg("ER-DUP-ID", id=j["id"]))
        ids.append(j["id"])
        for k in ("text", "owner"):
            if not isinstance(j[k], str) or not j[k].strip():
                raise bad(k, item)
        try:
            parse_due(j["due"])
        except ValueError:
            raise bad("due", item)
        for k in ("safety_critical", "agent_doable"):
            if type(j[k]) is not bool:
                raise bad(k, item)
        if type(j["source_line"]) is not int or not 1 <= j["source_line"] <= d["source_lines"]:
            raise Invalid(msg("CK-NO-SOURCE", n=n))
        if "disciplines" in j and (not j["safety_critical"] or not isinstance(j["disciplines"], list)
                                   or not j["disciplines"] or not all(isinstance(x, str) and x for x in j["disciplines"])):
            raise bad("disciplines", item)
        dep = j["depends_on"]
        if not isinstance(dep, list) or not all(isinstance(x, str) for x in dep) or len(set(dep)) != len(dep):
            raise bad("depends_on", item)
    text = {j["id"]: j["text"] for j in jobs}
    for n, j in enumerate(jobs, 1):
        if any(x not in ids for x in j["depends_on"]):
            raise bad("depends_on", f"job {n}")
    try:
        tuple(graphlib.TopologicalSorter({j["id"]: j["depends_on"] for j in jobs}).static_order())
    except graphlib.CycleError as e:
        cyc = e.args[1]
        raise Invalid(msg("CK-LOOP", a=text[cyc[0]], b=text[cyc[1]]))
    return d


def list_hash(d):
    return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def gap_view(d):
    """Approval view: source lines that produced no job."""
    used = {j["source_line"] for j in d["job"]}
    return [msg("FL-GAP", n=n) for n in range(1, d["source_lines"] + 1) if n not in used]


def propose_list(d):
    """A proposed list must pass the fixed checker; it takes effect only after a clinician approves it."""
    check_list(d)
    return {"list_hash": list_hash(d), "approved": False, "gaps": gap_view(d)}


def diff_view(old, new):
    o = {j["id"]: j for j in old["job"]}
    n = {j["id"]: j for j in new["job"]}
    return {"added": [i for i in n if i not in o], "removed": [i for i in o if i not in n],
            "changed": [i for i in n if i in o and n[i] != o[i]]}


def load_list(path):
    try:
        with open(path, "rb") as f:
            return check_list(tomllib.load(f))
    except (OSError, tomllib.TOMLDecodeError):
        raise Invalid(code("list_unreadable"))


class OrdersEnv:
    def reset(self, case_file, actor, role, episode_id, discipline=None, scope=()):
        self.session = open_session(actor, role, discipline, scope)
        self.lst = check_list(case_file if isinstance(case_file, dict) else load_list(case_file))
        self.hash = list_hash(self.lst)
        self.records = {r["id"] for r in self.lst["record"]}
        self.episode_id = episode_id
        self.jobs = {j["id"]: j for j in self.lst["job"]}
        self.order = list(self.jobs)
        self.dependents = {i: [k for k in self.order if i in self.jobs[k]["depends_on"]] for i in self.order}
        self.done, self.cancelled, self.approver, self.log = set(), {}, None, []
        return self.observe()

    def _waiting(self, i):
        return [d for d in self.jobs[i]["depends_on"] if d not in self.done]

    def _open(self):
        return [i for i in self.order if i not in self.done and i not in self.cancelled]

    def available(self):
        return [i for i in self._open() if not self._waiting(i)]

    def allowed_disciplines(self, i):
        return self.jobs[i].get("disciplines") or ([self.approver] if self.approver else [])

    def _closed_ok(self, i):
        """A cancel counts toward closed only with a reason, and for a safety job only if a clinician cancelled it."""
        c = self.cancelled.get(i)
        return bool(c and c["reason"].strip() and (not self.jobs[i]["safety_critical"] or c["role"] == "clinician"))

    def check(self, now=None):
        now = parse_due(now) if now else None
        all_done = len(self.done) == len(self.order)
        closed = not all_done and all(i in self.done or self._closed_ok(i) for i in self.order)
        blocked = [msg("RF-WAITING", job_text=self.jobs[self._waiting(i)[0]]["text"])
                   for i in self._open() if self._waiting(i)]
        if self.approver is None:
            blocked.insert(0, msg("RF-NOT-APPROVED"))
        flags = [msg("FL-OVERDUE", job_text=self.jobs[i]["text"], owner=self.jobs[i]["owner"], time=self.jobs[i]["due"])
                 for i in self._open() if now and parse_due(self.jobs[i]["due"]) < now]
        status = msg("ST-DONE") if all_done else msg("ST-CLOSED") if closed else None
        return {"all_done": all_done, "closed": closed, "status": status, "done": sorted(self.done),
                "cancelled": sorted(self.cancelled), "open": self._open(), "next": self.available(),
                "blocked": blocked, "flags": flags}

    def exit_code(self, c):
        return 0 if c["all_done"] else 3 if c["closed"] else 1

    def next_view(self):
        """Patient-facing next steps: table lines only."""
        n = self.available()
        return [msg("PV-NEXT", plain=self.jobs[i]["text"]) for i in n] or [msg("PV-NOTHING-NEXT")]

    def observe(self):
        c = self.check()
        return {"episode_id": self.episode_id, "list_id": self.lst["list_id"], "open": c["open"],
                "next": c["next"], "blocked": c["blocked"], "flags": c["flags"]}

    def _gate(self, e, s):
        t = e["type"]
        if t not in PERMS[s.role]:
            return msg(DENY[s.role]) if DENY[s.role] else ""
        try:
            datetime.datetime.fromisoformat(e["ts"])
        except (TypeError, ValueError):
            return code("timestamp")
        if t == "approve":
            return code("already_approved") if self.approver is not None else None
        if self.approver is None:
            return msg("RF-NOT-APPROVED")
        if t == "cancel" and not (isinstance(e["reason"], str) and e["reason"].strip()):
            return msg("RF-CANCEL-REASON")
        if t == "tick" and not (isinstance(e["evidence"], str) and e["evidence"].strip()):
            return msg("RF-EVIDENCE")
        if t == "tick" and e["evidence"] not in self.records:
            return msg("RF-EVIDENCE-NOT-FOUND")
        i = e["id"]
        if i not in self.jobs:
            return code("job_id")
        j = self.jobs[i]
        if t == "tick" and s.role in AGENTS and not j["agent_doable"]:
            return msg("RF-PERSON-JOB")
        if t in SCOPED_ACTIONS and j["safety_critical"] and not in_scope(s, self.allowed_disciplines(i)):
            return msg("RF-SCOPE", disciplines=", ".join(self.allowed_disciplines(i)))
        if t == "tick":
            if i in self.done or i in self.cancelled:
                return code("already_closed")
            w = self._waiting(i)
            return msg("RF-WAITING", job_text=self.jobs[w[0]]["text"]) if w else None
        if t == "untick":
            if i not in self.done:
                return code("not_done")
            return code("dependent_done") if any(k in self.done for k in self.dependents[i]) else None
        return code("already_closed") if i in self.done or i in self.cancelled else None

    def _apply(self, e):
        t = e["type"]
        if t == "approve":
            self.approver = e["discipline"]
        elif t == "tick":
            self.done.add(e["id"])
        elif t == "untick":
            self.done.discard(e["id"])
        else:
            self.cancelled[e["id"]] = {"reason": e["reason"], "role": e["role"]}

    def _event(self, a, s):
        e = dict(a, actor=s.actor, role=s.role, discipline=s.discipline, scope=list(s.scope),
                 episode_id=self.episode_id)
        if a["type"] == "approve":
            e["list_hash"] = self.hash
        return e

    def step(self, action):
        s = self.session
        if not isinstance(action, dict) or action.get("type") not in set(FIELDS) | {"next"}:
            return self._refuse(code("action_type"))
        if SESSION_KEYS & set(action):
            return self._refuse(code("role_fixed_at_session_open"))
        t = action["type"]
        if t == "next":
            return {"accepted": True, "next": self.available()}
        if t == "tick" and "evidence" not in action:
            return self._refuse(msg("RF-EVIDENCE"))
        if t == "cancel" and "reason" not in action:
            return self._refuse(msg("RF-CANCEL-REASON"))
        if set(action) != FIELDS[t] | {"type", "ts"}:
            return self._refuse(code("fields"))
        e = self._event(action, s)
        why = self._gate(e, s)
        if why is not None:
            return self._refuse(why)
        self._apply(e)
        self.log.append(e)
        return {"accepted": True}

    def _refuse(self, why):
        if self.session.role == "patient" and why.startswith("ERR:"):
            why = ""
        return {"accepted": False, "reason": why}

    def state(self):
        return (tuple(sorted(self.done)), tuple(sorted(self.cancelled)), self.approver)

    def replay(self, log, now=None):
        self.done, self.cancelled, self.approver, self.log = set(), {}, None, []
        for e in log:
            if not isinstance(e, dict) or e.get("type") not in FIELDS or e.get("episode_id") != self.episode_id:
                raise Invalid(code("log_event"))
            if e["type"] == "approve" and e.get("list_hash") != self.hash:
                self.log.append(dict(e))          # approval of another version covers nothing here
                continue
            s = open_session(e.get("actor"), e.get("role"), e.get("discipline"), e.get("scope"))
            if self._event({k: e[k] for k in FIELDS[e["type"]] | {"type", "ts"} if k in e}, s) != e:
                raise Invalid(code("log_event"))
            why = self._gate(e, s)
            if why is not None:
                raise Invalid(why or code("log_event_refused"))
            self._apply(e)
            self.log.append(dict(e))
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
    flags = [i for i, x in enumerate(rest) if x.startswith("--")]
    if any(i + 1 >= len(rest) for i in flags):
        raise Invalid(msg("ER-BAD-COMMAND"))
    o = {rest[i][2:]: rest[i + 1] for i in flags}
    pos = [x for j, x in enumerate(rest) if not x.startswith("--") and j - 1 not in flags]
    return pos, o


def main(argv):
    """compile LIST | status LIST LOG EPISODE [--now 'YYYY-MM-DD HH:MM'] |
    approve LIST LOG EPISODE ... | tick|untick|cancel LIST LOG EPISODE JOB --role R --actor A --ts ISO
    [--discipline D] [--scope d1,d2] [--evidence RECORD_ID | --reason TEXT]
    Exit: 0 all done, 3 closed, 1 open, 2 invalid."""
    try:
        cmd = argv[1] if len(argv) > 1 else ""
        pos, o = opts(argv[2:])
        if cmd == "compile" and len(pos) == 1:
            for g in gap_view(load_list(pos[0])):
                print(g)
            return 0
        if cmd == "status" and len(pos) == 3:
            env = OrdersEnv(); env.reset(pos[0], "status", "patient", pos[2])
        elif cmd in FIELDS and len(pos) == (3 if cmd == "approve" else 4) and {"role", "actor", "ts"} <= set(o):
            env = OrdersEnv()
            env.reset(pos[0], o["actor"], o["role"], pos[2], o.get("discipline"),
                      tuple(x for x in o.get("scope", "").split(",") if x))
        else:
            print(msg("ER-BAD-COMMAND"), file=sys.stderr)
            return 2
        try:
            c = env.replay(read_log(pos[1]), o.get("now"))
        except ValueError:
            raise Invalid(msg("ER-BAD-COMMAND"))
        if cmd != "status":
            act = {"type": cmd, "ts": o["ts"]}
            if cmd != "approve":
                act["id"] = pos[3]
            for k in ("evidence", "reason"):
                if k in o:
                    act[k] = o[k]
            r = env.step(act)
            if not r["accepted"]:
                raise Invalid(r["reason"])
            with open(pos[1], "a") as f:
                f.write(json.dumps(env.log[-1], ensure_ascii=False) + "\n")
            c = env.check()
        for x in env.next_view() + c["blocked"] + c["flags"] + ([c["status"]] if c["status"] else []):
            print(x)
        return env.exit_code(c)
    except Invalid as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
