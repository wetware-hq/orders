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
| `patient` | tick, untick |
| `patient_agent` | tick agent-doable jobs only |
| `clinician` (with `--discipline`, e.g. medicine, nursing, physiotherapy, pharmacy) | tick, untick, cancel, approve |
| `clinician_agent` (with `--scope`) | tick agent-doable jobs only |

A tick needs `--evidence`: the id of a record in the registry. A cancel needs a `--reason`. Scope of practice applies per item: a safety-critical job can be ticked or cancelled only by its listed `disciplines`, or by the approving clinician's discipline when none are listed, and only a clinician can cancel it. Assistants never cancel or approve.

## Work done for the patient

orders has no field for substitute decision-makers. Every event records the actor, role and discipline of the session that made it. When a parent, guardian or carer does a job for the patient, a clinician ticks it in their own session, with a record in the registry as evidence. Never use the patient's session for someone else's work. (In [forms](https://github.com/wetware-hq/forms), answers given for the patient use the signed `on_behalf` field.)

## Interface

`OrdersEnv` in `orders.py` is the environment. A session (person or agent) reaches it only through `call(method, ...)`, and the per-call table `CALL_PERMS` allows only `observe` and `step`. Everything else belongs to the harness and is refused with "Refused: only the system running this session can start it again or replay it."

- `reset(case_file, actor, role, episode_id, discipline=None, scope=())` (harness) opens a session on one list and episode.
- `observe(item=None)` (session) shows only what the role may see: a patient's agent sees only agent-doable, non-safety jobs; a clinician's agent sees only lists approved inside its clinician's scope, and only jobs whose listed disciplines it holds. A refused read gets "Refused: you don't have access to this.", the same line whether or not the item exists.
- `step(action)` (session) passes the action through the gate (type, role, discipline) and appends it to the log, or refuses it with a fixed line.
- `step({"type": "submit", ...})` runs the check once and logs the submission with its result. A fail returns the missing items and the episode continues; only a pass is final. After 3 failed submissions in a row the episode goes to the clinician queue and agents are refused until a clinician sends `review`. A clinician can always finish in their own session.
- `check()` (harness) is a deterministic checker with no model.
- `replay(log)` (harness) rebuilds state from a log; the same log always gives the same state, and every logged submission result is recomputed.
- `export()` (harness) refuses any episode whose id does not start with `SYNTH-`.

The command line wraps the same calls. Done, closed and open are separate. Exit codes: 0 all done, 3 closed (every job done or cancelled with a reason, and safety jobs cancelled only by a clinician), 1 open, 2 invalid or refused.

## Quickstart

Python 3.11 or later, standard library only.

```bash
git clone https://github.com/wetware-hq/orders.git
cd orders
python3 orders.py compile examples/pharmacy_discharge_reconciliation.toml
python3 orders.py approve examples/pharmacy_discharge_reconciliation.toml log.jsonl SYNTH-EP-1 --role clinician --discipline pharmacy --actor SYNTH-ph-01 --ts 1937-08-10T08:00
python3 orders.py tick    examples/pharmacy_discharge_reconciliation.toml log.jsonl SYNTH-EP-1 get_home_list --role clinician_agent --scope pharmacy --actor SYNTH-agent-01 --ts 1937-08-10T08:30 --evidence SYNTH-RX-0001
python3 orders.py status  examples/pharmacy_discharge_reconciliation.toml log.jsonl SYNTH-EP-1 --now "1937-08-10 12:00"
```

The last command exits 1 (open) and flags one overdue job.

## Rules

Every rule is a row in `RULES.md` with its signer, class (safety or efficacy), the role it protects and the tests that plant its failure. Everything a person sees comes from `messages.py`, which must match the signed wording table `WORDING.md` word for word.

```bash
python3 scripts/rulecheck.py                  # exit 0: every rule has a test, all wording matches WORDING.md
python3 scripts/mutate.py orders.py _gate     # exit 0: every mutant of the gate is caught
```

## Tests

```bash
python3 -m pytest -q
```

Contributors run pytest and `scripts/rulecheck.py` before every commit. Agent rules are in [AGENTS.md](AGENTS.md).

## Status

An MCP server is planned. There isn't one yet. All 58 wording lines are signed; `WORDING.md` is the signed table.

## Licence

MIT. See [LICENSE](LICENSE).
