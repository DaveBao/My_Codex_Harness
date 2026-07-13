# Tech Debt Tracker

Record evidence-backed optimization candidates without adding them to the active DAG.

## Entry Format

- **ID:** stable debt identifier.
- **Wave:** wave that exposed the candidate.
- **Feature IDs:** merged features involved.
- **Paths / symbols:** exact implementation locations.
- **Capability:** duplicated behavior or responsibility.
- **Evidence:** bounded diff, behavior, or validation evidence.
- **Risk:** correctness, security, data integrity, maintainability, or cost impact.
- **Disposition:** `deferred`, `correctness-blocking`, or `resolved`.
- **Suggested consolidation:** likely canonical module or entry point; this is not an approved plan.

Ordinary duplication is `deferred` and never blocks feature delivery. Use `correctness-blocking` only
for conflicting sources of truth, inconsistent observable behavior, security/data-integrity risk,
or a known correctness dependency for the next wave. Human planning creates any optimization
feature; this tracker does not modify `TODO.json`.
