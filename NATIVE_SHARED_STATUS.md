# Shared native context status (additive candidate)

`nativeState.capabilities.sharedThreadStatus=1` permits a source-less status:

```
{"type":"activeElsewhere","activeFlags":[]}
```

It attests that this member's shared native context is active but not attributable
to the current room. It does not identify another room/source, disclose waiting
flags from a private conversation, claim current room execution or completion,
or admit/wake work. An explicit source or nonempty flags are rejected. Existing
agent authentication, room post permission, exact revision/fingerprint CAS,
privacy of source-bound observations and75-second expiry remain unchanged.

This is an additive source contract, not an update to frozen Hollywood177 bytes.
The connector negotiates ONLY from a current authenticated catalog, discards
persisted capabilities on reconnect and retains the legacy fallback on old APIs.
The companion NextComp candidate renders **Shared context busy**, distinct from
room Running, Idle and Agent unavailable. Coordinate compatible client delivery
before activating a server advertising this capability. No client package,
version, signature, phone installation or live API is changed by this source.

Per-input response outcomes are a separate axis: an accepted answer is not task
completion, and native turn end may leave an explicit source-addressing hold.
The connector's owner-private journal/read tools expose those receipts without
publishing ambiguous text or private source metadata to room viewers.
