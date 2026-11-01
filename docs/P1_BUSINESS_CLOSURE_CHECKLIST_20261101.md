# P1 Business Data and Core Workflow Closure

Baseline: `main` at `fbc1325db37b7da0b3cdbbdb299e296b5e95f576` (2026-11-01).
Working branch: `remediation/p1-business-closure-20261101`.

This tracker separates source implementation, automated verification, and real-platform acceptance. A task is not closed by code presence alone.

## P1-01 Platform account identity
- [ ] Keep user-entered identity explicitly labeled as user-provided.
- [ ] Read platform identity from an authenticated platform endpoint where supported.
- [ ] Persist verification source, checked_at, and result; represent unsupported/unavailable as unverified.
- [ ] Test stale sessions and identity changes.

## P1-02 Login-session validity
- [ ] Implement authoritative per-platform session validation where supported.
- [ ] Distinguish valid, expired, unknown, and check-error.
- [ ] Fail closed for expired/unknown sessions before side-effecting tasks.
- [ ] Test task dispatch blocking and recovery after re-authentication.

## P1-03 Historical data integrity
- [ ] Inventory every legacy array/truncated read path and migrate production consumers to pagination.
- [ ] Add resumable backfill cursor/checkpoint and safe restart semantics.
- [ ] Add deterministic deduplication keys and idempotent upserts.
- [ ] Reconcile source totals, database totals, API totals, and UI totals per tenant/platform.
- [ ] Report inaccessible/deleted source records separately from missing/unfetched records.

## P1-04 Timezone
- [ ] Document UTC versus legacy-naive timestamp semantics and migration rules.
- [ ] Ensure all new persisted instants are UTC-aware or explicitly business-local by field contract.
- [ ] Add cross-day, ISO-week, month/year boundary and DST tests.
- [ ] Verify reports, scheduler, quotas, and calendar use the tenant business timezone.

## P1-05 Content template lifecycle
- [ ] Add active/archived lifecycle and prevent destructive deletion while referenced.
- [ ] Persist immutable template version snapshots at content-generation time.
- [ ] Keep historical content linked to the exact template version used.
- [ ] Test template edits/deactivation/deletion against old and in-flight content.

## P1-06 Industry template lifecycle
- [ ] Add active/archived lifecycle and restore.
- [ ] Persist immutable version history on every update.
- [ ] Pin production runs to the selected template version.
- [ ] Retain history and block destructive deletion when referenced.

## P1-07 Personal opinions and external references
- [ ] Persist tenant-scoped personal opinions and external references.
- [ ] Normalize URLs/text and deduplicate by stable fingerprint.
- [ ] Implement relevance filtering against topic, account positioning, and platform.
- [ ] Feed selected references into both automatic and manual generation.
- [ ] Persist per-content usage/citation records with source/version and selection reason.
- [ ] Test that unrelated sources are excluded and actual used sources are traceable.

## P1-08 Platform draft saving
- [ ] Verify all supported platforms with real accounts.
- [ ] Verify multi-image upload count, order, and actual platform preview.
- [ ] Verify tags and title/body fields are persisted correctly.
- [ ] Persist platform draft identifier or an explicit unavailable marker.
- [ ] On timeout, read back platform state before retry; never blindly create a duplicate.
- [ ] Retain redacted evidence for success, ambiguous result, and failure.

## P1-09 Per-content analytics
- [ ] Read all historical pages with resumable collection.
- [ ] Distinguish true zero, not collected, unsupported, partial, and failed.
- [ ] Add per-content retry and last-attempt diagnostics.
- [ ] Define platform-specific metric mappings and normalized metric contracts.
- [ ] Reconcile counts and sample individual records against platform UI.

## P1-10 Business configuration
- [ ] Re-run hard-coded business-rule scan against this baseline and all new code.
- [ ] Move mutable thresholds, schedules, retry policies, and quota rules to tenant configuration.
- [ ] Keep security ceilings in code and document why they are immutable.
- [ ] Verify config persistence, runtime reload, tenant isolation, and backward compatibility.

## P1-11 Usage display（自用不设额度，仅展示用量）
产品定位为自用，不设额度，也不做额度预留/结算；本项只要求如实展示用量。
- [ ] 不设额度：不做额度拦截，也不做预留与结算（原「强化额度预留结算」不再实施）。
- [ ] 仅展示用量：按业务时区统计并展示调用次数（成功/失败分开计）。
- [ ] 前端展示的用量与后端记录一致。

## P1-12 Topic and operations-goal lifecycle
- [ ] Provide archive and restore operations with optimistic version checks.
- [ ] Provide deactivate/reactivate semantics without deleting history.
- [ ] Keep plan-round, task, draft, and content associations immutable.
- [ ] Block hard deletion of plans with historical associations; prefer soft deletion.
- [ ] Exclude inactive/archived plans from automatic production and scheduling.
- [ ] Test concurrent updates, active-task cancellation, restore, and historical traceability.

## Evidence / acceptance log

| Item | Code | Automated tests | Production / real platform | Status |
|---|---|---|---|---|
| P1-01 | Pending | Pending | User device required | Open |
| P1-02 | Pending | Pending | User device required | Open |
| P1-03 | Pending | Pending | Data reconciliation required | Open |
| P1-04 | Partial | Partial | Deployment timezone required | Open |
| P1-05 | Pending | Pending | Not applicable | Open |
| P1-06 | Partial | Pending | Not applicable | Open |
| P1-07 | Pending | Pending | Not applicable | Open |
| P1-08 | Partial | Partial | Real platform accounts required | Open |
| P1-09 | Partial | Pending | Real platform comparison required | Open |
| P1-10 | Partial | Pending | Deployment config required | Open |
| P1-11 | Partial | Pending | Not applicable（自用不设额度，仅展示用量） | Open |
| P1-12 | Partial | Pending | Not applicable | Open |

No item is considered closed until its acceptance evidence is recorded.
