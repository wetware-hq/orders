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
    "patient": {"tick", "untick", "result", "grant", "revoke", "submit", "next"},
    "patient_agent": {"tick", "result", "submit", "next"},
    "clinician": {"tick", "untick", "cancel", "result", "approve", "submit", "review", "next"},
    "clinician_agent": {"tick", "result", "submit", "next"},
}
# ---- per-call permissions: env methods a session may call through call(). reset, replay, check and export
# belong to the harness and are refused with RF-HARNESS-ONLY. ----
CALL_PERMS = {
    "patient": {"observe", "step"},
    "patient_agent": {"observe", "step"},
    "clinician": {"observe", "step"},
    "clinician_agent": {"observe", "step"},
}
# Patient screens never show a machine code: refusal code -> signed patient line id (ids only; wording is in WORDING.md).
PATIENT_LINE = {"role_fixed_at_session_open": "PV-CARE-TEAM-STEP", "action_type": "PV-TRY-AGAIN", "fields": "PV-TRY-AGAIN", "timestamp": "PV-TRY-AGAIN", "interpreter_clinician_only": "PV-CARE-TEAM-STEP", "final": "PV-ALREADY-FINAL", "already_closed": "PV-ALREADY-FINAL", "dependent_done": "PV-STEP-AFTER-DONE", "nothing_to_revoke": "PV-NOTHING-TO-REVOKE", "grant_needs_display_name": "PV-GRANT-NAMES-AGENT",
                "grant_names_agent": "PV-GRANT-NAMES-AGENT", "job_id": "PV-JOB-NOT-FOUND", "not_done": "PV-NOT-DONE",
                "grant_names_this_patient": "PV-ONLY-YOU", "result_value": "PV-RESULT-EMPTY"}
MAX_FAILS = 3   # failed submissions in a row before the episode goes to the clinician queue
DENY = {"patient": "RF-ROLE-CANCEL", "patient_agent": "RF-ROLE-CANCEL", "clinician": None,
        "clinician_agent": "RF-CLIN-AGENT"}
SCOPED_ACTIONS = {"tick", "cancel", "result"}
RESULT_MAX = 500   # characters in a recorded result           # on safety_critical jobs: role + discipline checked together
FIELDS = {"tick": {"id", "evidence"}, "untick": {"id"}, "cancel": {"id", "reason"}, "approve": set(),
          "submit": set(), "review": set(), "grant": {"agent", "agent_name", "patient"}, "revoke": {"agent", "patient"}, "result": {"id", "value"}}
OPTIONAL = {"tick": {"interpreter"}}
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
                raise Invalid(msg("RF-NOT-BOOL"))
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
        self._clear_submissions()
        return self.observe()

    def _clear_submissions(self):
        self.submissions, self.fails, self.passed, self.missing = 0, 0, False, []
        self.grants, self.patient, self.access, self.interpreter, self.results = {}, None, None, {}, {}
        self.names = {}

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
        blocked = [msg("WT-WAITING", job_text=self.jobs[self._waiting(i)[0]]["text"])
                   for i in self._open() if self._waiting(i)]
        if self.approver is None:
            blocked.insert(0, msg("RF-NOT-APPROVED"))
        flags = [msg("FL-OVERDUE", job_text=self.jobs[i]["text"], owner=self.jobs[i]["owner"], time=self.jobs[i]["due"])
                 for i in self._open() if now and parse_due(self.jobs[i]["due"]) < now]
        status = msg("ST-DONE") if all_done else msg("ST-CLOSED") if closed else None
        queue = [msg("CQ-THREE-FAILS", items="; ".join(f"{self.order.index(i) + 1}. {self.jobs[i]['text']}" for i in self.missing))] \
            if self.fails >= MAX_FAILS else []
        checked = msg("ST-CHECK-PASS") if self.passed else msg("ST-CHECK-FAIL") if self.submissions else None
        return {"all_done": all_done, "closed": closed, "status": status, "checked": checked, "done": sorted(self.done),
                "cancelled": sorted(self.cancelled), "open": self._open(), "next": self.available(),
                "blocked": blocked, "flags": flags, "submissions": self.submissions, "final": self.passed,
                "clinician_queue": queue}

    def exit_code(self, c):
        return 0 if c["all_done"] else 3 if c["closed"] else 1

    def next_view(self):
        """Patient-facing next steps: table lines only."""
        n = self.available()
        access = [] if not self.access else [msg("PV-GRANT-LIST" if self.access[0] == "grant" else "PV-REVOKE-LIST",
                                                 agent_name=self.access[2], date=self.access[1])]
        return access + ([msg("PV-NEXT", plain=self.jobs[i]["text"]) for i in n] or [msg("PV-NOTHING-NEXT")])

    def no_grant_line(self, agent):
        """RF-NO-GRANT-LIST names the agent only by the display name stored at grant time, never by its id."""
        name = self.names.get(agent)
        return msg("RF-NO-GRANT-LIST", agent_name=name) if name else msg("RF-NOT-YOURS")

    def visible(self, i=None):
        """Read scope. A patient's agent sees only agent-doable, non-safety jobs. A clinician's agent sees only
        lists its clinician's discipline approved, and only jobs whose listed disciplines it holds."""
        s = self.session
        if s.role == "clinician_agent":
            if not in_scope(s, [self.approver] if self.approver else []):
                return False
            return i is None or "disciplines" not in self.jobs[i] or in_scope(s, self.jobs[i]["disciplines"])
        if s.role == "patient_agent":
            if not (self.grants.get(s.actor) or {}).get("on"):
                return False
            return i is None or (self.jobs[i]["agent_doable"] and not self.jobs[i]["safety_critical"])
        return True

    def observe(self, item=None):
        """Refused reads get RF-NOT-YOURS, the same line whether or not the item exists."""
        if not self.visible() or (item is not None and (item not in self.jobs or not self.visible(item))):
            return {"accepted": False, "reason": msg("RF-NOT-YOURS")}
        c = self.check()
        if item is not None:
            return {"accepted": True, "id": item, "text": self.jobs[item]["text"],
                    "done": item in self.done, "cancelled": item in self.cancelled}
        mine = [i for i in self.order if self.visible(i)]
        return {"episode_id": self.episode_id, "list_id": self.lst["list_id"],
                "open": [i for i in c["open"] if i in mine], "next": [i for i in c["next"] if i in mine],
                "blocked": c["blocked"], "flags": c["flags"]}

    def call(self, method, *args, **kw):
        """The only entry point for a session (person or agent): observe and step. Everything else is harness-only."""
        if method not in CALL_PERMS[self.session.role]:
            return {"accepted": False, "reason": msg("RF-HARNESS-ONLY")}
        return getattr(self, method)(*args, **kw)

    def open_episode(self, synthetic, timestamp):
        """Harness-only. Writes the synthetic flag as the first log event; it can never be set again or changed."""
        if self.log or type(synthetic) is not bool:
            raise Invalid(code("open_first_only"))
        self.log.append({"type": "open", "synthetic": synthetic, "timestamp": timestamp, "episode_id": self.episode_id})

    def synthetic(self):
        first = self.log[0] if self.log else {}
        return first.get("type") == "open" and first.get("synthetic") is True

    def export(self):
        """Harness-only. Reads the synthetic flag from the first log event, never the episode id."""
        if not self.synthetic():
            raise Invalid(msg("RF-EXPORT-REAL"))
        return dict(self.check(), log=list(self.log), synthetic=True, interpreter=dict(self.interpreter),
                    results={i: list(v) for i, v in self.results.items()})

    def _gate(self, e, s):
        t = e["type"]
        if t not in PERMS[s.role]:
            return msg(DENY[s.role]) if DENY[s.role] else ""
        try:
            datetime.datetime.fromisoformat(e["timestamp"])
        except (TypeError, ValueError):
            return code("timestamp")
        if t == "approve":
            return code("already_approved") if self.approver is not None else None
        if self.approver is None:
            return msg("RF-NOT-APPROVED")
        if self.passed:
            return code("final")
        if s.role in AGENTS and self.fails >= MAX_FAILS:
            return msg("RF-WAIT-CLINICIAN")
        if t == "review":
            return None if self.fails >= MAX_FAILS else code("nothing_to_review")
        g = self.grants.get(s.actor)
        if s.role == "patient_agent" and g is None:
            return self.no_grant_line(s.actor)
        if s.role == "patient_agent" and not g["on"]:
            return msg("RF-REVOKED", date=g["revoked_on"])
        if t in ("grant", "revoke"):
            if not (isinstance(e["agent"], str) and e["agent"].strip()):
                return code("grant_names_agent")
            if t == "grant" and not (isinstance(e["agent_name"], str) and e["agent_name"].strip()
                                     and e["agent_name"] != e["agent"]):
                return code("grant_needs_display_name")
            if e["patient"] != s.actor or self.patient not in (None, e["patient"]):
                return code("grant_names_this_patient")
            if t == "revoke" and not (self.grants.get(e["agent"]) or {}).get("on"):
                return code("nothing_to_revoke")
            return None
        if "interpreter" in e and s.role != "clinician":
            return code("interpreter_clinician_only")
        if "interpreter" in e and type(e["interpreter"]) is not bool:
            return msg("RF-NOT-BOOL")
        if t == "submit":
            return None
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
        if t in ("tick", "result") and s.role in AGENTS and not j["agent_doable"]:
            return msg("RF-PERSON-JOB")
        if t == "result" and s.role == "patient_agent" and not self.visible(i):
            return msg("RF-NOT-YOURS")
        if t in SCOPED_ACTIONS and j["safety_critical"] and not in_scope(s, self.allowed_disciplines(i)):
            return msg("RF-SCOPE", disciplines=", ".join(self.allowed_disciplines(i)))
        if t == "result":
            if not (isinstance(e["value"], str) and e["value"].strip() and len(e["value"]) <= RESULT_MAX):
                return code("result_value")
            return code("already_closed") if i in self.cancelled else None
        if t == "tick":
            if i in self.done or i in self.cancelled:
                return code("already_closed")
            w = self._waiting(i)
            return msg("WT-WAITING", job_text=self.jobs[w[0]]["text"]) if w else None
        if t == "untick":
            if i not in self.done:
                return code("not_done")
            return code("dependent_done") if any(k in self.done for k in self.dependents[i]) else None
        return code("already_closed") if i in self.done or i in self.cancelled else None

    def _apply(self, e):
        t = e["type"]
        if t == "approve":
            self.approver = e["discipline"]
        elif t == "submit":
            self.submissions += 1
            self.passed = e["result"] == "pass"
            self.fails = 0 if self.passed else self.fails + 1
            self.missing = e["missing"]
        elif t == "review":
            self.fails = 0
        elif t in ("grant", "revoke"):
            day = e["timestamp"][:10]
            self.grants[e["agent"]] = {"on": t == "grant", "revoked_on": None if t == "grant" else day}
            if t == "grant":
                self.names[e["agent"]] = e["agent_name"]
            self.patient, self.access = e["patient"], (t, day, self.names[e["agent"]])
        elif t == "tick":
            self.done.add(e["id"])
            self.interpreter[e["id"]] = e.get("interpreter", False)
        elif t == "untick":
            self.done.discard(e["id"])
            self.interpreter.pop(e["id"], None)
        elif t == "result":
            self.results.setdefault(e["id"], []).append(
                {"value": e["value"], "role": e["role"], "actor": e["actor"], "timestamp": e["timestamp"]})
        else:
            self.cancelled[e["id"]] = {"reason": e["reason"], "role": e["role"]}

    def _event(self, a, s):
        e = dict(a, actor=s.actor, role=s.role, discipline=s.discipline, scope=list(s.scope),
                 episode_id=self.episode_id)
        if a["type"] == "approve":
            e["list_hash"] = self.hash
        if a["type"] == "submit":
            c = self.check()
            e.update(result="pass" if c["status"] else "fail", missing=c["open"])
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
        base = FIELDS[t] | {"type", "timestamp"}
        if not base <= set(action) <= base | OPTIONAL.get(t, set()):
            return self._refuse(code("fields"))
        e = self._event(action, s)
        why = self._gate(e, s)
        if why is not None:
            return self._refuse(why)
        self._apply(e)
        self.log.append(e)
        if t == "submit":
            if e["result"] == "pass":
                return {"accepted": True, "result": msg("ST-CHECK-PASS"), "final": True}
            return {"accepted": True, "result": msg("ST-CHECK-FAIL"), "final": False, "missing": self.check()["blocked"]}
        return {"accepted": True}

    def _refuse(self, why):
        if self.session.role == "patient" and why.startswith("ERR:") and why[4:] in PATIENT_LINE:
            why = msg(PATIENT_LINE[why[4:]])      # patients get a signed line, never a code or a blank
        return {"accepted": False, "reason": why}

    def state(self):
        return (tuple(sorted(self.done)), tuple(sorted(self.cancelled)), self.approver)

    def replay(self, log, now=None):
        self.done, self.cancelled, self.approver, self.log = set(), {}, None, []
        self._clear_submissions()
        for n, e in enumerate(log):
            if isinstance(e, dict) and e.get("type") == "open":
                if n != 0 or set(e) != {"type", "synthetic", "timestamp", "episode_id"} \
                        or e["episode_id"] != self.episode_id:
                    raise Invalid(code("open_first_only"))
                self.open_episode(e["synthetic"], e["timestamp"])
                continue
            if not isinstance(e, dict) or e.get("type") not in FIELDS or e.get("episode_id") != self.episode_id:
                raise Invalid(code("log_event"))
            if e["type"] == "approve" and e.get("list_hash") != self.hash:
                self.log.append(dict(e))          # approval of another version covers nothing here
                continue
            s = open_session(e.get("actor"), e.get("role"), e.get("discipline"), e.get("scope"))
            if self._event({k: e[k] for k in FIELDS[e["type"]] | OPTIONAL.get(e["type"], set()) | {"type", "timestamp"} if k in e}, s) != e:
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
    """compile LIST | status|export LIST LOG EPISODE [--now 'YYYY-MM-DD HH:MM'] |
    open LIST LOG EPISODE --synthetic true|false --ts ISO (harness: first event only) |
    approve|submit|review|grant|revoke LIST LOG EPISODE ... [--agent AGENT_ID (grant/revoke)] [--interpreter true|false (tick)] | result LIST LOG EPISODE JOB --value TEXT ... | tick|untick|cancel LIST LOG EPISODE JOB --role R --actor A --ts ISO
    [--discipline D] [--scope d1,d2] [--evidence RECORD_ID | --reason TEXT]
    Exit: 0 all done, 3 closed, 1 open, 2 invalid."""
    try:
        cmd = argv[1] if len(argv) > 1 else ""
        pos, o = opts(argv[2:])
        if cmd == "compile" and len(pos) == 1:
            for g in gap_view(load_list(pos[0])):
                print(g)
            return 0
        if cmd == "open" and len(pos) == 3 and {"synthetic", "ts"} <= set(o):
            env = OrdersEnv(); env.reset(pos[0], "harness", "patient", pos[2]); env.replay(read_log(pos[1]))
            if o["synthetic"] not in ("true", "false"):
                raise Invalid(msg("RF-NOT-BOOL"))
            env.open_episode(o["synthetic"] == "true", o["ts"])
            with open(pos[1], "a") as f:
                f.write(json.dumps(env.log[0]) + "\n")
            return 0
        if cmd == "export" and len(pos) == 3:
            env = OrdersEnv(); env.reset(pos[0], "export", "patient", pos[2]); env.replay(read_log(pos[1]))
            print(json.dumps(env.export(), indent=1))
            return 0
        if cmd == "status" and len(pos) == 3:
            env = OrdersEnv(); env.reset(pos[0], "status", "patient", pos[2])
        elif cmd in FIELDS and len(pos) == (3 if cmd in ("grant", "revoke") or not FIELDS[cmd] else 4) and {"role", "actor", "ts"} <= set(o):
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
            act = {"type": cmd, "timestamp": o["ts"]}
            if cmd in ("grant", "revoke"):
                act.update(agent=o.get("agent", ""), patient=o["actor"])
                if cmd == "grant":
                    act["agent_name"] = o.get("agent-name", "")
            elif FIELDS[cmd]:
                act["id"] = pos[3]
            if cmd == "result":
                act["value"] = o.get("value", "")
            if "interpreter" in o:
                if o["interpreter"] not in ("true", "false"):
                    raise Invalid(msg("RF-NOT-BOOL"))
                act["interpreter"] = o["interpreter"] == "true"
            for k in ("evidence", "reason"):
                if k in o:
                    act[k] = o[k]
            r = env.step(act)
            if not r["accepted"]:
                raise Invalid(r["reason"])
            with open(pos[1], "a") as f:
                f.write(json.dumps(env.log[-1]) + "\n")
            c = env.check()
        for x in env.next_view() + c["blocked"] + c["flags"] + ([c["status"]] if c["status"] else []):
            print(x)
        return env.exit_code(c)
    except Invalid as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
