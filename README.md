# orders

Checklists of jobs that get ticked off in the right order.

- A **list** is a TOML file: `list_id` and an ordered list of `[[job]]`, each with a unique `id`, `text`,
  `depends_on` (ids of jobs that must be done first) and optional `safety_critical = true`.
  `compile` refuses duplicate ids, unknown ids and loops (A needs B needs A).
- The **log** is a JSONL file you only ever add to: one line per tick, untick or cancel, with the job,
  who did it, their role and the time.
- Nothing else is stored. The current state is worked out by replaying the log from the top, with every
  line checked again.
- You can't tick a job until everything it depends on is done. You can't untick a job while any job
  that depends on it is done. Only a clinician can cancel a safety-critical job.

```
python3 orders.py compile examples/ward_discharge.toml
python3 orders.py tick   examples/ward_discharge.toml log.jsonl review_meds --actor SYNTH-dr-01 --role clinician --ts 1936-05-02T09:00
python3 orders.py status examples/ward_discharge.toml log.jsonl   # 0 all done, 1 not yet, 2 invalid
```

## Agent environment

`OrdersEnv` wraps one episode: `reset(case_file, actor, role, episode_id)`, `observe()`, `step(action)`,
`check()`, `replay(log)`. Actions are dicts (`tick`, `untick`, `cancel` with `id` and `ts`; `next` is
read-only). Each one passes a gate on fields, types and role first. Rejected actions give a reason
and are not logged. A cancelled job counts as closed, but jobs that depend on it get flagged.

An MCP server is planned once the core passes. There isn't one yet.

Example data is made up. Python standard library only. Tests: `python3 -m pytest`.
