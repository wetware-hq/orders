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

`OrdersEnv` in `orders.py` is the agent environment:

- `reset(case_file, actor, role, episode_id, discipline=None, scope=())` opens a session on one list and episode.
- `observe()` returns the current state, including the next job for this session.
- `step(action)` passes the action through the gate (type, role, discipline) and appends it to the log, or refuses it with a fixed message.
- `check(now=None)` is a deterministic checker with no model. With `now`, overdue jobs are flagged.
- `replay(log, now=None)` rebuilds state from a log; the same log always gives the same state.

Done, closed and open are separate. Exit codes: 0 all done, 3 closed (every job done or cancelled with a reason, and safety jobs cancelled only by a clinician), 1 open, 2 invalid or refused.

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

Every rule is a row in `RULES.md` with its signer, class (safety or efficacy), the role it protects and the tests that plant its failure. Everything a person sees comes from the fixed wording table in `messages.py`.

```bash
python3 scripts/rulecheck.py                  # exit 0: every rule has a test, all wording is from messages.py
python3 scripts/mutate.py orders.py _gate     # exit 0: every mutant of the gate is caught
```

## Tests

```bash
python3 -m pytest -q
```

Contributors run pytest and `scripts/rulecheck.py` before every commit. Agent rules are in [AGENTS.md](AGENTS.md).

## Status

An MCP server is planned. There isn't one yet. Some wording lines in `RULES.md` are still marked unsigned (draft).

## Licence

MIT. See [LICENSE](LICENSE).
