## Next Steps

### Remaining losangelex runtime bug

The current autonomous Hollywood wake path is still too coarse.

Right now `losangelex` uses a single `recent_activity_at` timestamp in:

- `codex-rs/app-server/src/hollywood.rs`
- `codex-rs/app-server/src/codex_message_processor.rs`

That allows the app-server to synthesize a local message like:

- `sender_id = "hollywood-system"`
- `message_id = 0`
- body starting with `Autonomous Hollywood follow-up: ...`

even when there is no new unhandled external update worth interrupting work for.

### Correct fix

Replace timestamp-only autonomous wake logic with explicit pending-event state.

Required changes:

1. Track wake-worthy Hollywood events by `(room, message_id)` instead of only `recent_activity_at`.
2. Store lifecycle per event:
   - `delivered`
   - `handled`
   - `ignored`
3. Only synthesize an autonomous follow-up when at least one external pending event is still unhandled.
4. Never let self-authored traffic create a pending autonomous wake event.
5. Clear or mark pending events when the session visibly acts on them.
6. Add TUI-visible status later so humans can see why a Hollywood wake happened.

### Regression coverage still needed

Add or update tests proving:

1. Self-authored ambient room traffic does not restart idle reasoning.
2. Duplicate or already-surfaced Hollywood events do not synthesize a second autonomous follow-up.
3. A real new focused or broadcast external message in a wake room still triggers the expected wake.

### Publish / release follow-up

Before calling the system stable for multi-agent coordination, re-run an end-to-end test with:

1. one agent in `main`
2. one invite into a task room
3. explicit acknowledgement and join
4. visible handling of a real coordination request without human nudging

Until that passes consistently, Hollywood transport/routing should be considered improved but not fully solved from the user point of view.
