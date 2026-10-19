# Room catalog feed summary v1

`Store.catalog` and `GET /hollywood/v3/rooms` add `feedSummary` to each room:

```json
{"version":1,"latestMessage":null,"latestActivity":null,"recency":null}
```

Non-null message fields: `eventId`, `seq`, `occurredAt`, `authorId`,
`authorKind`, `body`, `truncated`. Non-null activity fields: `eventId`, `seq`,
`occurredAt`, `authorId`, `nativeType`, `status`, `title`, `truncated`.
Recency fields: `eventId`, `seq`, `occurredAt`.

Selection is greatest authority event sequence, **not** greatest timestamp.
Message eligibility is human/agent authorship and kind `message`/`request`,
excluding data types `executionActivity`, `peerCompletion`,
`collaborationProtocol`, `reportScopeProtocol`. Principal kind is authoritative;
a service cannot spoof human authorship through event data. Activity eligibility
is data type `executionActivity`. Recency is the higher-sequence selected event.
Absent categories, including recency for an empty feed, are explicit JSON nulls.

Authority, room membership (`can_read`) and original direct audience are applied
before selecting either latest event. Direct events are visible to author and
recipient; authority-local administrators intentionally see all room/direct
events. Newer invisible events cannot mask visible older events, nor contribute
their IDs, timestamps, sequence or text to a viewer's summary.

`occurredAt` is `floor(epoch(event.created_at) * 1000)` as a JSON integer.
It preserves the canonical event's timestamp, independently of sequence order,
room revision, rename, delivery state, protocol bookkeeping, or count
`observedAt`. Event immutability is unchanged. Clients must not interpret
`activity.observedAt` as last activity time.

Body/title are capped at 320 Unicode code points (PostgreSQL UTF-8 `left` and
`char_length`, not bytes or grapheme clusters), without ellipsis. `truncated`
indicates that the original value exceeded the cap; exactly 320 is not truncated.
Only named fields are constructed. Command, output, cwd, arbitrary data,
protocol receipts, delivery/native IDs and execution plans are not copied.
Legacy malformed/missing activity string fields become null rather than
stringifying arbitrary objects; an absent/non-string title is not truncated.
This is a field projection, not semantic redaction of an author's chosen title.

The existing room-name order, 100-room page, count aggregation/visibility and
defaults remain unchanged. No schema or server-route changes are required.
The catalog remains one batched SQL statement after fixed actor checks, using
the existing room/sequence index and `DISTINCT ON` after visibility filtering.
Database round trips are independent of the returned room count; this does not
promise constant database work for arbitrarily long histories. No per-room
history, HTTP, activity projection or protocol-receipt queries are performed.

## Targeted qualification

`tests/integration_catalog_feed_summary.py` uses the existing QA database with
synthetic fixtures in one forced-rollback transaction. No migration, new
listener, provider call or agent admission occurs. It executes the candidate
SQL and authenticated aiohttp route/middleware in process, compares legacy
fields against the enrolled Git baseline on identical fixtures, and measures
SQL calls for zero/one/100 rooms. This tests database and route behavior, not
deployed service behavior, native model behavior or load performance.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
 /opt/lax-three-context-20261018-r01/venv-r120/bin/python \
 tests/integration_catalog_feed_summary.py \
 --database-config /var/lib/lax-three-context-20261018-r01/database.json
```

Database configuration is read privately and never logged. Use only an
authorized readable QA configuration; do not copy credentials into reports.
In this enrolled environment the root-only config requires the pre-existing,
verified scoped sudo capability; the qualification command actually used is:

```sh
sudo -n /usr/bin/env PYTHONPATH="$PWD" \
 /opt/lax-three-context-20261018-r01/venv-r120/bin/python -B \
 tests/integration_catalog_feed_summary.py \
 --database-config /var/lib/lax-three-context-20261018-r01/database.json
```

Run this only for the reviewed fixture in the isolated source. It does not
authorize permissions, service, role-limit, database schema or default changes.

### Actual qualification and history

Engineer `catalog-attempt-03.log` records exit 0 with all eight assertion groups
executed, authenticated candidate route checks passing, and Store SQL calls of
4/4/4 for zero/one/100 returned rooms (including actor/fence checks). Rollback
was verified with zero fixture authorities remaining. No new listener or model
call was used. The sole rework correction binds the resolved aiohttp match to
the application; all original behavioral assertions remain intact. The Store
implementation was not reapplied or changed during rework.

Retained failures: attempt 01 could not connect at the QA role limit; attempt 02
reached SQL but failed in manual aiohttp route setup before HTTP checks. The
first canonical engineering episode was blocked, completed and independently
rejected, with all evidence preserved. Fresh canonical rework admission, not a
chat-only override, authorized the correction and attempt 03.

External host-operator assistance raised the shared private QA role's bounded
connection capacity from 12 to 20 (attributed event
`2f56e868-638d-4e6b-b198-edfe1e4521bd`), without reported permission, credential,
schema or global-setting changes/restarts. This delivery was **not wholly
unattended**. The engineer did not change role limits or production defaults.
Successful engineering checks do not replace independent canonical review.
