# Original administrative acceptance

Schema 14 adds a protected administrative command journal for principal enrollment,
room creation, role changes, room membership and host placement. Each primitive
commits its original accepted result in the same transaction as the mutation.
Same actor/operation submissions serialize before lookup; changed method, route or
wire digest conflicts. Matching replay returns the original result even if the
target has since changed. This is not an exactly-once execution claim.

POST/PUT inputs accept operationId. Transitional callers without it retain the
prior API behavior and do not gain an original administrative receipt. New native
LAX administration supplies a stable child operation under its original callback.
No old receipt is fabricated from a matching current name, ID or revision.

GET /hollywood/v3/administration/commands/{operationId} requires a currently active
administrator and selects only that issuer's command within its authority. The
response identifies authority, actor, operation, method, path, exact wire digest,
original accepted result and acceptance time. It excludes request bodies, bearer
headers and enrollment credentials. Revoked administrative permission prevents
readback. Failed mutations leave no accepted receipt.

The transaction wrapper reuses the existing connection-aware administrative
primitives; it never performs host setup, launches a model or assumes a shared
filesystem. Linux setup acceptance is owned by the separate protected LAX host
provisioner.

Actual qualification: tests/integration_administrative_receipts.py runs the
authenticated PostgreSQL HTTP API with concurrent duplicate operations, conflicts,
issuer isolation, mutable role/grant changes, placement, rejected escalation,
restart and current administrative revocation.
