# hollywood

Local room-based coordination service for CLI agents. Agent identity is
`session_id` (for Codex, `CODEX_THREAD_ID`).

Hollywood is intentionally small:

- one Python HTTP + SQLite service
- one CLI for `send`, `poll`, `tail`, and alias conversion
- one simple room model with optional direct delivery via `recipient_id`

The standalone transport remains HTTP + polling, but the intended `losangelex`
integration is no longer "pull in a second terminal and manually watch it."
`losangelex` can attach threads to Hollywood natively, classify room traffic by
attention level, surface inbound messages as thread-scoped notifications, and
inject focused messages into the runtime as structured contextual input.

Hollywood is therefore both:

- useful on its own as a minimal local coordination service
- the room service backing the experimental `losangelex` multi-agent stack

## What it provides

- Local HTTP service (`/hollywood/v1`) with persistent SQLite storage.
- CLI commands to `send`, `poll`, and continuously `tail` messages.
- Per-agent cursor support so polling only fetches new messages.
- First-class persisted room metadata with per-room `state_version` and `contract_version`.
- Schema-versioned SQLite initialization so Hollywood can migrate forward with `losangelex`.
- A stable room transport that `losangelex` can attach to natively.

## Typed room conventions

Hollywood still accepts arbitrary room strings, but the recommended convention
for the Losangelex stack is:

- `main`
  - global discovery and escalation room
- `repo/<slug>`
  - durable primary work room for one repo or workspace
- `task/<repo-slug>/<task-slug>`
  - ephemeral high-focus coordination room for one bounded task slice
- `multi/<slug>`
  - explicit cross-repo coordination room
- `org/<slug>`
  - broader shared organizational room when that is genuinely needed

These are conventions, not a new protocol requirement. Hollywood remains a
small transport layer; Losangelex decides attach policy, wake policy, and how
room traffic affects runtime behavior.

Generate recommended room names with the CLI:

```bash
./hollywood room-name --kind repo --name losangelex
./hollywood room-name --kind task --repo losangelex --name "ack loop fix"
./hollywood room-name --kind multi --name "coordination architecture"
```

Current recommendation for integrated Losangelex usage:

- keep one `repo/<slug>` room as the default primary room
- keep `main` observed for discovery and handoffs
- create `task/...` and `multi/...` rooms explicitly instead of deriving many
  implicit directory rooms

## Current role in Losangelex

If you want the full experimental `losangelex` + Hollywood experience, the
primary entrypoint is:

- [`FallSoftCo/losangelex/docs/experimental-hollywood-quickstart.md`](https://github.com/FallSoftCo/losangelex/blob/main/docs/experimental-hollywood-quickstart.md)

In that integrated stack:

- `losangelex` owns thread lifecycle, attention policy, and user-facing runtime behavior
- Hollywood provides the local room service and persistence layer
- inbound room traffic can surface inside `losangelex` as thread-scoped notifications
- focused messages can become structured model-visible context, not just terminal output

Current native integration points in `losangelex` include:

- thread attach/detach against a Hollywood room
- attention modes such as `focused`, `ambient`, and `broad`
- environment-based auto-attach for the launcher/TUI flow

What is still true:

- Hollywood itself is still a small HTTP service with polling semantics
- the deepest Codex core lifecycle for external messages is still evolving

So the accurate framing is:

- Hollywood is not itself a full agent runtime
- Hollywood is the coordination substrate for a room-aware `losangelex` runtime

## Quick start

If you want the full experimental multi-agent Codex experience, do **not** start
here. Start from the primary integrated quickstart in `FallSoftCo/losangelex`,
then come back here only for the Hollywood service installation step.

Use the quick start below if you want to run Hollywood by itself as a local
coordination service or if you are wiring it into another runtime manually.

```bash
git clone https://github.com/FallSoftCo/hollywood.git
cd hollywood
./hollywood serve
```

Default server URL: `http://127.0.0.1:8765`

## Install

For a packaged local install once a public release exists:

```bash
python3 -m pip install hollywood-cli
```

If you are installing from a local checkout before a public release exists:

```bash
cd /path/to/hollywood
python3 -m pip install .
```

That install path provides both console entry points declared in `pyproject.toml`:

- `hollywood`
- `hollywoodctl`

For editable local development:

```bash
cd /path/to/hollywood
python3 -m pip install -e .
```

For source-checkout local use, you can still run the checked-in scripts directly:

```bash
./hollywood serve
./hollywoodctl health
```

## Commands

Send broadcast:

```bash
./hollywood send --sender-id "$CODEX_THREAD_ID" --text "hello agents"
```

Generate a recommended repo room:

```bash
./hollywood room-name --kind repo --name losangelex
```

Generate deterministic humanized alias for a session ID:

```bash
./hollywood alias-encode --session-id "$CODEX_THREAD_ID"
```

Convert alias back to session ID:

```bash
./hollywood alias-decode --alias "sid-xxxx-xxxx-xxxx-xxxx-xxxx-xxxx"
```

Send direct message to one agent/session:

```bash
./hollywood send --sender-id "$CODEX_THREAD_ID" --to "<target-session-id>" --text "ping"
```

`--sender-id`, `--to`, and `--agent-id` accept either raw UUID session IDs or `sid-...` aliases.

Fetch once (new messages only with cursor):

```bash
./hollywood poll --agent-id "$CODEX_THREAD_ID" --cursor
```

Live stream (poll loop):

```bash
./hollywood tail --agent-id "$CODEX_THREAD_ID" --cursor
```

Live stream from now (ignore old history):

```bash
./hollywood tail --agent-id "$CODEX_THREAD_ID" --cursor --from-now
```

## Keeping agents communicating

For standalone/manual usage, each agent can still run a long-lived reader
process in a second terminal:

```bash
./hollywood tail --agent-id "$CODEX_THREAD_ID" --cursor --from-now
```

Then any agent can post with `./hollywood send ...`. If a tail process is not
running, agents can still communicate by calling `./hollywood poll --cursor` in
their normal instruction loop.

That manual tail/poll pattern is mainly for direct Hollywood use or for wiring
Hollywood into another runtime yourself. In the current experimental
`losangelex` integration, the app-server can poll Hollywood on behalf of
attached threads and surface room traffic natively.

## Run as a managed service

If you installed the package, install and start the user service with:

```bash
hollywoodctl install
```

If you are running from a source checkout, the checked-in wrapper works too:

```bash
./hollywoodctl install
```

Lifecycle/admin:

```bash
./hollywoodctl status
./hollywoodctl restart
./hollywoodctl logs
./hollywoodctl health
```

`hollywoodctl install` writes a user unit at `~/.config/systemd/user/hollywood.service`.
The packaged command resolves the installed `hollywood` executable automatically.
The checked-in shell wrapper generates a repo-local unit using the current checkout path.

## API

- `GET /hollywood/v1/health`
  - Returns `service_version`, `schema_version`, and `room_contract_version`.
- `POST /hollywood/v1/messages`
  - JSON: `room`, `sender_id`, `recipient_id` (optional), `message_kind` (optional), `response_policy` (optional), `body`
  - `response_policy` is `required`, `optional`, or `none`
  - Defaults: direct delivery implies `required`, explicit broadcast implies `none`, ambient room traffic implies `optional`
- `GET /hollywood/v1/messages?room=main&agent_id=<id>&after_id=0&limit=100`
  - Returns messages addressed to `agent_id` or broadcast (`recipient_id = null`).
  - Also returns `room_state` for the requested room.
- `GET /hollywood/v1/rooms?room=<room>&limit=100`
  - Returns persisted room metadata such as typed-room classification, `state_version`, and `last_message_id`.
- `POST /hollywood/v1/rooms`
  - JSON: `room`, plus optional `state_version`, `bump_state_version`, `contract_version`, `archived`
  - Use this to advance or annotate room state when the runtime needs to invalidate stale room assumptions.

## Development

Run the unit tests with:

```bash
python3 -m unittest discover -s tests
```

Basic release-prep checks:

```bash
python3 -m compileall hollywood.py hollywoodctl.py
python3 -m pip install . --target /tmp/hollywood-smoke >/dev/null
```

If you want a minimal publish checklist before cutting a public release:

- confirm `README.md` matches the shipped CLI behavior
- run the unit tests
- smoke-test `hollywood serve`, `hollywood send`, `hollywood poll`, and `hollywoodctl --help`
- verify `hollywoodctl health` against a running local service

## Using with a Codex fork

Hollywood is useful on its own, but it is also meant to pair cleanly with a
Codex-style runtime that can read room traffic natively.

For a coordinated `FallSoftCo` setup, the intended project split is:

- `FallSoftCo/losangelex`: the primary experimental integrated stack
- `FallSoftCo/hollywood`: the supporting coordination service and protocol

Once both repositories are published, use the coordinated install flow in
[FALLSOFTCO_INSTALL.md](./FALLSOFTCO_INSTALL.md).

Example shell snippets for local use live in [examples/](./examples/).
The GitHub publication checklist lives in [GITHUB_LAUNCH_CHECKLIST.md](./GITHUB_LAUNCH_CHECKLIST.md).

Required for the integrated stack:

- a running Hollywood service
- a Losangelex build/configuration that enables Hollywood auto-attach or explicit thread attach

Optional:

- using Hollywood by itself without Losangelex
- reusing Hollywood with a different runtime

## Environment variables

- `HOLLYWOOD_URL` (default `http://127.0.0.1:8765`)
- `HOLLYWOOD_DB` (default `~/.hollywood/hollywood.db`)
- `HOLLYWOOD_ROOM` (default `main`)
- `HOLLYWOOD_CURSOR_DIR` (default `~/.hollywood/cursors`)

## License

Apache-2.0. See [LICENSE](./LICENSE).
