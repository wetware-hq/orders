# orders

Checklists of jobs that get ticked off in the right order. It works in any clinical setting:
medicine, nursing, allied health and patients.

- **Who can use it.** The same four roles as forms, fixed when a session opens: patient, patient_agent,
  clinician (with a discipline) and clinician_agent. Anyone else is refused. Permissions live in one
  table (`PERMS` in `orders.py`).
- **List** (TOML): `list_id`, `version`, `source_lines`, a records registry `[[record]]` and ordered
  `[[job]]` entries. Each job has `id`, `text`, `depends_on`, `owner`, `due` (YYYY-MM-DD HH:MM),
  `safety_critical`, `agent_doable` and `source_line`. It can also list `disciplines`.
  `compile` refuses duplicate ids, unknown ids, loops and jobs with no source line. It also lists
  source lines that produced no job.
- **Approval.** Nothing can be ticked until a clinician approves that exact version.
- **Log.** A JSONL file that is only ever added to. State comes from replaying it, with every line checked again.
- **Ticks and cancels.** A tick needs evidence: the id of a record in the registry. Assistants may tick
  only agent-doable jobs, and never cancel or approve. A cancel needs a reason. Safety-critical jobs can
  be ticked or cancelled only by the listed disciplines, or the approving clinician's discipline if none
  are listed. Only a clinician can cancel them.
- **Done, closed and open are separate.** Exit codes: 0 all done, 3 closed (every job done or cancelled
  with a reason, and safety jobs cancelled only by a clinician), 1 open, 2 invalid.
  `status --now` flags overdue jobs.
- **Text.** All text shown to people comes from `messages.py`. `scripts/rulecheck.py` and
  `scripts/mutate.py` check the wording, the register and the tests.

```
python3 orders.py compile examples/pharmacy_discharge_reconciliation.toml
python3 orders.py approve examples/pharmacy_discharge_reconciliation.toml log.jsonl SYNTH-EP-1 --role clinician --discipline pharmacy --actor SYNTH-ph-01 --ts 1937-08-10T08:00
python3 orders.py tick    examples/pharmacy_discharge_reconciliation.toml log.jsonl SYNTH-EP-1 get_home_list --role clinician_agent --scope pharmacy --actor SYNTH-agent-01 --ts 1937-08-10T08:30 --evidence SYNTH-RX-0001
python3 orders.py status  examples/pharmacy_discharge_reconciliation.toml log.jsonl SYNTH-EP-1 --now "1937-08-10 12:00"
```

## Known limit

Version 1 has no role for substitute decision-makers, such as parents, guardians or carers acting for the patient. Anything they enter through the patient's session would be recorded as "You answered", which would be wrong. Until then, a clinician records the answer in their own session and names who gave it. Never use the patient's session.

`OrdersEnv` offers `reset`, `observe`, `step`, `check` and `replay` for agents. An MCP server is
planned once the core passes. There isn't one yet. Example data is made up. Standard library only. MIT licence.
