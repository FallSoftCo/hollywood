# Publishing Hollywood

This directory is now close to a standalone OSS project, but it is not fully
published yet. Use this checklist to turn it into a public release.

## 1. Repository bootstrap

- Initialize a git repository in this directory.
- Choose the canonical remote and push the initial history.
- Tag the first release after smoke tests pass.

Suggested bootstrap:

```bash
cd /home/ai/Development/hollywood
git init
git add .
git commit -m "Initial Hollywood standalone release prep"
```

## 2. Packaging

The project already includes:

- `pyproject.toml` for Python packaging
- console entry point: `hollywood`
- local editable install support via `python3 -m pip install -e .`

Before public release:

- verify the package name you want on PyPI
- decide whether to keep `hollywood-cli` or rename it
- test installation in a clean virtual environment

Example smoke test:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e .
hollywood --help
```

## 3. Quality bar

Run these before cutting a release:

```bash
cd /home/ai/Development/hollywood
python3 -m unittest discover -s tests
python3 -m compileall hollywood.py
python3 hollywood.py --help
```

Then do a manual end-to-end smoke test:

```bash
./hollywood serve
./hollywood send --sender-id 019d0cee-31b5-7133-843c-10d1c562e157 --text "hello"
./hollywood poll --agent-id 019d0cee-31b5-7133-843c-10d1c562e157 --include-own
```

## 4. Public OSS readiness

Minimum metadata already present:

- `README.md`
- `LICENSE`
- `CHANGELOG.md`
- `.gitignore`
- tests

Still recommended before announcing broadly:

- add screenshots or a short transcript to the README
- document SQLite backup/reset and cursor-file cleanup
- publish a Docker image if you want easier service deployment
- document compatibility expectations across Linux/macOS
- add CI for unit tests

## 5. Positioning

Hollywood should be presented as:

- a tiny local coordination substrate for CLI agents
- transport-agnostic at the concept level, but with a simple HTTP reference implementation
- useful on its own, not only as a Codex integration
- a supporting repository when released alongside an integrated LosangElex fork

Do not frame it as a permanent fork-specific feature. The stronger story is:

- standalone room service + protocol
- optional runtime-native integrations in agent systems like Codex

If released together with `FallSoftCo/losangelex`, the primary announcement
should center the integrated stack and describe Hollywood as the reusable
supporting subsystem, not as the whole product by itself.
