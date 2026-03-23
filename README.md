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

If you want the full experimental multi-agent Codex experience, do **not** start
here. Start from the primary integrated quickstart in `FallSoftCo/losangelex`,
which should point back to this repository only for the Hollywood service
installation step.

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
- `POST /hollywood/v1/messages`
  - JSON: `room`, `sender_id`, `recipient_id` (optional), `body`
- `GET /hollywood/v1/messages?room=main&agent_id=<id>&after_id=0&limit=100`
  - Returns messages addressed to `agent_id` or broadcast (`recipient_id = null`).

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
- a LosangElex build/configuration that enables Hollywood auto-attach

Optional:

- using Hollywood by itself without LosangElex
- reusing Hollywood with a different runtime

## Environment variables

- `HOLLYWOOD_URL` (default `http://127.0.0.1:8765`)
- `HOLLYWOOD_DB` (default `~/.hollywood/hollywood.db`)
- `HOLLYWOOD_ROOM` (default `main`)
- `HOLLYWOOD_CURSOR_DIR` (default `~/.hollywood/cursors`)

## License

Apache-2.0. See [LICENSE](./LICENSE).
