"""Account-wide earned-credit redemption, independent of conversation lifetime."""

import asyncio
import hashlib
import json
import math
import time
import uuid

from .store import Conflict


class Limits:
    def __init__(self, runtime, clock=time.time):
        self.runtime, self.clock = runtime, clock
        self.lock = asyncio.Lock()
        self.snapshot = None
        self.generation = 0
        runtime.store.db.executescript("""
            CREATE TABLE IF NOT EXISTS limit_attempt (
                id TEXT PRIMARY KEY, account TEXT NOT NULL, origin TEXT NOT NULL,
                outcome TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                next_at REAL NOT NULL DEFAULT 0, result TEXT
            );
            CREATE TABLE IF NOT EXISTS limit_auto_gate (
                account TEXT PRIMARY KEY, armed INTEGER NOT NULL
            );
        """)

    def identity(self, account, expected_id=None):
        # Never expose credentials. Missing file means identity cannot be pinned safely.
        path = self.runtime.rpc.home / "auth.json"
        try:
            credentials = json.loads(path.read_text())
            account_id = (credentials.get("tokens") or {}).get("account_id")
        except (OSError, ValueError, AttributeError):
            return None
        if not isinstance(account_id, str) or not account_id:
            return None
        if expected_id is not None and expected_id != account_id:
            return None
        return hashlib.sha256(json.dumps([account_id, (account or {}).get("email")]).encode()).hexdigest()

    async def read(self):
        started = self.clock()
        generation = self.generation
        account = (await self.runtime.rpc.call("account/read", {"refreshToken": False})).get("account")
        fingerprint = self.identity(account)
        if not account or account.get("type") != "chatgpt" or not fingerprint:
            raise ValueError("Reset requires a verifiable ChatGPT account")
        rates = await self.runtime.rpc.call("account/rateLimits/read", {})
        after = (await self.runtime.rpc.call("account/read", {"refreshToken": False})).get("account")
        if fingerprint != self.identity(after, rates.get("accountId")) or generation != self.generation:
            raise Conflict("Account changed while reading usage; refresh again")
        if self.clock() - started > 30:
            raise Conflict("Usage observation expired while refreshing; try again")
        windows = [rates.get("rateLimits", {}).get(name) for name in ("primary", "secondary")]
        for window in windows:
            if window is None:
                continue
            used = window.get("usedPercent")
            if type(used) not in (int, float) or not math.isfinite(used) or not 0 <= used <= 100:
                raise ValueError("Invalid usage percentage; refusing reset")
        remaining = [100 - w["usedPercent"] for w in windows if w]
        credits = rates.get("rateLimitResetCredits") or {}
        self.snapshot = {"scope": "account", "accountKey": fingerprint,
                         "observedAt": self.clock(), "remainingPercent": min(remaining) if remaining else None,
                         "availableCredits": credits.get("availableCount"), "rates": rates}
        return self.snapshot

    async def reset(self, command_id, *, automatic=False, expected_account_key=None):
        if not isinstance(command_id, str) or not 1 <= len(command_id) <= 128:
            raise ValueError("A bounded commandId is required")
        async with self.lock:
            current = await self.read()
            if expected_account_key != current["accountKey"]:
                raise Conflict("Account differs from the displayed eligibility; refresh before reset")
            db = self.runtime.store.db
            rows = self.runtime.store.rows("SELECT * FROM limit_attempt WHERE id=?", (command_id,))
            if rows:
                attempt = rows[0]
                if attempt["account"] != current["accountKey"]:
                    raise Conflict("Reset attempt belongs to different credentials")
                if attempt["result"]:
                    return {**json.loads(attempt["result"]), "limits": current}
                if attempt["next_at"] > self.clock() or attempt["attempts"] >= 3:
                    raise Conflict("Reset retry is in backoff or needs review")
            else:
                pending = self.runtime.store.rows(
                    "SELECT id FROM limit_attempt WHERE account=? AND outcome='uncertain'",
                    (current["accountKey"],))
                if pending:
                    raise Conflict("An earlier reset is uncertain; retry its original commandId")
                if type(current["availableCredits"]) is not int or current["availableCredits"] <= 0:
                    return {"outcome": "noCredit", "limits": current}
                if automatic and (current["remainingPercent"] is None or current["remainingPercent"] >= 2):
                    return {"outcome": "notNeeded", "limits": current}
                with db:
                    db.execute("INSERT INTO limit_attempt(id,account,origin,outcome) VALUES(?,?,?,'uncertain')",
                               (command_id, current["accountKey"], "coordinator" if automatic else "manual"))
            # Persist before RPC: a timeout may have consumed a credit.
            with db:
                db.execute("UPDATE limit_attempt SET attempts=attempts+1,next_at=? WHERE id=?",
                           (self.clock() + 60, command_id))
            response = await self.runtime.rpc.call("account/rateLimitResetCredit/consume",
                                                   {"idempotencyKey": command_id})
            outcome = response.get("outcome")
            if outcome not in {"reset", "alreadyRedeemed", "nothingToReset", "noCredit"}:
                raise ValueError("Unknown reset outcome; original attempt retained")
            result = {"outcome": outcome, "scope": "account", "commandId": command_id}
            with db:
                db.execute("UPDATE limit_attempt SET outcome=?,result=? WHERE id=?",
                           (outcome, json.dumps(result), command_id))
            # No optimistic 100%: confirmed effect comes only from this fresh read.
            refreshed = await self.read()
            if refreshed["accountKey"] != current["accountKey"]:
                raise Conflict("Account changed after redemption; original receipt retained")
            return {**result, "limits": refreshed}

    async def automatic(self):
        current = await self.read()
        db = self.runtime.store.db
        pending = self.runtime.store.rows(
            "SELECT id,attempts,next_at FROM limit_attempt WHERE account=? AND origin='coordinator' AND outcome='uncertain'",
            (current["accountKey"],))
        if pending:
            attempt = pending[0]
            if attempt["attempts"] < 3 and attempt["next_at"] <= self.clock():
                return await self.reset(attempt["id"], automatic=True,
                                        expected_account_key=current["accountKey"])
            return
        with db:
            db.execute("INSERT OR IGNORE INTO limit_auto_gate VALUES(?,1)", (current["accountKey"],))
        if current["remainingPercent"] is None or current["remainingPercent"] >= 2:
            if current["remainingPercent"] is not None and current["rates"].get("ordinaryUsageAllowed") is True:
                with db:
                    db.execute("UPDATE limit_auto_gate SET armed=1 WHERE account=?", (current["accountKey"],))
            return
        if not self.runtime.store.rows("SELECT armed FROM limit_auto_gate WHERE account=?", (current["accountKey"],))[0]["armed"]:
            return
        # One automatic attempt per observed reset-window identity. Even failure/no
        # recovery cannot consume a second credit in a tight or restart-driven loop.
        rates = current["rates"].get("rateLimits", {})
        windows = [rates.get(name, {}) or {} for name in ("primary", "secondary")]
        resets = [w.get("resetsAt") for w in windows]
        triggering = [w for w in windows if w.get("usedPercent", 0) > 98]
        if not triggering or any(type(w.get("resetsAt")) is not int or w["resetsAt"] <= self.clock()
                                 for w in triggering):
            return
        seed = json.dumps([current["accountKey"], resets])
        command = "auto-reset:" + str(uuid.uuid5(uuid.NAMESPACE_URL, seed))
        if self.runtime.store.rows("SELECT id FROM limit_attempt WHERE id=?", (command,)):
            return
        with db:
            db.execute("UPDATE limit_auto_gate SET armed=0 WHERE account=?", (current["accountKey"],))
        return await self.reset(command, automatic=True, expected_account_key=current["accountKey"])
