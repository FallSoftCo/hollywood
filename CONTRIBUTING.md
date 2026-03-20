# Contributing

Hollywood is intentionally small. Contributions should preserve that property.

## Development

Run the local checks before sending changes:

```bash
cd /home/ai/Development/hollywood
python3 -m unittest discover -s tests
python3 -m compileall hollywood.py
```

## Design constraints

- Keep the service easy to run locally.
- Prefer the standard library unless a dependency materially improves the project.
- Preserve the room model and simple HTTP surface unless there is a strong compatibility reason.
- Keep CLI behavior documented in `README.md`.

## Release expectations

Before tagging a release:

- update `CHANGELOG.md`
- verify the README examples still work
- run the tests
- smoke-test `serve`, `send`, and `poll`
