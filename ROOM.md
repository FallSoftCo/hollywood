# Hollywood shared team room

The `hollywood-room` service owns persistent Codex app-server threads. Terminal and Android
clients submit commands and replay events; disconnecting or rebuilding either client does not
stop agent turns. The legacy `/hollywood/v1` service and CLI remain separate.

This implementation supports a development directory or a Git project per service and four
named teammates (`coordinator`, `maya`, `theo`, `rowan`). Every task/teammate pair has a separate
root conversation. Workspace mode determines whether those conversations share files or use
separate Git worktrees.

## Run and install

Use Losangelex's `losangelex/pin_room.py` to install locked, non-editable packages and a Linux
user service. It shares the existing Codex sign-in through a file link while keeping room
databases, logs and runtime configuration separate. Existing skills and rules remain available.
It never restarts an already running service when installing another release.

For an isolated development instance:

```sh
uv sync --locked --extra room --extra push
uv run --extra room --extra push hollywood-room \
  --state /absolute/private/development-state \
  --workspace /absolute/git/project \
  --codex /absolute/pinned/package/bin/codex \
  --codex-home /absolute/configured/codex-home \
  --port 18767
```

For a development directory containing several repositories and ordinary subfolders, pass
`--workspace /absolute/Development --workspace-mode shared`. Agents start at that root and
edit the actual named subfolders. The workspace-write sandbox covers that directory; requests
outside it retain the normal approval policy. Agents are instructed to read applicable nested
`AGENTS.md` files, inspect existing changes, include repository paths in delegation briefs, and
coordinate overlapping edits. Conversations are independent; shared files are not isolated or
automatically locked. Git operations and worktrees can still be requested explicitly.

The default `--workspace-mode worktree` preserves the original single-repository workflow.
When changing the configured root or mode, activate the new release while the runtime is idle.
Existing threads resume with the current root, mode and instructions while retaining their
conversation history. Existing worktrees and their uncommitted edits are left in place.

The API binds to loopback. Put an authenticated private HTTPS connection, such as Tailscale
Serve, in front of it for Android. All endpoints except `/health` require the owner bearer
token from `STATE/client-token`. Do not expose an unauthenticated proxy or put that token in a URL.

## API v2

Paths below start with `/hollywood/v2`. JSON requests reject unknown fields and are capped at
16 KiB. Each submitted message/answer/control has a caller-generated `commandId`. Retrying the
same ID and contents returns the original receipt; changing its contents fails with 409.

| Method/path | Purpose |
| --- | --- |
| `GET overview` | Team, tasks, agent states, open attention and unconfirmed deliveries |
| `GET messages?after=ID&limit=100&task=TASK` | Paginated durable history; task is optional |
| `GET history?visibility=room&limit=50` | Newest chat page; exact conversation, optional task, and exclusive `before` / `after` or inclusive `around` cursor |
| `GET events?after=ID` | SSE replay; also accepts `Last-Event-ID` |
| `POST messages` | `commandId`, `body`, optional `task`, `targets`, `visibility`, `replyTo` |
| `GET messages/ID` | Current message and attention state, including resolved/expired actions |
| `GET commands/ID` | Original command receipt and delivery states |
| `POST control` | `commandId`, `task`, `agent`, `action`: `pause`, `resume`, or `interrupt` |
| `POST attention/ID/answer` | `commandId`, `body`; binds an answer to its original task/agent |
| `POST approval/ID/answer` | Explicit `decision`: `accept` or `decline` for a live upstream request |
| `POST devices` | Owner registers `id` and Firebase installation ID `fid` |
| `DELETE devices/ID` | Stops future push attempts for that device |

`history` returns messages newest first, with nullable `olderCursor` and `newerCursor` event IDs.
Use the older cursor as `before`, the newer cursor as `after`, or `around=ID` to restore a reading
position or open an old notification. Pages contain at most 100 events and reflect current
question/approval state. `visibility` is exactly `room` or `direct:AGENT`; omitted `task` includes
all tasks in that conversation. Replies include an optional, bounded source excerpt. Keyset
pagination and indexed conversation queries keep older history accessible without offsets or a
client history limit. The existing forward replay API and agent-facing reads retain their behavior.

Unnamed room messages address the coordinator. `Maya, …` or `@maya …` addresses Maya publicly.
Explicit `visibility: "direct"` requires one target. Replies retain the original task and
private-recipient scope. Agents use validated Hollywood tools; peer deliveries use app-server
tool output, retaining tool authority. A delegated worker also receives the original public
human request with its original addressee identified. Private source messages cannot be
forwarded as delegated authorization.

**P0 context review:** the one-time forwarded human request can exceed 1,000 tokens. It is
capped at 6,000 UTF-8 bytes plus its fixed attribution, remains below the 10K-token item limit,
and preserves the original human text and addressee. Manual review focused on authenticated
source identity, rejecting private sources, keeping peer briefs at tool authority, and the
captured "do not write it yourself" delegation failure. The live regression and transactional
source-visibility tests cover these cases. Other tool outputs are capped at 900 UTF-8 bytes;
published synthetic summaries are capped at 700 bytes.

Private questions and replies retain their visibility. `hollywood_brief` shares only the
agent's explicit task summary with the coordinator. The model must choose the summary; live
evaluations test this behavior. This is conversation separation in a trusted, single-owner team,
not isolation between hostile operating-system users.

Commands, task creation, authorization references, assignment briefs and deliveries commit
transactionally. A database lock permits one service owner. A crash leaves dispatches whose
effects cannot be established as `uncertain`; the service does not blindly repeat them.
Four roots may run concurrently. Automatic chains have depth/turn limits; new human input
renews that conversation's automatic-turn allowance. Explicit task updates notify the
coordinator in the originating task.

Approvals carry the concrete command or file changes from the app-server request. Missing
action details disable acceptance. Restarting the runtime expires approvals; durable records
never recreate an old live request. An interrupt records its target turn before contacting Codex.

## Android notifications

Configure the Android app in Firebase and supply the host with an ADC credential file and
`--firebase-project PROJECT --firebase-credentials /private/path/adc.json`. The Python SDK
supports service-account impersonation and workload identity federation. Give the sending
identity the Firebase Cloud Messaging API Admin role on the intended project. Use federation
for unattended production hosts where an external workload identity is available.

Push payloads contain only opaque attention and device-registration IDs. The app displays a generic notification
without connecting to the private host. Opening it fetches current authenticated state.
High-priority delivery is used for questions and approvals; completion alerts use normal
priority. Attempts persist per device, retry with backoff, skip resolved/expired attention and
do not send historical notifications to newly registered devices. Provider acceptance is not
treated as a device receipt. Device registration and the last received alert are shown in the app.
Registrations are scoped to the connected host. Alerts and intents for another registration are
ignored, so switching hosts cannot open an unrelated task with the same numeric attention ID.

## Evidence and remaining limits

```sh
uv run --extra room --extra push pytest -q
uv run --extra room python tests/evaluate_room.py --help
uv run --extra room --with textual==6.12.0 python tests/evaluate_lifecycle.py --help
uv run --extra room python tests/evaluate_shared_workspace.py --help
```

The explicit evaluations use real Codex model runs, disposable worktrees and frozen requests
captured from earlier failures. They test peer handoff, private summaries, per-task context,
real delegated file creation, client rebuilds during a live turn, steering and context recovery
after an idle runtime restart. Reports record the model, binary hash, checks and transcripts.
The shared-workspace evaluation freezes real room documentation in two nested repositories,
resumes an existing thread at a non-Git development root, checks per-folder instructions and
preserved edits, and delegates a review to an independent teammate in the selected subfolder.
Deterministic tests cover storage/transport invariants rather than simulating model judgment.

Rebuilding clients preserves running turns. Replacing the host process during a turn can still
interrupt it: keep the installed release running, test another instance on a separate port/state,
and switch runtime versions after work is idle. Idle thread resumption is tested. Legacy session
migration, aggregation of independently configured roots, arbitrary team membership, automatic merge/integration of
worktrees, and a comparative productivity benchmark against Codex remain future work.

Authentication references: [Google service-account impersonation](https://cloud.google.com/docs/authentication/use-service-account-impersonation)
and [Firebase Android messaging](https://firebase.google.com/docs/cloud-messaging/android/get-started).
