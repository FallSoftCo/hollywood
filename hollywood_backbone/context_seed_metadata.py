"""Human review configuration projection; never a worker-health certificate."""


def project(binding, actor, system_enabled):
    """Expose configuration and own pending-review metadata without seed bodies."""
    human = actor["kind"] == "human" and actor["administrator"]
    own = None
    if human and binding["own_review_id"] is not None:
        own = {"draftId": str(binding["own_review_id"]),
               "state": binding["own_review_state"],
               "revision": binding["own_review_revision"]}
    return {
        "schema": "lax-human-context-review-availability/v1",
        "purpose": binding["purpose"],
        "sourceIndexVersion": 1,
        "windowGeneration": binding["rebuild_generation"],
        "agentActive": binding["agent_active"],
        "contextSystemEnabled": system_enabled,
        "humanControlAuthorized": bool(human and system_enabled and binding["agent_active"]),
        "nativeReviewConfigured": (binding["purpose"] == "writer"
            and binding["manual_seed_control"] == 1
            and binding["native_action_retention"] == 1),
        "nativeReviewScope": "idle-writer" if binding["purpose"]=="writer" else "preview-only",
        "nativeThreadBound": binding["native_thread"] is not None,
        "inferenceBusy": (binding["active_turn"] is not None
                          or binding["accepting"] or binding["capacity_held"]),
        "rebuildPending": binding["rebuild_requested"] is not None,
        "reviewHeld": binding["seed_review"] is not None,
        "ownReview": own,
        "workerReadiness": "unverified",
        "fullProviderInputReady": False,
    }
