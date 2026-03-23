# Publishing Hollywood

This directory is now close to a standalone OSS project, but it is not fully
published yet. Use this checklist to turn it into a public release.

## 1. Repository bootstrap

- Choose the canonical remote and push the initial history.
- Tag the first release after smoke tests pass.

If you are starting from a plain working tree without git metadata yet:

```bash
git init
git remote add origin git@github.com:FallSoftCo/hollywood.git
git add .
git commit -m "Initial Hollywood standalone release prep"
git branch -M main
git push -u origin main
```

## 2. Packaging

The project already includes:

- `pyproject.toml` for Python packaging
- console entry points: `hollywood` and `hollywoodctl`
- local install support via `python3 -m pip install .`
- editable install support via `python3 -m pip install -e .`

Before public release:

- verify the package name you want on PyPI
- verify the project metadata and URLs match the publishing organization
- test installation in a clean virtual environment

Example smoke test:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install .
hollywood --help
hollywoodctl --help
```

## 3. Quality bar

Run these before cutting a release:

```bash
python3 -m unittest discover -s tests
python3 -m compileall hollywood.py hollywoodctl.py
python3 hollywood.py --help
python3 hollywoodctl.py --help
```

Then do a manual end-to-end smoke test:

```bash
./hollywood serve
./hollywood send --sender-id 019d0cee-31b5-7133-843c-10d1c562e157 --text "hello"
./hollywood poll --agent-id 019d0cee-31b5-7133-843c-10d1c562e157 --include-own
./hollywoodctl health
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
- verify `hollywoodctl install` from both packaged and source-checkout flows

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
