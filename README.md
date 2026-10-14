# orders

Checklists of jobs that get ticked off in the right order.

- **List** (TOML): `list_id` and an ordered list of `[[job]]`, each with `id`, `text`, `depends_on`,
  `owner`, `due` (YYYY-MM-DD HH:MM) and `safety_critical`. `compile` refuses duplicate ids, unknown
  ids and loops.
- **Log**: a JSONL file that is only ever added to. Each line is a tick (needs evidence, a record id),
  an untick, or a cancel (needs a reason), with who did it, their role and the time.
- Nothing else is stored. State comes from replaying the log, with every line checked again.
- Done and cancelled are separate. A cancelled job never counts as done, so jobs that wait on it stay blocked.
- A job can't be ticked before the jobs it waits on are done. A job can't be unticked while a job that
  depends on it is done. Only a clinician can cancel a safety-critical job.
- `status --now "1936-05-02 12:30"` flags overdue jobs.
- All text shown to people comes from `messages.py`. `scripts/rulecheck.py` checks that and checks
  that every rule in `RULES.md` has a test.

```
python3 orders.py compile examples/ward_discharge.toml
python3 orders.py tick   examples/ward_discharge.toml log.jsonl review_meds --actor SYNTH-dr-01 --role clinician --ts 1936-05-02T09:00 --evidence SYNTH-REC-0001
python3 orders.py cancel examples/ward_discharge.toml log.jsonl book_followup --actor SYNTH-dr-01 --role clinician --ts 1936-05-02T09:05 --reason "booked elsewhere"
python3 orders.py status examples/ward_discharge.toml log.jsonl --now "1936-05-02 12:30"   # 0 all done, 1 not yet, 2 invalid
```

`OrdersEnv` offers `reset`, `observe`, `step`, `check` and `replay` for agents. An MCP server is
planned once the core passes. There isn't one yet. Example data is made up. Standard library only.
