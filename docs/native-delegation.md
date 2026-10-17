# Native execution delegation

A request's `sourceEventId` links conversation. It can name a message or result.
The native execution that delegated the work is a separate, canonical relation.

Hollywood commits `hb_native_delegation` with a newly accepted native agent
request, referring to the original native-effect audit row and parent attempt.
Idempotent command recovery returns the original child and never changes that
parent. Explicit external communication transports have no native parent.

`GET /hollywood/v3/deliveries/{childDeliveryId}/delegation` returns
`hollywood-native-delegation/v1`: child request/delivery, conversation source,
parent authority/agent/host/delivery/generation/claim/request/context/role,
native call, operation and proof identities. Only the child author or an
administrator can read it, and current read access to both events is required.
No parent message body is returned. The parent execution binding is validated
historically; it need not still run. Current cancellation and outcome policy
remain the connector's responsibility. The relation grants no execution or
human authority.

Schema 30 is additive. Its one-time conservative legacy backfill requires the
immutable command, event and native-effect rows to retain the same original
PostgreSQL transaction identity, with one matching original native effect.
It stores the relation to that audit row; transaction IDs are never retained as
durable identities. Updated, frozen or ambiguous legacy evidence remains
unavailable. Missing proof is a conflict, never permission to repost a child.
Rollback leaves the additive table; old readers and original receipts remain
unchanged.

Qualification: `tests/integration_native_delegation.py` exercises actual
private PostgreSQL and authenticated HTTP, including result-based conversation
sources, private parent isolation, original receipt recovery, historical
bindings and conservative migration. Its native lifecycle rows are synthetic.
Actual managed-model continuation remains a separate acceptance gate.
