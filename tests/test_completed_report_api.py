"""Wire/path regressions, distinct from PostgreSQL and real-native qualification."""

import unittest
from uuid import uuid4

from aiohttp.test_utils import TestClient, TestServer

from hollywood_backbone import native_effects
from hollywood_backbone.report_reconciliation import evidence_paths
from hollywood_backbone.server import WorkReportReconcile, application
from hollywood_backbone.store import Denied


def report():
    return {"operationId": "wire-reconcile", "revision": 2,
            "priorDeliveryId": str(uuid4()), "state": "submitted",
            "effectDisposition": "reviewed", "note": "Original effects inspected.",
            "evidence": [{"hostId": str(uuid4()), "workspaceId": str(uuid4()),
                          "relativePath": "result.json", "sha256": "a" * 64, "size": 12}]}


class PathBoundary(unittest.TestCase):
    def test_workspace_escape_and_ambiguous_paths(self):
        for path in ("", ".", "../out", "a/../out", "/tmp/out", "a\\out", "a//out",
                     "./out", "a/./out", "out\x00suffix"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                evidence_paths([{"relativePath": path}])
        with self.assertRaises(ValueError):
            evidence_paths([{"relativePath": "out"}, {"relativePath": "out"}])
        evidence_paths([{"relativePath": "nested/valid.json"}])

    def test_effect_attestation_and_no_original_live_claim_fields(self):
        value = report()
        WorkReportReconcile.model_validate(value)
        for field in ("effectDisposition", "priorDeliveryId"):
            bad = dict(value)
            del bad[field]
            with self.assertRaises(ValueError):
                WorkReportReconcile.model_validate(bad)
        for field in ("claimId", "generation", "currentDeliveryId", "contextId"):
            with self.assertRaises(ValueError):
                WorkReportReconcile.model_validate({**value, field: "caller hint"})
        with self.assertRaises(ValueError):
            WorkReportReconcile.model_validate({**value, "note": "   "})

    def test_internal_call_cannot_invent_current_native_participation(self):
        with self.assertRaises(Denied):
            native_effects.current_participation(None, {"id": str(uuid4())})


class WireStore:
    """Captures route forwarding only; no store/business/SQL success claimed."""
    def __init__(self):
        self.actor_id = str(uuid4())
        self.calls = []

    def authenticate(self, token):
        if token != "synthetic-wire-fixture":
            raise Denied("Bad fixture credential")
        return {"id": self.actor_id}

    def workflow(self, actor, action, identity, operation, payload, *, wire=None):
        self.calls.append((actor, action, identity, operation, payload, wire))
        return {"wireFixtureOnly": True, "action": action}


class WireHTTP(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = WireStore()
        self.client = TestClient(TestServer(application(self.store)))
        await self.client.start_server()
        self.path = "/hollywood/v3/work/" + str(uuid4()) + "/report-reconcile"
        self.headers = {"Authorization": "Bearer synthetic-wire-fixture"}

    async def asyncTearDown(self):
        await self.client.close()

    async def test_explicit_route_and_wire_are_preserved(self):
        value = report()
        response = await self.client.post(self.path, json=value, headers=self.headers)
        self.assertEqual(response.status, 200)
        _, action, identity, operation, payload, wire = self.store.calls[0]
        self.assertEqual(action, "work.report_reconcile")
        self.assertEqual(operation, value["operationId"])
        self.assertEqual(payload["priorDeliveryId"], value["priorDeliveryId"])
        self.assertEqual(wire["body"], value)
        self.assertEqual(wire["path"], f"work/{identity}/report-reconcile")

    async def test_paths_rejected_before_domain_mutation(self):
        value = report()
        value["evidence"][0]["relativePath"] = "../outside"
        response = await self.client.post(self.path, json=value, headers=self.headers)
        self.assertEqual(response.status, 400)
        self.assertEqual(self.store.calls, [])

    async def test_ordinary_report_does_not_accept_reconciliation_shape(self):
        response = await self.client.post(self.path.removesuffix("-reconcile"),
                                          json=report(), headers=self.headers)
        self.assertEqual(response.status, 400)
        self.assertEqual(self.store.calls, [])

    async def test_authentication_and_spoofed_transport_fields(self):
        response = await self.client.post(self.path, json=report())
        self.assertEqual(response.status, 403)
        response = await self.client.post(self.path, json={**report(), "currentClaimId": "fake"},
                                          headers=self.headers)
        self.assertEqual(response.status, 400)
        self.assertEqual(self.store.calls, [])


if __name__ == "__main__":
    unittest.main()
