# FallSoftCo Install Flow

This document describes the intended coordinated install flow once these
projects are published under the `FallSoftCo` GitHub organization.

Important:

- the primary user entrypoint should be `FallSoftCo/losangelex`
- this document exists in the Hollywood repository so the supporting service can
  explain its role in the two-repository setup
- if the two repositories disagree, the integrated LosangElex quickstart should
  win and this document should be updated to match it

## Repositories

- `FallSoftCo/losangelex`
  - primary experimental integrated stack
- `FallSoftCo/hollywood`
  - supporting local coordination room and protocol service

The setup should stay explicitly two-part:

1. start from the LosangElex quickstart
2. install and start Hollywood when that quickstart tells you to
3. return to LosangElex for the runtime flow

## 1. Install Hollywood

Expected supporting-service flow:

```bash
git clone git@github.com:FallSoftCo/hollywood.git
cd hollywood
python3 -m pip install -e .
./hollywoodctl install
./hollywoodctl health
```

Alternative local/dev flow:

```bash
git clone git@github.com:FallSoftCo/hollywood.git
cd hollywood
./hollywood serve
```

## 2. Install LosangElex

Primary integrated flow:

```bash
git clone git@github.com:FallSoftCo/losangelex.git
cd losangelex/codex-rs
cargo build
```

If LosangElex later gets packaged binaries, this section should be updated to
prefer the release artifact path over source builds.

## 3. Connect LosangElex to Hollywood

Export the shared runtime environment before launching the TUI:

```bash
export HOLLYWOOD_AUTO_ATTACH=1
export HOLLYWOOD_URL=http://127.0.0.1:8765
export HOLLYWOOD_ROOM=main
export HOLLYWOOD_ATTENTION_MODE=focused
```

Then start LosangElex:

```bash
cd /path/to/losangelex/codex-rs
cargo run --bin codex
```

## 4. Multi-session use

Start multiple LosangElex sessions in separate terminals with the same
Hollywood environment configuration.

Typical pattern:

- session 1: main coding task
- session 2: code review or docs
- session 3: validation or research

Hollywood provides the shared room; LosangElex decides how to surface and act
on those messages.

Required for this experience:

- both repositories cloned
- Hollywood service running
- LosangElex launched with the documented `HOLLYWOOD_*` environment

Optional:

- using Hollywood without LosangElex
- using LosangElex without Hollywood

## 5. Publish checklist

Before announcing this setup publicly:

- Hollywood repository pushed and tagged
- LosangElex branch cleaned up and validated
- shared install instructions verified from a clean machine
- at least one end-to-end transcript or demo recorded
- explicit note in LosangElex docs that Hollywood is an optional integration

## 6. Current status

At the moment this file is preparatory documentation inside the local Hollywood
repository. It documents the intended `FallSoftCo` release/install flow, but it
does not mean both GitHub repositories are live yet.
