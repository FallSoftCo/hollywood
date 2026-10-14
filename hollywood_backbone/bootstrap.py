"""Repeatable local authority bootstrap with persisted intent before database effects."""

import argparse
import json
import os
import secrets
from pathlib import Path

from .store import Store, uid


def initialize(config, state, name):
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state, 0o700)
    intent = state / "installation-intent.json"
    if not intent.exists():
        plan = {
            "authorityId": uid(),
            "globalRoomId": uid(),
            "name": name,
            "credentials": {
                label: {"principalId": uid(), "token": secrets.token_urlsafe(40)}
                for label in ("Owner", "Global")
            },
        }
        # Exclusive creation and fsync precede any database mutation. A lost reply or
        # crash can reconcile the same identities and credentials on the next run.
        with intent.open("x") as f:
            os.chmod(intent, 0o600)
            json.dump(plan, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        fd = os.open(state, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    plan = json.loads(intent.read_text())
    if plan["name"] != name:
        raise ValueError("Installation name changed under existing intent")
    store = Store(config)
    try:
        store.migrate()
        result = store.bootstrap(plan["authorityId"], name, plan)
        result["credentialsFile"] = str(intent)
        receipt = state / "installation-receipt.json"
        receipt.write_text(json.dumps(result, indent=2))
        os.chmod(receipt, 0o600)
        return result
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    print(
        json.dumps(initialize(json.loads(args.database_config.read_text()), args.state, args.name))
    )


if __name__ == "__main__":
    main()
