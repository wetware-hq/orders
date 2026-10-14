# Rule register

Signer is who signed the rule. class: safety where signed as safety; blank rows are still for Physician to mark.
Wording rows (W-) are the fixed lines this repo emits. All are signed by Physician; the source of truth is WORDING.md, checked word for word by scripts/rulecheck.py. CK-TWO-FACTS is not used: ruling R-03 replaced it with CK-TWO-ITEMS.
P rows not listed here (P-01, P-02) apply only to the other repo and are registered there.

| id | rule | signer | class | protects_role | test names |
|---|---|---|---|---|---|
| O-01 | Each job has owner, due (YYYY-MM-DD HH:MM) and safety_critical | Mathew |  | all | test_job_fields_owner_due_safety |
| O-02 | A tick requires evidence | Mathew |  | clinician | test_tick_requires_evidence |
| O-03 | A cancel requires a reason | Mathew |  | clinician | test_cancel_requires_reason |
| O-04 | Done and cancelled are separate states; a cancelled job never counts as done | Mathew |  | patient | test_cancelled_never_counts_as_done |
| O-05 | Only a clinician can cancel a safety_critical job | Mathew |  | patient | test_only_clinician_cancels_safety_critical |
| O-06 | status takes --now and flags overdue jobs | Mathew |  | patient | test_status_now_flags_overdue, test_not_overdue_at_exact_due_time |
| O-07 | CLI cancel works and is tested | Mathew |  | all | test_cli_cancel |
| O-08 | compile rejects duplicate ids | Mathew |  | all | test_compile_rejects_duplicate_ids |
| O-09 | compile rejects dependency cycles | Mathew |  | all | test_compile_rejects_cycles |
| O-10 | depends_on ids must exist | Mathew |  | all | test_compile_rejects_unknown_dependency |
| O-11 | The log is append-only JSONL of events with job id, actor and timestamp | Mathew |  | all | test_log_append_only |
| O-12 | State is derived only by replaying the log | Mathew |  | all | test_state_from_replay_only |
| O-13 | A job can't be ticked until all its dependencies are done | Mathew |  | patient | test_tick_waits_for_dependencies |
| O-14 | Unticking is blocked while any job that depends on it is done | Mathew |  | patient | test_untick_blocked_by_done_dependent, test_untick_and_cancel_need_right_state |
| O-15 | status exits 0 all done, 3 closed, 1 open, 2 invalid | Mathew |  | all | test_status_exit_codes |
| O-16 | status shows the next available jobs | Mathew |  | all | test_status_shows_next_jobs |
| O-17 | step() gates type and role; rejected actions give a reason and are not logged | Mathew |  | all | test_rejected_actions_not_logged |
| O-18 | replay(log) rebuilds the same check() report; malformed lines are refused and a refused line reports its own refusal | Mathew |  | all | test_replay_equality, test_replay_refuses_malformed_log_lines, test_replay_reports_the_refusal_line |
| O-19 | observe() shows nothing from other episodes | Mathew |  | all | test_observe_isolated_episode |
| O-20 | Synthetic example lists (ward discharge) | Mathew |  | all | test_example_compiles |
| O-21 | A scripted agent episode runs deterministically | Mathew |  | all | test_scripted_agent_episode |
| R-04 | Closed state with its own exit code (3): every job done or cancelled with a reason | Physician | safety | patient | test_closed_state_exit_3 |
| R-04b | A cancelled safety_critical job counts toward closed only if a clinician cancelled it | Physician | safety | patient | test_closed_needs_clinician_cancel_on_safety_job |
| R-05 | Evidence must be the id of a record in the case's records registry; a made-up id is refused | Physician | safety | patient | test_evidence_must_be_registered_record |
| G-01 | Roles: patient, patient_agent, clinician (any registered health professional, free discipline field), clinician_agent | Mathew |  | all | test_roles_are_four_with_discipline |
| G-02 | Permissions are a table keyed by role | Mathew |  | all | test_permissions_table_by_role |
| G-03 | One synthetic allied-health example | Mathew |  | all | test_allied_health_example |
| S-01 | Scope of practice: safety_critical items list allowed disciplines; gate checks role and discipline together | Scientist / Physician | safety | patient | test_scope_of_practice_refuses_other_discipline |
| S-02 | If no disciplines are listed, scope defaults to the approving clinician's discipline | Scientist / Physician | safety | patient | test_scope_defaults_to_approver_discipline |
| S-03 | Unregistered users (carer, assistant, family) are refused; no such role in v1 | Scientist / Physician | safety | patient | test_unregistered_role_refused |
| S-05 | clinician_agent is limited to the scope of practice of the clinician who granted it | Physician | safety | patient | test_clinician_agent_limited_to_granter_scope |
| S-04 | clinician_agent never confirms, cancels or approves; ticks only agent-doable jobs with findable evidence | Physician | safety | patient | test_clinician_agent_rules |
| S-06 | Strict Boolean: every yes/no field in a list (safety_critical, agent_doable) is accepted only if it is a real true or false (type is bool). 1, 0, "yes", "false", null, 1.0 and anything else are refused with RF-NOT-BOOL and the list does not compile | Physician | safety | patient | test_s06_yes_no_fields_strict_bool, test_s06_yes_no_fields_real_bool_accepted |
| R5-01 | Per-call permissions (table CALL_PERMS): every session, including patient_agent and clinician_agent, may call only observe and step; reset, replay, check and export are harness-only and refused with RF-HARNESS-ONLY | Physician | safety | patient | test_r5_01_call_permissions_table, test_r5_01_harness_only_calls_refused, test_r5_01_agents_may_observe_and_step |
| R5-02 | One check per submission (PERMS: submit for every role, review for clinician only): a submit is logged with its result and missing items and counted; a fail returns ST-CHECK-FAIL with the missing lines and the episode continues; only a pass is final (ST-CHECK-PASS). After 3 failed submissions in a row the episode goes to the clinician queue (CQ-THREE-FAILS) and agents get RF-WAIT-CLINICIAN until a clinician reviews; a clinician can always finish in their own session. The logged result is recomputed on replay | Physician | safety | patient | test_r5_02_failed_submission_continues_and_is_logged, test_r5_02_pass_is_final, test_r5_02_every_role_may_submit, test_r5_02_three_fails_queue_and_wait, test_r5_02_clinician_review_unblocks_and_clinician_can_finish, test_r5_02_submission_result_cannot_be_forged_and_replays, test_r5_02_closed_counts_as_pass |
| R5-03 | Read scope per role (visible()): a patient's agent sees only agent-doable, non-safety jobs; a clinician's agent sees only lists approved inside its clinician's scope, and only jobs whose listed disciplines it holds; patients and clinicians see the list. Refused reads get RF-NOT-YOURS, the same line whether or not the item exists | Physician | safety | patient | test_r5_03_observe_patient_sees_whole_list, test_r5_03_observe_patient_agent_only_agent_doable_jobs, test_r5_03_observe_clinician_sees_all, test_r5_03_observe_clinician_agent_only_its_scope, test_r5_03_not_yours_does_not_reveal_existence, test_r5_03_observe_item_shows_done_and_cancelled |
| R5-04 | Export refuses any episode not marked synthetic with RF-EXPORT-REAL (the flag is defined by R8-01) | Physician | safety | patient | test_r8_01_export_needs_synthetic_flag_not_id |
| R8-01 | Synthetic flag: only the harness (open_episode, not in CALL_PERMS or PERMS) writes `synthetic` (true/false) as the first log event when the episode opens; it can never be set again or changed, and replay refuses a second, moved, altered or other-episode flag. Export reads the flag, never the episode id: SYNTH-x without the flag is refused with RF-EXPORT-REAL | Physician | safety | patient | test_r8_01_export_needs_synthetic_flag_not_id, test_r8_01_flag_set_once_first_and_never_changed, test_r8_01_replay_refuses_changed_flag, test_r8_01_only_harness_sets_flag, test_r8_01_cli_open_and_export |
| R8-03 | Clinician queue lists each missing item as its number and its job text as written | Physician |  | clinician | test_r8_03_queue_lists_number_and_text |
| R8-04 | `interpreter` (strict true/false) may be set on tick events only, and only by a clinician session; export carries it | Physician |  | patient | test_r8_04_interpreter_clinician_only, test_r8_04_interpreter_strict_bool, test_r8_04_export_carries_interpreter_and_replays, test_r8_04_interpreter_only_on_tick |
| R8-02 | List grants: a patient's agent needs a grant from the patient's own session naming its exact agent id and the patient (one patient per episode). Without one it sees nothing (RF-NOT-YOURS) and every step gets RF-NO-GRANT-LIST; with one it sees only that patient's agent-doable, non-safety jobs. Revoke names the agent and needs an active grant; after it the agent gets RF-REVOKED and sees nothing. The patient sees PV-GRANT-LIST / PV-REVOKE-LIST | Physician | safety | patient | test_r8_02_patient_agent_without_grant_sees_and_does_nothing, test_r8_02_grant_shows_only_agent_doable_non_safety_jobs, test_r8_02_grant_names_agent_and_patient, test_r8_02_other_agent_not_covered, test_r8_02_only_patient_grants, test_r8_02_revoke_works_the_same_way, test_r8_02_patient_sees_grant_line, test_r8_02_cli_grant_names_agent |
| R10-01 | Grant lines name the agent only by the display name stored at grant time (agent_name on the grant event; refused only when empty or equal to the internal id). PV-GRANT-LIST, PV-REVOKE-LIST and RF-NO-GRANT-LIST fill [agent_name] from that stored name; with no stored name the agent gets RF-NOT-YOURS. | Physician | safety | patient | test_r10_grant_needs_display_name_not_id, test_r10_grant_missing_display_name_refused, test_r10_revoke_line_uses_name_stored_at_grant, test_r10_no_grant_refusal_names_agent_from_stored_name, test_r11_synth_display_name_accepted_and_id_refused |
| R11-01 | Patient refusals show a signed patient line, never a machine code or a blank (PATIENT_LINE maps code to line id); another patient's id or name in a grant or revoke never reaches the patient screen or log. | Physician | safety | patient | test_r11_no_err_on_patient_screens, test_r11_other_patient_never_shown, test_r11_patient_lines_for_each_code, test_r11_cases_cover_every_mapped_code, test_r11_patient_refusal_changes_nothing, test_r11_malformed_action_never_echoes, test_every_patient_reachable_code_has_a_signed_pv_line, test_static_check_fails_on_an_unmapped_code, test_r11_mapping_pinned, test_r11_each_code_shows_its_signed_line, test_r11_refuse_passes_unmapped_codes_through_unchanged, test_every_safety_function_is_listed, test_scan_catches_a_planted_omission |
| T-05 | Build checks and step replies: wrong types in a list are refused with ER-BAD-FIELD, never a crash; CLI commands need exact positions; a failed submit is never final. | Physician | safety | clinician | test_build_refuses_wrong_types, test_list_hash_is_key_order_free_and_utf8, test_cli_commands_need_exact_positions, test_step_return_shapes |
| R8-08 | Record a result on a job: `result` (job id and a non-empty text value of at most 500 characters) is an agent-callable step, appended to the log and replayed; results accumulate per job and export carries them. Same gate as a tick: agents only on agent-doable jobs, a patient's agent only on jobs it may see under its grant, safety jobs only within scope; no result on a cancelled job | Physician | safety | patient | test_r8_08_result_recorded_via_step_and_logged, test_r8_08_results_append_and_replay, test_r8_08_result_value_must_be_text, test_r8_08_result_refusals, test_r8_08_export_carries_results_and_cli |
| P-03 | Patient's agent role (Engineer/Patient): patient, patient's agent and clinician are separate roles. Only a person's own session can confirm, decline or cancel. The patient's agent answering a clinician form is refused with a table line. | Physician | safety |  | test_p03_cancel_own_session_only |
| P-04 | Agent-doable jobs (Physician): each job is marked agent-doable or human-required. An agent may tick only agent-doable jobs. | Physician | safety |  | test_p04_agent_doable_jobs |
| P-05 | Tick evidence (Physician): an agent's tick must point to a record the environment can find (e.g. a result or sent document), never the agent's own statement. A made-up id is refused. | Physician | safety |  | test_p05_evidence_is_findable_record |
| P-06 | Version lock (Physician): approval covers that form/list version only. Any edit needs fresh approval by a clinician; the approval view shows only what changed from the last approved version. | Physician | safety |  | test_p06_version_lock_and_diff_view |
| P-07 | Propose tools (Director/Scientist): propose_form and propose_list take effect only after a clinician approves in their own session. A proposed form must first pass the fixed checker: one fact per question, no negatives, alert answer set, conditions point back only and never loop, safety-critical questions marked, every condition has plain wording. | Physician | safety |  | test_p07_propose_needs_checker_and_approval |
| P-08 | Citations and gap view (Scientist): each proposed job cites its source line; the approval view lists source lines that produced no job. | Physician | safety |  | test_p08_source_line_citations_and_gap_view |
| P-09 | Roles fixed at session start (Engineer): roles are set when the session opens and never passed in a call. | Physician | safety |  | test_p09_roles_fixed_at_session_open |
| W-RF-ROLE-CANCEL | Refused: only a clinician's own session can cancel a safety job. | Physician (signed) |  |  | test_line_rf_role_cancel |
| W-RF-PERSON-JOB | Refused: a person must do this job and tick it in their own session. | Physician (signed) |  |  | test_line_rf_person_job |
| W-RF-EVIDENCE | Refused: a tick needs a record id, such as a result or a sent document. | Physician (signed) |  |  | test_line_rf_evidence |
| W-WT-WAITING | Waiting: this job can start once [job text] is done. | Physician (signed) |  |  | test_line_wt_waiting |
| W-RF-CANCEL-REASON | Refused: cancelling needs a reason. | Physician (signed) |  |  | test_line_rf_cancel_reason |
| W-RF-NOT-APPROVED | Refused: this form or list is not approved by a clinician yet. | Physician (signed) |  |  | test_line_rf_not_approved, test_unapproved_list_blocked_line_first |
| W-CK-LOOP | Jobs [a] and [b] wait on each other, so neither can be done. | Physician (signed) |  |  | test_line_ck_loop |
| W-CK-NO-SOURCE | Job [n] does not say which line it came from. | Physician (signed) |  |  | test_line_ck_no_source |
| W-FL-OVERDUE | Overdue: [job text], owner [owner], due [time]. | Physician (signed) |  |  | test_line_fl_overdue |
| W-FL-GAP | No job was made from source line [n]. Approve or add one. | Physician (signed) |  |  | test_line_fl_gap |
| W-RF-EVIDENCE-NOT-FOUND | Refused: no record with this id was found. | Physician (signed) |  |  | test_line_rf_evidence_not_found |
| W-ST-DONE | Done: every job is finished. | Physician (signed) |  |  | test_line_st_done |
| W-ST-CLOSED | Closed: every job is finished or was cancelled with a reason. | Physician (signed) |  |  | test_line_st_closed |
| W-PV-NEXT | Next for you: [question or job plain wording] | Physician (signed) |  |  | test_line_pv_next |
| W-PV-NOTHING-NEXT | There's nothing for you to do right now. | Physician (signed) |  |  | test_line_pv_nothing_next |
| W-ER-DUP-ID | Setup error: the id [id] is used more than once. | Physician (signed) |  |  | test_line_er_dup_id |
| W-ER-BAD-FIELD | Setup error: [field] in [item] is missing or not allowed. | Physician (signed) |  |  | test_line_er_bad_field |
| W-ER-BAD-COMMAND | Command not recognised. Run with --help to see the commands. | Physician (signed) |  |  | test_line_er_bad_command |
| W-RF-SCOPE | Refused: this item can only be confirmed, cancelled or ticked by [disciplines]. | Physician (signed) |  |  | test_line_rf_scope |
| W-RF-UNREGISTERED | Refused: only registered health professionals and patients can use this in version 1. | Physician (signed) |  |  | test_line_rf_unregistered |
| W-RF-CLIN-AGENT | Refused: a clinician's assistant can draft and propose, but cannot confirm, cancel or approve. | Physician (signed) |  |  | test_line_rf_clin_agent |
| W-RF-NOT-BOOL | Refused: answers must be Yes or No. | Physician (signed) |  |  | test_s06_yes_no_fields_strict_bool |
| W-RF-HARNESS-ONLY | Refused: only the system running this session can start it again or replay it. | Physician (signed) |  |  | test_r5_01_harness_only_calls_refused, test_every_line_id_used_in_code_exists_in_the_copy |
| W-RF-NOT-YOURS | Refused: you don't have access to this. | Physician (signed) |  |  | test_r5_03_not_yours_does_not_reveal_existence, test_every_line_id_used_in_code_exists_in_the_copy |
| W-RF-EXPORT-REAL | Refused: only synthetic sessions can be exported. | Physician (signed) |  |  | test_r8_01_export_needs_synthetic_flag_not_id, test_every_line_id_used_in_code_exists_in_the_copy |
| W-ST-CHECK-PASS | Submitted and checked: complete. This result is final. | Physician (signed) |  |  | test_r5_02_pass_is_final, test_every_line_id_used_in_code_exists_in_the_copy |
| W-ST-CHECK-FAIL | Submitted and checked: not complete yet. See what's missing below. | Physician (signed) |  |  | test_r5_02_failed_submission_continues_and_is_logged, test_every_line_id_used_in_code_exists_in_the_copy |
| W-CQ-THREE-FAILS | For a clinician to check: this was submitted 3 times without passing. Missing: [items]. | Physician (signed) |  |  | test_r5_02_three_fails_queue_and_wait, test_every_line_id_used_in_code_exists_in_the_copy |
| W-RF-WAIT-CLINICIAN | Refused: a clinician must look at this before it can be submitted again. | Physician (signed) |  |  | test_r5_02_three_fails_queue_and_wait, test_every_line_id_used_in_code_exists_in_the_copy |
| W-PV-GRANT-LIST | [agent_name] can now help with your care to-do list, from [date]. | Physician (signed) |  |  | test_r8_02_patient_sees_grant_line |
| W-PV-REVOKE-LIST | [agent_name] can no longer help with your care to-do list, from [date]. | Physician (signed) |  |  | test_r8_02_revoke_works_the_same_way, test_r10_revoke_line_uses_name_stored_at_grant |
| W-RF-NO-GRANT-LIST | Refused: the patient has not given [agent_name] permission to help with this list. | Physician (signed) |  |  | test_r10_no_grant_refusal_names_agent_from_stored_name |
