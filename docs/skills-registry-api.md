# Skills Registry API

The tenant Skill registry stores validated manifests and lifecycle metadata. It does not execute user-supplied code.

## API namespace

All routes below are tenant-scoped and use the application's normal authentication and tenant-context middleware.

Base path: `/api/skill-registry/skills`

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/skill-registry/skills` | List registered Skills |
| POST | `/api/skill-registry/skills` | Register a Skill manifest |
| GET | `/api/skill-registry/skills/{skill_id}` | Read a Skill |
| PUT | `/api/skill-registry/skills/{skill_id}` | Replace a manifest with a higher version |
| DELETE | `/api/skill-registry/skills/{skill_id}` | Delete an unreferenced Skill |
| POST | `/api/skill-registry/skills/{skill_id}/enable` | Enable metadata record |
| POST | `/api/skill-registry/skills/{skill_id}/disable` | Disable metadata record |
| POST | `/api/skill-registry/skills/validate` | Validate without persistence |
| GET | `/api/skill-registry/skills/{skill_id}/preview` | Preview stored manifest and validation |
| GET | `/api/skill-registry/skills/{skill_id}/versions` | List immutable version metadata |
| GET | `/api/skill-registry/skills/{skill_id}/audit` | Read lifecycle audit records |
| POST | `/api/skill-registry/skills/{skill_id}/execute` | Fail-closed execution request (409) |
| GET | `/api/skill-registry/skills/{skill_id}/executions` | Read metadata-only execution history |

The existing `/api/skills` routes are retained for backward compatibility. New registry lifecycle APIs deliberately use a separate namespace to avoid route shadowing.

## Manifest

Example:

```json
{
  "skill_id": "content_outline",
  "name": "Content outline",
  "version": "1.2.0",
  "category": "content",
  "description": "Creates a structured outline",
  "instructions": "Produce a JSON outline from the supplied brief.",
  "permissions": ["read:own_inputs"],
  "inputs": {"type": "object"},
  "outputs": {"type": "object"},
  "config": {}
}
```

- `skill_id` is immutable and unique within the tenant.
- `version` uses semantic version syntax; updates must increase the numeric major/minor/patch tuple.
- Manifest size is limited to 64 KiB.
- Unknown fields and unsupported permissions are rejected.
- Validation results and canonical manifest SHA-256 are persisted for traceability.
- Versions are immutable; updates append a version record.

## Lifecycle and safety

Registration does not imply approval or runnability. Enabling a Skill changes only its metadata state. All custom Skills remain `blocked_no_sandbox` and report `execution_enabled=false`.

Execution requests currently:
1. Resolve the Skill from the current tenant's database context.
2. Serialize and size-check the input without executing it.
3. Persist only the byte count, rejection reason, execution ID, and diagnostic ID.
4. Return HTTP 409. Input content and input hashes are not stored.

No user code is passed to `eval`, `exec`, a shell, a subprocess, or a host Python import. There is no in-process fallback. Execution may only be enabled after an independently deployed sandbox passes the threat-model, network/filesystem isolation, resource limit, tenant isolation, crash recovery, and adversarial security gates in [the sandbox design](skills-sandbox-security-design.md).

## Persistence

- Migration 119 adds manifest, validation, version, reference, and audit storage.
- Migration 120 adds execution-attempt metadata.
- Skill rows are tenant-local; the APIs use the tenant-scoped connection supplied by the application context.
- Referenced Skills cannot be deleted. Deletion removes their version rows but preserves audit and execution history.
