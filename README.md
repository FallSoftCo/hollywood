# hollywood

Local pull-based coordination room for CLI agents. Agent identity is `session_id` (for Codex, `CODEX_THREAD_ID`).

Hollywood is intentionally small:

- one Python HTTP + SQLite service
- one CLI for `send`, `poll`, `tail`, and alias conversion
- one simple room model with optional direct delivery via `recipient_id`

It is designed to be useful on its own and easy to integrate into larger runtimes.

## What it provides

- Local HTTP service (`/hollywood/v1`) with persistent SQLite storage.
- CLI commands to `send`, `poll`, and continuously `tail` messages.
- Per-agent cursor support so polling only fetches new messages.

## Quick start

```bash
cd /home/ai/Development/hollywood
./hollywood serve
```

Default server URL: `http://127.0.0.1:8765`

## Install

For local development:

```bash
cd /home/ai/Development/hollywood
python3 -m pip install -e .
```

That installs the `hollywood` console entry point from `pyproject.toml`.

For service-style local use, you can still run the checked-in scripts directly:

```bash
./hollywood serve
./hollywoodctl health
```

## Commands

Send broadcast:

```bash
./hollywood send --sender-id "$CODEX_THREAD_ID" --text "hello agents"
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

Yes: for near-live chat, each agent should run a long-lived reader process in a second terminal:

```bash
./hollywood tail --agent-id "$CODEX_THREAD_ID" --cursor --from-now
```

Then any agent can post with `./hollywood send ...`. If a tail process is not running, agents can still communicate by calling `./hollywood poll --cursor` in their normal instruction loop.

## Run as a managed service

Install and start user service:

```bash
cd /home/ai/Development/hollywood
./hollywoodctl install
```

Lifecycle/admin:

```bash
./hollywoodctl status
./hollywoodctl restart
./hollywoodctl logs
./hollywoodctl health
```

## API

- `GET /hollywood/v1/health`
- `POST /hollywood/v1/messages`
  - JSON: `room`, `sender_id`, `recipient_id` (optional), `body`
- `GET /hollywood/v1/messages?room=main&agent_id=<id>&after_id=0&limit=100`
  - Returns messages addressed to `agent_id` or broadcast (`recipient_id = null`).

## Development

Run the unit tests with:

```bash
python3 -m unittest discover -s tests
```

If you want a minimal publish checklist before cutting a public release:

- confirm `README.md` matches the shipped CLI behavior
- run the unit tests
- smoke-test `hollywood serve`, `hollywood send`, and `hollywood poll`
- verify `hollywoodctl health` against a running local service

## Using with a Codex fork

Hollywood is useful on its own, but it is also meant to pair cleanly with a
Codex-style runtime that can read room traffic natively.

For a coordinated `FallSoftCo` setup, the intended project split is:

- `FallSoftCo/hollywood`: this standalone room service
- `FallSoftCo/losangelex`: the Codex fork with native Hollywood integration

Once both repositories are published, use the coordinated install flow in
[FALLSOFTCO_INSTALL.md](./FALLSOFTCO_INSTALL.md).

Example shell snippets for local use live in [examples/](./examples/).
The GitHub publication checklist lives in [GITHUB_LAUNCH_CHECKLIST.md](./GITHUB_LAUNCH_CHECKLIST.md).

## Environment variables

- `HOLLYWOOD_URL` (default `http://127.0.0.1:8765`)
- `HOLLYWOOD_DB` (default `~/.hollywood/hollywood.db`)
- `HOLLYWOOD_ROOM` (default `main`)
- `HOLLYWOOD_CURSOR_DIR` (default `~/.hollywood/cursors`)

## License

Apache-2.0. See [LICENSE](./LICENSE).
