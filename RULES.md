# Rule register

Signer: 'Mathew' = signed instruction. 'TEXT PENDING FROM ROOM' = wording not yet written, no test on purpose.
'unsigned' = wording line from the draft, waiting for Physician. class is left blank for Physician.
Wording rows marked (blocked) depend on a pending rule and have no test yet.

| id | rule | signer | class | protects_role | test names |
|---|---|---|---|---|---|
| O-01 | Each job has owner, due (YYYY-MM-DD HH:MM) and safety_critical | Mathew |  | all | test_job_fields_owner_due_safety |
| O-02 | A tick requires evidence | Mathew |  | clinician | test_tick_requires_evidence |
| O-03 | A cancel requires a reason | Mathew |  | clinician | test_cancel_requires_reason |
| O-04 | Done and cancelled are separate states; a cancelled job never counts as done | Mathew |  | patient | test_cancelled_never_counts_as_done |
| O-05 | Only a clinician can cancel a safety_critical job | Mathew |  | patient | test_only_clinician_cancels_safety_critical |
| O-06 | status takes --now and flags overdue jobs | Mathew |  | patient | test_status_now_flags_overdue |
| O-07 | CLI cancel works and is tested | Mathew |  | all | test_cli_cancel |
| O-08 | compile rejects duplicate ids | Mathew |  | all | test_compile_rejects_duplicate_ids |
| O-09 | compile rejects dependency cycles | Mathew |  | all | test_compile_rejects_cycles |
| O-10 | depends_on ids must exist | Mathew |  | all | test_compile_rejects_unknown_dependency |
| O-11 | The log is append-only JSONL of events with job id, actor and timestamp | Mathew |  | all | test_log_append_only |
| O-12 | State is derived only by replaying the log | Mathew |  | all | test_state_from_replay_only |
| O-13 | A job can't be ticked until all its dependencies are done | Mathew |  | patient | test_tick_waits_for_dependencies |
| O-14 | Unticking is blocked while any job that depends on it is done | Mathew |  | patient | test_untick_blocked_by_done_dependent |
| O-15 | status exits 0 all done, 1 otherwise, 2 invalid | Mathew |  | all | test_status_exit_codes |
| O-16 | status shows the next available jobs | Mathew |  | all | test_status_shows_next_jobs |
| O-17 | step() gates type and role; rejected actions give a reason and are not logged | Mathew |  | all | test_rejected_actions_not_logged |
| O-18 | replay(log) rebuilds the same check() report | Mathew |  | all | test_replay_equality |
| O-19 | observe() shows nothing from other episodes | Mathew |  | all | test_observe_isolated_episode |
| O-20 | A synthetic example list (ward discharge) | Mathew |  | all | test_example_compiles |
| O-21 | A scripted agent episode runs deterministically | Mathew |  | all | test_scripted_agent_episode |
| P-01 | roles fixed at session open | TEXT PENDING FROM ROOM |  |  |  |
| P-02 | separate patient and patient-agent roles | TEXT PENDING FROM ROOM |  |  |  |
| P-03 | patient grant and revoke | TEXT PENDING FROM ROOM |  |  |  |
| P-04 | jobs marked agent-doable or human-only | TEXT PENDING FROM ROOM |  |  |  |
| P-05 | tick evidence must reference a real record | TEXT PENDING FROM ROOM |  |  |  |
| P-06 | version lock on approval with a diff view | TEXT PENDING FROM ROOM |  |  |  |
| P-07 | propose tools with the checker run first | TEXT PENDING FROM ROOM |  |  |  |
| P-08 | source-line citations and a gap view | TEXT PENDING FROM ROOM |  |  |  |
| P-09 | three patient consent failures (safety gate) | TEXT PENDING FROM ROOM |  |  |  |
| W-RF-ROLE-CANCEL | Refused: only a clinician's own session can cancel a safety job. | unsigned |  |  | test_line_rf_role_cancel |
| W-RF-PERSON-JOB | Refused: a person must do this job and tick it in their own session. | unsigned |  |  | (blocked) |
| W-RF-EVIDENCE | Refused: a tick needs a record id, such as a result or a sent document. | unsigned |  |  | test_line_rf_evidence |
| W-RF-WAITING | Refused: this job waits on [job text], which is not done yet. | unsigned |  |  | test_line_rf_waiting |
| W-RF-CANCEL-REASON | Refused: cancelling needs a reason. | unsigned |  |  | test_line_rf_cancel_reason |
| W-RF-NOT-APPROVED | Refused: this form or list is not approved by a clinician yet. | unsigned |  |  | (blocked) |
| W-CK-LOOP | Jobs [a] and [b] wait on each other, so neither can be done. | unsigned |  |  | test_line_ck_loop |
| W-CK-NO-SOURCE | Job [n] does not say which line it came from. | unsigned |  |  | (blocked) |
| W-FL-OVERDUE | Overdue: [job text], owner [owner], due [time]. | unsigned |  |  | test_line_fl_overdue |
| W-FL-GAP | No job was made from source line [n]. Approve or add one. | unsigned |  |  | (blocked) |
