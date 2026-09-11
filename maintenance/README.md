# Daily Codex upstream maintainer

Maya resumes the existing `codex-upstream-maintainer` task daily at 09:00
America/New_York under human request #3423. The systemd user timer checks every
30 minutes for retries, with persistent catch-up after reboot/login. The existing
Hollywood runtime supplies the persistent agent, model, task history and controls.
No room restart is needed.

The runner saves a pending command before delivery, retries the same idempotent
command ID after failures or lost HTTP replies, and verifies a durable task event
before recording dispatch. `schedule.json` tracks dispatch, not integration success.
Actual results belong in the task conversation and maintainer `report.md`.
Runtime failures remain visible in the room; each daily invocation requests
continuation of unfinished integration. Room pause controls remain effective.

Install from the repository root:

```sh
install -d ~/.local/share/losangelex-maintainer ~/.config/systemd/user
install -m 644 hollywood_room/maintainer.py ~/.local/share/losangelex-maintainer/maintainer.py
install -m 644 maintenance/losangelex-maintainer.service maintenance/losangelex-maintainer.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now losangelex-maintainer.timer
systemctl --user start losangelex-maintainer.service
```

Requires an existing default-team task `codex-upstream-maintainer`, Maya, the room
at localhost:18766 and its client-token file. The unit targets
`%h/Development/losangelex-next`; adjust for other installations. Failed delivery
is recorded in `~/.local/state/losangelex-maintainer/schedule.json` and the journal.

```sh
systemctl --user list-timers losangelex-maintainer.timer
journalctl --user -u losangelex-maintainer.service
cat ~/.local/state/losangelex-maintainer/schedule.json
systemctl --user disable --now losangelex-maintainer.timer
```

The prompt requires fetching OpenAI Codex main, integrating into a named candidate
branch in a separate worktree, preserving the dirty active checkout, reusing any
unfinished candidate, and recording SHAs, compatibility checks, tests and build
results. Cleanup is limited to maintainer-owned disposable artifacts. Landing is
coordinated with the active fork owner after validation; the scheduler never
deploys builds. Consult `~/.local/state/losangelex-maintainer/report.md` and task
history for actual integration outcomes.
