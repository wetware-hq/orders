# orders

orders - a job board for clinical checklists: jobs with owners, due times and dependencies, ticked off in order with evidence, kept as an append-only log that any agent can replay and check.

> Example data is synthetic (`SYNTH-` ids, 1930s dates, made-up people). orders is a research prototype. It is not a medical device and has not been approved for use with real patient data.

## Purpose

orders runs clinical checklists as an agentic environment. It works in any clinical setting: medicine, nursing, allied health, and lists that patients work through themselves. Each job has an owner, a due time and the jobs it depends on. A job can only be ticked when its dependencies are done, and only with evidence. orders does not give clinical advice and does not decide which jobs a list needs; it tracks the list a clinician approved and refuses anything the rules don't allow.

## Model

- **Items.** A list is versioned TOML: `list_id`, `version`, `source_lines`, a records registry `[[record]]` and ordered `[[job]]` entries with `id`, `text`, `depends_on`, `owner`, `due` (YYYY-MM-DD HH:MM), `safety_critical`, `agent_doable`, `source_line` and optional `disciplines`.
- **Compile checks.** `compile` refuses duplicate ids, unknown ids, dependency loops and jobs with no source line. It also lists source lines that produced no job.
- **Log.** An append-only JSONL event log. State is rebuilt by replaying the log, and every line is checked again on replay.
- **Approval.** Nothing can be ticked until a clinician approves that exact version.

## Roles

There are four roles, fixed when a session opens. Anyone else is refused. Permissions live in one table (`PERMS` in `orders.py`).

| Role | May |
|---|---|
| `patient` | tick, untick, grant and revoke access for a named assistant (`--agent`) |
| `patient_agent` | with a grant naming its own id: sees and ticks agent-doable, non-safety jobs only |
| `clinician` (with `--discipline`, e.g. medicine, nursing, physiotherapy, pharmacy) | tick, untick, cancel, approve |
| `clinician_agent` (with `--scope`) | tick agent-doable jobs only |

A tick needs `--evidence`: the id of a record in the registry. A cancel needs a `--reason`. Scope of practice applies per item: a safety-critical job can be ticked or cancelled only by its listed `disciplines`, or by the approving clinician's discipline when none are listed, and only a clinician can cancel it. Assistants never cancel or approve.

## Work done for the patient

orders has no field for substitute decision-makers. Every event records the actor, role and discipline of the session that made it. When a parent, guardian or carer does a job for the patient, a clinician ticks it in their own session, with a record in the registry as evidence. Never use the patient's session for someone else's work. (In [forms](https://github.com/wetware-hq/forms), answers given for the patient use the signed `on_behalf` field.)

## Interface

`OrdersEnv` in `orders.py` is the environment. A session (person or agent) reaches it only through `call(method, ...)`, and the per-call table `CALL_PERMS` allows only `observe` and `step`. Everything else belongs to the harness and is refused with "Refused: only the system running this session can start it again or replay it."

- `reset(case_file, actor, role, episode_id, discipline=None, scope=())` (harness) opens a session on one list and episode.
- `observe(item=None)` (session) shows only what the role may see: a patient's agent sees nothing without a grant naming it, and with one only agent-doable, non-safety jobs; a clinician's agent sees only lists approved inside its clinician's scope, and only jobs whose listed disciplines it holds. A refused read gets "Refused: you don't have access to this.", the same line whether or not the item exists.
- `step(action)` (session) passes the action through the gate (type, role, discipline) and appends it to the log, or refuses it with a fixed line.
- `step({"type": "submit", ...})` runs the check once and logs the submission with its result. A fail returns the missing items and the episode continues; only a pass is final. After 3 failed submissions in a row the episode goes to the clinician queue and agents are refused until a clinician sends `review`. A clinician can always finish in their own session.
- `step({"type": "result", "id": JOB, "value": TEXT, ...})` records a result on a job (agent-callable, same gate as a tick, appended to the log; results accumulate and export carries them). On the command line: `result LIST LOG EPISODE JOB --value TEXT`.
- `check()` (harness) is a deterministic checker with no model.
- `replay(log)` (harness) rebuilds state from a log; the same log always gives the same state, and every logged submission result is recomputed.
- `open_episode(synthetic, timestamp)` (harness) writes the `synthetic` flag as the first log event. It can never be set again or changed.
- `export()` (harness) reads that flag, never the episode id, and refuses any episode not marked synthetic. Export carries the `interpreter` field (set only by a clinician session on tick events).

The command line wraps the same calls. Done, closed and open are separate. Exit codes: 0 all done, 3 closed (every job done or cancelled with a reason, and safety jobs cancelled only by a clinician), 1 open, 2 invalid or refused.

## Quickstart

Python 3.11 or later, standard library only.

```bash
git clone https://github.com/wetware-hq/orders.git
cd orders
python3 orders.py compile examples/pharmacy_discharge_reconciliation.toml
python3 orders.py open    examples/pharmacy_discharge_reconciliation.toml log.jsonl SYNTH-EP-1 --synthetic true --ts 1937-08-10T07:59
python3 orders.py approve examples/pharmacy_discharge_reconciliation.toml log.jsonl SYNTH-EP-1 --role clinician --discipline pharmacy --actor SYNTH-ph-01 --ts 1937-08-10T08:00
python3 orders.py tick    examples/pharmacy_discharge_reconciliation.toml log.jsonl SYNTH-EP-1 get_home_list --role clinician_agent --scope pharmacy --actor SYNTH-agent-01 --ts 1937-08-10T08:30 --evidence SYNTH-RX-0001
python3 orders.py status  examples/pharmacy_discharge_reconciliation.toml log.jsonl SYNTH-EP-1 --now "1937-08-10 12:00"
python3 orders.py export  examples/pharmacy_discharge_reconciliation.toml log.jsonl SYNTH-EP-1
```

`status` exits 1 (open) and flags one overdue job.

## Rules

Every rule is a row in `RULES.md` with its signer, class (safety or efficacy), the role it protects and the tests that plant its failure.

Everything a person sees comes from `WORDING.md`: the signed line ids and wording (every fill-in value, such as a queue status or a relationship word, is its own row) and any lines still pending signature. It is generated by `scripts/sync_wording.py` from the signed table and carries the SHA-256 of its content in its header. `messages.py` reads only `WORDING.md` and holds no wording of its own.

```bash
python3 scripts/rulecheck.py                  # exit 0: every rule has a test, WORDING.md matches its hash, register rows match it
python3 scripts/mutate.py     # exit 0: every mutant of every function in safety_functions.toml is caught
```

The tests check `WORDING.md` against its header hash and check that every line id used in the code exists in it. Before pushing, maintainers run `python3 scripts/prepush.py SIGNED_TABLE.md`, which compares `WORDING.md` with the signed table and fails on internal names or local paths.

## Tests

```bash
python3 -m pytest -q
```

Contributors run pytest and `scripts/rulecheck.py` before every commit. Agent rules are in [AGENTS.md](AGENTS.md).

## Status

An MCP server is planned. There isn't one yet. All lines in `WORDING.md` are signed. Lines still waiting for signature are in `messages.PENDING` (draft: `WORDING_PENDING.md`); `CQ-SAMEDAY-ALERT` is a placeholder until it is drafted.

## Licence

MIT. See [LICENSE](LICENSE).

## Live-model status

S05 and S07 were checked with a scripted stand-in, not a real AI model. The scripted runs show the checks catch the mistakes those two scenarios are for. They don't show how a real model behaves. Those tests are still to come.

## Status

Not for use with real patients.
