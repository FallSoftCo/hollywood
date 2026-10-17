"""Real isolated PG/authenticated HTTP; native lifecycle metadata is synthetic."""
import asyncio
import copy
import hashlib
import json

from report_scope_fixture import Fixture, uid
from hollywood_backbone.artifact_roots import normalize
from hollywood_backbone.server import ArtifactEvidence
from hollywood_backbone.store import digest


async def main():
    f = Fixture()
    await f.start()
    a, b = f.agents["worker"], f.agents["peer"]
    checks = []
    try:
        physical = f.out / "shared-project"
        physical.mkdir()
        artifact = physical / "result.json"
        artifact.write_text(json.dumps({"projectCounter": 42}) + chr(10))
        resource_id = uid()
        target = {
            "schema": "hollywood-artifact-root/v1", "workspaceId": resource_id,
            "workspaceRevision": 1, "grantRevision": 1,
            "environmentSha256": digest({"host": f.ids["host"], "environment": "local"}),
            "rootIdentitySha256": digest({"device": physical.stat().st_dev, "inode": physical.stat().st_ino}),
        }
        await f.api("owner", "PUT", "hosts/" + f.ids["host"] + "/physical-workspaces/" + resource_id, {
            "operationId": uid(), "revision": 0, "state": "active", "rootPath": str(physical),
            "environmentSha256": target["environmentSha256"],
            "rootIdentitySha256": target["rootIdentitySha256"], "registrationSha256": digest("fixture")})
        for agent in (a, b):
            await f.api("owner", "PUT", "physical-workspaces/" + resource_id + "/agent-grants/" + agent["id"], {
                "operationId": uid(), "revision": 0, "workspaceRevision": 1,
                "executionHost": f.ids["host"], "state": "active", "canRead": True, "canWrite": False})
        def evidence(agent, root=target):
            return [{"hostId": f.ids["host"], "workspaceId": agent["workspace"],
                     "artifactRoot": copy.deepcopy(root), "relativePath": "result.json",
                     "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(), "size": artifact.stat().st_size}]
        original = copy.deepcopy(a["evidence"])
        old = [ArtifactEvidence.model_validate(e).model_dump(mode="json", by_alias=True) for e in original]
        normalize(old)
        assert old == original
        checks.append("Absent artifactRoot preserves older evidence canonical shape")
        private = f.out / "workspaces" / a["workspace"] / "result.json"
        assert private.read_bytes() != artifact.read_bytes()

        # Fixture-only current execution: use the original assigned native source.
        f.sql("UPDATE hb_delivery SET state='completed' WHERE id=%s", (a["source"]["deliveryId"],))
        f.sql("UPDATE hb_executor SET state='stopped' WHERE delivery=%s", (a["source"]["deliveryId"],))
        a["source"] = a["prior_source"]
        f.sql("UPDATE hb_delivery SET state='running',result_event=NULL WHERE id=%s", (a["prior"],))
        f.sql("UPDATE hb_executor SET state='live' WHERE delivery=%s", (a["prior"],))
        body = {"operationId": uid(), "revision": 2, "claimId": a["source"]["claimId"], "generation": 1,
                "state": "submitted", "note": "SYNTHETIC physical project evidence; review separate",
                "evidence": evidence(a), "briefingSequence": 0}
        async def ordinary(payload, status=200):
            return await f.api(a, "POST", "work/" + a["work"] + "/report", payload,
                               f.source(a, "hollywood_work_report"), status)
        bad = copy.deepcopy(body);bad["evidence"][0]["artifactRoot"]["workspaceRevision"] = 2
        await ordinary(bad, 409)
        bad = copy.deepcopy(body);bad["evidence"][0]["artifactRoot"]["workspaceId"] = uid()
        await ordinary(bad, 403)
        bad = copy.deepcopy(body);bad["evidence"][0]["workspaceId"] = uid()
        await ordinary(bad, 403)
        await ordinary(body)
        reported = f.sql("SELECT state,evidence FROM hb_work WHERE id=%s", (a["work"],))
        assert reported["state"] == "submitted" and reported["evidence"] == evidence(a)
        assert reported["evidence"][0]["sha256"] != hashlib.sha256(private.read_bytes()).hexdigest()
        checks.append("Current assigned report retains context identity but names and stores the separate physical project; stale/unowned/context-swapped roots denied")

        read = await f.read(b)
        proof = read["scopeRead"]
        await f.api("owner", "PUT", "physical-workspaces/" + resource_id + "/agent-grants/" + b["id"], {
            "operationId": uid(), "revision": 1, "workspaceRevision": 1,
            "executionHost": f.ids["host"], "state": "revoked", "canRead": False, "canWrite": False})
        revoked = copy.deepcopy(target);revoked["grantRevision"] = 2
        await f.report(b, f.report_body(b, proof, evidence=evidence(b, revoked)), status=403)
        await f.api("owner", "PUT", "physical-workspaces/" + resource_id + "/agent-grants/" + b["id"], {
            "operationId": uid(), "revision": 2, "workspaceRevision": 1,
            "executionHost": f.ids["host"], "state": "active", "canRead": True, "canWrite": False})
        await f.report(b, f.report_body(b, proof, evidence=evidence(b)), status=409)
        current = copy.deepcopy(target);current["grantRevision"] = 3
        await f.report(b, f.report_body(b, proof, evidence=evidence(b, current)))
        report = f.sql("SELECT state,evidence FROM hb_work WHERE id=%s", (b["work"],))
        assert report["state"] == "submitted" and report["evidence"] == evidence(b, current)
        assert f.sql("SELECT state FROM hb_delivery WHERE id=%s", (b["prior"],))["state"] == "completed"
        assert f.sql("SELECT state FROM hb_delivery WHERE id=%s", (b["source"]["deliveryId"],))["state"] == "running"
        assert json.loads(private.read_text()) == {"counter": 1}
        assert json.loads(artifact.read_text()) == {"projectCounter": 42}
        checks.append("Completed original report reauthorizes current read grant, rejects revoked/stale grants, preserves original/current execution and distinct files")
        result = {"state": "passed", "checks": checks, "httpCalls": len(f.trace),
                  "nativeLifecycle": "synthetic", "actualPostgreSQL": True, "actualAuthenticatedHTTP": True,
                  "actualFilesystemArtifacts": True, "modelsStarted": 0, "productionChanged": False}
        (f.out / "artifact-resources-passed.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    finally:
        await f.close()


if __name__ == "__main__":
    asyncio.run(main())
