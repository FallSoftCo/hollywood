"""Exclusive file ownership may authorize its own Work's ordinary native edit.

A proposed/ready coordinated target is still governed by its explicit plan.
Claim ownership and one-shot physical mutation are distinct records.
"""
from .edit_plan_scope import need, now
from .store import json_value


def snapshot(row):
    return json_value({"claimId": row["id"], "claimRevision": row["revision"],
        "ownerId": row["owner"], "workId": row["work"], "relativePath": row["relative_path"],
        "kind": row["kind"], "contentSha256": row["content_sha256"],
        "sourceRevision": row["source_revision"], "originDeliveryId": row["origin_delivery"],
        "expiresAt": row["expires_at"], "state": row["state"]})


def authorize(c, namespace, paths, source, authority, files):
    owned = {}
    expected = {item["relativePath"]: item["beforeSha256"] for item in files}
    current_time = now(c)
    for path in paths:
        rows = c.execute("""SELECT * FROM hb_path_claim WHERE authority=%s AND host=%s
            AND workspace_id=%s AND state<>'released' AND (relative_path=%s
            OR (kind='directory' AND left(%s,length(relative_path)+1)=relative_path||'/')
            OR left(relative_path,length(%s)+1)=%s||'/') ORDER BY id""",
            (*namespace, path, path, path, path)).fetchall()
        for row in rows:
            need(authority is not None and source is not None
                 and str(row["owner"]) == source["agentId"]
                 and str(row["work"]) == authority["workId"]
                 and row["kind"] == "file" and row["relative_path"] == path
                 and row["state"] == "active" and row["expires_at"] > current_time,
                 "scope_busy", "Foreign, held, expired or directory ownership needs explicit coordinated resolution")
            need(row["content_sha256"] == expected[path], "claim_content_changed",
                 "Owned file claim must match the actual patch preimage")
            owned[str(row["id"])] = snapshot(row)
    return [owned[key] for key in sorted(owned)]


def pinned(permit, owned):
    need(permit["proof"].get("ownershipClaims", []) == owned, "claim_changed",
         "Original file ownership changed; retain the original permit")


def advance(c, permit, outcome):
    """Record the owned file's new bytes; this grants no new execution."""
    if outcome != "applied":
        return []
    changes = []
    files = {item["relativePath"]: item for item in permit["proof"]["files"]}
    for original in permit["proof"].get("ownershipClaims", []):
        row = c.execute("SELECT * FROM hb_path_claim WHERE id=%s FOR UPDATE",
                        (original["claimId"],)).fetchone()
        need(row is not None and snapshot(row) == original, "claim_changed",
             "Original owned claim changed before reviewed settlement")
        file = files[original["relativePath"]]
        updated = c.execute("""UPDATE hb_path_claim SET content_sha256=%s,revision=revision+1
            WHERE id=%s AND revision=%s RETURNING *""",
            (file["afterSha256"], original["claimId"], original["claimRevision"])).fetchone()
        need(updated is not None, "claim_changed", "Original ownership CAS changed")
        changes.append({"before": original, "after": snapshot(updated)})
    return changes
