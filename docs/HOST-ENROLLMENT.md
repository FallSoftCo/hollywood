# Host-local credential enrollment

An authorized human administrator can enroll a principal through the existing
`POST /hollywood/v3/principals` operation using exactly one of
`enrollmentCredential` (legacy) or `enrollmentCredentialDigest` (new).
The digest is the existing canonical `store.digest(secret)`, not raw SHA-256.
The host generates and retains its secret locally. Only its digest and public
identity metadata need to reach the administrator. The digest is not a usable
Bearer credential. Existing revocations and conflicting identities remain
conflicts; enrollment does not rotate or restore credentials.

Explicit `administrator: true` is allowed only when an authenticated human
administrator creates a new **agent** principal. Agent administrators cannot
mint further administrators. Omission preserves an existing principal's
privileges; an explicit different privilege is rejected, never silently applied.
This is an explicit provisioning operation, not model input or worker activation.

No endpoint, database migration, model request or service activation is added.
Existing protected administrative operation receipts and legacy enrollment stay
compatible. Enrolling a principal alone does not grant room membership, host
placement, filesystem access or execution enrollment.

`tests/integration_principal_enrollment.py PRIVATE_QA_ROOT` exercises the actual
HTTP handlers and PostgreSQL transactions. It requires the named isolated QA
installation; every fixture is enclosed in an explicit outer rollback.
