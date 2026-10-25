"""Purpose-owned prepared reports, approved dispatch and original install receipts."""
from uuid import UUID
import json

from .store import Conflict, Denied
from . import context_seed_review as review
from .context_automatic_refill import sha, uint, text


def bind(c, actor, purpose, binding, value):
    version = value.get("manualSeedControl", 0)
    if type(version) is not int or version not in (0, 1):
        raise ValueError("Explicit manual seed control version0/1 required")
    if version and (purpose != "writer" or value.get("nativeActionRetention", 0) != 1):
        raise Denied("Manual native control requires retained Writer profile")
    if binding.get("manual_seed_control", 0) != version:
        if binding["active_turn"] is not None or binding.get("seed_review"):
            raise Conflict("Original review/inference must settle before profile change")
        c.execute("UPDATE hb_purpose_context SET manual_seed_control=%s WHERE agent=%s AND purpose=%s",
                  (version, actor["id"], purpose))
    return version


def row(c, actor, purpose, binding):
    if purpose != "writer" or binding["manual_seed_control"] != 1 or not binding["seed_review"]:
        raise Denied("Original manual Writer review required")
    value = c.execute("""SELECT * FROM hb_context_seed_draft WHERE id=%s AND agent=%s
        AND purpose=%s FOR UPDATE""", (binding["seed_review"], actor["id"], purpose)).fetchone()
    if not value or not value["review"]:
        raise Conflict("Original review unavailable")
    return value


def pending(c, actor, purpose, binding):
    if not binding.get("seed_review"):
        return None
    value = row(c, actor, purpose, binding)
    return review.public(value)


def snapshot(c, store, actor, purpose, binding, p):
    value = row(c, actor, purpose, binding)
    control = value["review"]
    if (p["operationId"] != control["snapshotOperationId"] or
            control["state"] not in ("requested", "prepared", "approved", "applying")):
        raise Conflict("Exact original frozen review snapshot required")
    review.reauthorize(store, c, value, binding)
    content = json.loads("".join(x["content"] for x in control["snapshot"]["chunks"]))
    if p["value"] != {"materialIds": content["nextMaterialIds"]}:
        raise Conflict("Original reviewed material selection changed")
    return control["snapshot"]


def prepared(c, store, actor, purpose, binding, p):
    value = row(c, actor, purpose, binding)
    control, data = value["review"], p["value"]
    from .context_native_idle import PREFLIGHT, STATE
    if set(data) != {"draftId", "approvalDigest", "preflight", "snapshotSha256", "runtime", "historySha256"}:
        raise ValueError("Exact prepared native metadata required")
    if data["draftId"] != str(value["id"]) or control["state"] != "requested":
        raise Conflict("Original requested review changed")
    review.reauthorize(store, c, value, binding)
    if data["snapshotSha256"] != control["snapshot"]["sha256"]:
        raise Conflict("Prepared canonical snapshot differs")
    sha(data["approvalDigest"])
    sha(data["historySha256"])
    preflight = data["preflight"]
    if not isinstance(preflight, dict) or set(preflight) != PREFLIGHT:
        raise ValueError("Exact native preflight required")
    state = preflight["state"]
    if not isinstance(state, dict) or set(state) != STATE:
        raise ValueError("Exact native state required")
    if (state["threadId"] != binding["native_thread"] or state["contextId"] != str(binding["context_id"])
            or state["purpose"] != purpose or state["idle"] is not True
            or state["activeTurnId"] is not None or state["heldOperationId"] is not None
            or preflight["operationId"] != control["nativeOperationId"]):
        raise Conflict("Prepared native cut differs")
    text(state["lastStartedTurnId"], "Original turn")
    UUID(state["windowId"])
    uint(state["historyVersion"], "history version")
    for key in ("packetSha256", "replacementSha256"):
        sha(preflight[key])
    for key in ("prefixItemCount", "replacementItemCount", "replacementWireBytes"):
        uint(preflight[key], key, positive=True)
    if not (preflight["prefixItemCount"] < preflight["replacementItemCount"] <= 256
            and preflight["replacementWireBytes"] <= 1048576):
        raise Conflict("Native prepared packet exceeds contract")
    if not isinstance(data["runtime"], dict) or set(data["runtime"]) != {"binarySha256", "homeConfigSha256"}:
        raise ValueError("Exact runtime fingerprints required")
    for hashed in data["runtime"].values():
        sha(hashed)
    updated = {**control, "state": "prepared", "revision": control["revision"]+1,
               "approvalDigest": data["approvalDigest"], "preflight": preflight,
               "runtime": data["runtime"], "historySha256": data["historySha256"]}
    review.save(c, value, updated)
    return review.public({**value, "review": updated})


def apply(c, store, actor, purpose, binding, p):
    value = row(c, actor, purpose, binding)
    control, data = value["review"], p["value"]
    if set(data) != {"draftId", "approvalDigest", "preflight"}:
        raise ValueError("Exact approved native dispatch required")
    if (data["draftId"] != str(value["id"]) or control["state"] != "approved"
            or data["approvalDigest"] != control["approvalDigest"]
            or data["preflight"] != control["preflight"]):
        raise Conflict("Exact current human approval required")
    review.reauthorize(store, c, value, binding)
    updated = {**control, "state": "applying", "revision": control["revision"]+1}
    review.save(c, value, updated)
    return review.public({**value, "review": updated})


def cancelled(c, store, actor, purpose, binding, p):
    value = row(c, actor, purpose, binding)
    control, data = value["review"], p["value"]
    if (set(data) != {"draftId", "nativeOperationId", "nativeWindowChanged"}
            or data["draftId"] != str(value["id"])
            or data["nativeOperationId"] != control["nativeOperationId"]
            or data["nativeWindowChanged"] is not False or control["state"] != "cancel_requested"):
        raise Conflict("Original no-dispatch cancellation confirmation required")
    review.idle(c, binding)
    updated = {**control, "state": "cancelled", "revision": control["revision"]+1}
    review.save(c, value, updated)
    c.execute("""UPDATE hb_purpose_context SET seed_review=NULL,rebuild_requested=NULL
        WHERE agent=%s AND purpose=%s""", (actor["id"], purpose))
    return review.public({**value, "review": updated})


def installed(c, store, actor, purpose, binding, p):
    value = row(c, actor, purpose, binding)
    control = value["review"]
    if (control["state"] != "applying" or p["value"].get("preflight") != control["preflight"]
            or p["value"].get("snapshotOperationId") != control["snapshotOperationId"]):
        raise Conflict("Original approved installation differs")
    # Dispatch already happened. Record only its exact original native outcome;
    # do not pretend a later revoked reviewer undoes a committed native write.
    content = json.loads("".join(x["content"] for x in control["snapshot"]["chunks"]))
    from .context_native_idle import installed as original
    result = original(c, store, actor, purpose, binding, p, selection=content["selection"])
    updated = {**control, "state": "installed", "revision": control["revision"]+1,
               "generation": result["generation"], "nativeReceipt": result["nativeReceipt"]}
    review.save(c, value, updated)
    c.execute("UPDATE hb_purpose_context SET seed_review=NULL WHERE agent=%s AND purpose=%s",
              (actor["id"], purpose))
    return result


def refused(c, store, actor, purpose, binding, p):
    value = row(c, actor, purpose, binding)
    control, data = value["review"], p["value"]
    if (set(data) != {"draftId", "nativeState", "historySha256", "journalSha256", "noDispatch"}
            or data["draftId"] != str(value["id"]) or data["noDispatch"] is not True
            or control["state"] not in ("requested", "prepared", "approved", "applying")):
        raise Conflict("Original no-dispatch review observation required")
    sha(data["historySha256"])
    sha(data["journalSha256"])
    review.idle(c, binding)
    if control["state"] == "applying" and (
            data["nativeState"] != control["preflight"]["state"]
            or data["historySha256"] != control["historySha256"]):
        raise Conflict("Uncertain native outcome must remain held")
    updated = {**control, "state": "refused", "revision": control["revision"]+1,
               "noDispatchObservation": data}
    review.save(c, value, updated)
    return review.public({**value, "review": updated})

 
def revised(c, store, actor, purpose, binding, p):
    value = row(c, actor, purpose, binding)
    control, data = value["review"], p["value"]
    if (set(data) != {"draftId", "replacementDraftId", "nativeOperationId", "nativeWindowChanged"}
            or data["draftId"] != str(value["id"]) or control["state"] != "revise_requested"
            or data["replacementDraftId"] != control["replacement"]["draftId"]
            or data["nativeOperationId"] != control["nativeOperationId"]
            or data["nativeWindowChanged"] is not False):
        raise Conflict("Original no-dispatch replacement handoff required")
    review.idle(c, binding)
    human, target, _ = review.seeds.authorize(store, c, str(value["reviewer"]),
        control["replacementCredentialDigest"], str(actor["id"]), purpose)
    replacement = review.seeds.draft_row(c, human, str(actor["id"]), purpose, data["replacementDraftId"])
    review.seeds.same_context(replacement, binding)
    if replacement["review"] is not None or replacement["revision"] != control["replacement"]["revision"]:
        raise Conflict("Replacement draft changed")
    view = review.seeds.build_preview(c, store, human, target, binding, replacement, purpose)
    if view["canonicalContentSha256"] != control["replacement"]["canonicalSha256"]:
        raise Conflict("Replacement sources changed; review again without releasing the hold")
    new_control = review.freeze(replacement, binding, view, human, control["replacementCredentialDigest"])
    review.save(c, value, {**control, "state": "superseded", "revision": control["revision"]+1})
    review.save(c, replacement, new_control)
    c.execute("""UPDATE hb_purpose_context SET seed_review=%s,rebuild_requested=%s
        WHERE agent=%s AND purpose=%s""",
        (replacement["id"], new_control["requestOperationId"], actor["id"], purpose))
    return review.public({**replacement, "review": new_control})
