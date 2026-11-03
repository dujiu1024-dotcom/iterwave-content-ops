# Calendar and scheduling API

This document records the calendar and draft-scheduling contract used by the web client.

## Calendar query

`GET /api/schedule?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`

- Both dates are calendar dates in the application's configured business timezone (currently Asia/Shanghai).
- The range is inclusive.
- Invalid dates and an end date earlier than the start date return HTTP 422.
- The response contains scheduled drafts, saved drafts used as calendar fallback events, and eligible unscheduled drafts.
- The event list is bounded to 200 items. Clients should request a bounded visible range (month/week/day), not the entire history.

## Create a schedule

`POST /api/schedule`

```json
{
  "draft_id": "draft-id",
  "scheduled_publish_at": "2026-12-01T10:30"
}
```

The timestamp is a timezone-naive local wall-clock value interpreted in Asia/Shanghai. UTC suffixes and explicit offsets are rejected to prevent silent timezone conversion. The operation only writes `drafts.scheduled_publish_at`; it does not publish, enqueue a publish operation, or grant publication authorization.

A draft must be in `draft_pending` or `draft_saved`. A second draft on the same platform cannot occupy the same scheduled minute; a collision returns HTTP 409.

## Update or cancel a schedule

`PATCH /api/schedule/{draft_id}`

```json
{ "scheduled_publish_at": "2026-12-01T11:00" }
```

Pass `null` to cancel:

```json
{ "scheduled_publish_at": null }
```

The same draft-state, timezone, and conflict rules apply. A conflict or stale/ineligible draft is returned as an HTTP error; clients must display the error and refresh rather than silently retrying.

## Publication safety

Calendar scheduling is metadata for planning only. Reaching the scheduled time does not itself execute publication. Publication remains governed by the existing explicit user authorization, review, and platform safety flow.


## Centralized content review

### List the review queue

`GET /api/review-queue`

Query parameters:

- `status`: `pending` (default), `approved`, `rejected`, or `all`.
- `platform`: optional platform key.
- `task_type`: optional draft state (currently `draft_pending` or `draft_saved`).
- `from` / `to`: inclusive content creation dates in `YYYY-MM-DD` format.
- `page` / `page_size`: pagination; page size is capped at 100.

The response includes round metadata, draft state, G2 outcome, and the latest ten review audit events for each content item. Invalid dates and reversed ranges return HTTP 422.

### Approve a content item

`POST /api/contents/{content_id}/approve`

```json
{ "reason": "人工审核通过" }
```

Approval is recorded in `user_override` and is idempotent for repeated requests. G2 must have passed and the content must have an eligible unpublished draft. Approval does not modify draft publication state and never triggers publication.

### Reject a content item

`POST /api/contents/{content_id}/reject`

```json
{ "reason": "驳回原因" }
```

A non-empty reason is required. Duplicate rejection is rejected with HTTP 409. The audit event includes the content identifier so it can be displayed in the centralized review history.

### Resubmit a rejected item

`POST /api/contents/{content_id}/resubmit`

The existing resubmission flow remains in force: the content must pass G2 again before it can be resubmitted. Resubmission is recorded as a separate audit event and returns the item to the pending review queue.
