"""Explicit same-Work human prerequisites; no model polling or new task ledger."""
from uuid import UUID

from .store import Conflict, Denied


def outstanding(c, agent, value):
    ids = value.get("humanPrerequisites", [])
    if not ids:
        return []
    rows = c.execute("""SELECT id,state FROM hb_attention
        WHERE agent=%s AND id=ANY(%s::uuid[])""", (agent, ids)).fetchall()
    states = {str(row["id"]): row["state"] for row in rows}
    # A lost/unknown question is not a supplied human answer. Expiry means the
    # prerequisite ended, never that permission or a successful answer exists.
    return [identity for identity in ids
            if states.get(identity) not in ("closed", "expired")]


def wait(c, store, actor, binding, p):
    from .context_system import item
    from .context_peers import require_current_read, own_change
    v = p["value"]
    if set(v) != {"workId", "attentionIds"}:
        raise ValueError("Exact original Work and human question identities required")
    ids = v["attentionIds"]
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 8
            or any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids)):
        raise ValueError("One to eight distinct human questions required")
    ids = [str(UUID(x)) for x in ids]
    work = item(c, store, actor, v["workId"], "work")
    require_current_read(binding, work, p["nativeTurn"])
    d = c.execute("""SELECT * FROM hb_delivery
        WHERE agent=%s AND event=%s""", (actor["id"], work["source"])).fetchone()
    if (work["state"] not in ("pending", "running") or not d
            or d["state"] not in ("claimed", "running") or d["cancellation_requested"]):
        raise Conflict("Original responsibility no longer admits a human wait")
    combined = list(dict.fromkeys(work["value"].get("humanPrerequisites", []) + ids))
    if len(combined) > 8:
        raise ValueError("Responsibility human prerequisite limit exceeded")
    from .human_continuations import QUESTION_METHODS, context
    for identity in combined:
        row = c.execute("SELECT * FROM hb_attention WHERE id=%s", (identity,)).fetchone()
        if (not row or row["agent"] != actor["id"] or row["delivery"] != d["id"]
                or row["generation"] != d["generation"]
                or row["method"] not in QUESTION_METHODS
                or row["request"].get("isBlocking") is not False):
            raise Denied("Human prerequisite belongs to different original work")
        context(c, row)
    value = {**work["value"], "humanPrerequisites": combined}
    pending = outstanding(c, actor["id"], value)
    if pending:
        own_change(c, actor, binding, work, "waiting", value, p["nativeTurn"])
    return {"workId": str(work["id"]), "state": "waiting" if pending else "results_available",
            "attentionIds": combined, "pendingAttentionIds": pending,
            "instruction": ("End this inference. The original Work stays unfinished; only "
                "interpreted human answers resolve these prerequisites. Other Work may execute."
                if pending else "Read current results; expired questions are not human approval.")}
