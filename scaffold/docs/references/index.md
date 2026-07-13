# References

Store external or generated reference material here. Agents read these files only when `docs/project-map.md`, the active feature, or a handoff event points to them.

- `worklog-events.md`: lifecycle event contract; read only when emitting, validating, or analyzing lifecycle telemetry.
- `lifecycle-event.schema.json`: canonical lifecycle event schema; validate before appending telemetry.
- `builder-handoff.schema.json`: canonical Builder handoff preflight schema; validate before spawning Reviewer.
