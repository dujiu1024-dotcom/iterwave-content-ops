# Review-adoption and child rebase contract

## Two-stage review

1. `POST /api/plan_adjustments/adopt_master` creates a `review_adoption` mother-plan draft. It does not activate the mother plan or change child-plan pointers.
2. A human approves the mother-plan draft. Switching the effective mother plan pauses eligible old children with `pause_reason=master_plan_switch` and records `paused_by_master_version_id`.
3. `POST /api/plan_adjustments/adopt` with `target_master_plan_version_id` creates child rebase drafts only for children paused by that exact target. Child drafts still require a separate human review before activation.

## Batch eligibility and skipped children

The endpoint inspects the full relevant cohort (the source mother version's children when a target is supplied; the feedback source child when `effect_review_id` narrows the request). It returns:

- `drafts`: eligible child drafts created or idempotently reused. These are never effective until separately reviewed.
- `skipped`: each ineligible child with `sub_plan_id`, `platform`, current `status`, stable `reason`, and a human-readable `detail`.
- `eligible_count` / `expected_draft_count`: number of eligible children in the inspected cohort.
- `skipped_count`: number of explicitly reported ineligible children.
- `status`: `completed` when no child was skipped, otherwise `partial`. A partial response means the eligible subset completed successfully and every excluded child is listed; it does not mean a transaction partially committed.

Stable skip reasons:

| Reason | Meaning |
| --- | --- |
| `no_current_version` | Child has no current version pointer. |
| `no_effective_version` | Current child version is not effective. |
| `not_paused_by_target` | Child was not paused by the requested mother-plan switch. Manual pauses are not implicitly rebased. |
| `terminal_status` | Child is archived, cancelled, completed, or otherwise in a terminal/non-eligible state. |

The endpoint keeps all-or-nothing transaction semantics for the eligible subset: if any eligible child draft fails validation or persistence, all drafts created by that request are rolled back and the request fails. Skipped children are never mutated. If the cohort exists but every child is ineligible, the endpoint returns a successful response with an empty `drafts` array and explicit `skipped` entries.

## Retry and audit behavior

The existing idempotency rules remain in force: a retry for the same adoption reuses a matching draft instead of creating a duplicate; conflicting draft content is rejected. The response's `drafts` and `skipped` arrays let the UI distinguish completed work from children that need separate operator action. Historical child versions and production records remain attached to their original mother-plan snapshots.
