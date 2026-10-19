# Native reader room observations

Hollywood remains the canonical room server. LAX readers consume attributable observations separately from addressed requests; no additional coordinator runs.

Enrollment starts the observation frontier at the current authority head. Existing history remains explicitly retrievable. The durable frontier and unique per-agent event material survive disconnect/restart. Quiet upgrades establish the first frontier before restarting readers and do not assign historical traffic.

Readers ingest at most sixteen eligible events per pass, retaining at most thirty-two pending passive observations. Read grants and direct audiences filter ingestion and are rechecked on retrieval/contribution. Administrators retain the existing authority read policy. Self-authored messages, native activity and peer-completion protocol traffic do not become passive inputs. Addressed requests retain their original route.

An observation has its own agent-scoped identity, original source/authorship and responseRequired=false. It creates no delivery or response need. Reader inference may use a read-only grant without inventing a work claim; normal shared host inference admission still applies. No stopped runtime starts because unread observations exist.

The reader reads exact material and records relevance. A relevant interpretation may amend an already accepted unfinished responsibility after reading its current revision. The objective/request remain unchanged; no turn/steer is sent. A private source cannot expand into a broader or unrelated private work audience. Unrelated observations retain their interpretation without a reply.

Canonical room history supplies bounded references; context_material_read fetches exact original events in chunks. Byte-budget truncation preserves forward/backward cursors, including multibyte text. Cursors do not grant access or restore revoked membership. The responder still exclusively owns outward communication. Observations cannot use accept_work or prepare_response as addressed requests; optional initiative needs deliberate sourced communication and current post permission.

The explicit tests/integration_room_observations.py qualification uses actual HTTP and PostgreSQL, frozen native-binding metadata and rollback-only fixture events. It covers read-only admission, exact retrieval, byte bounds/pagination, original work updates, cursor recovery/deduplication, private filtering and revocation. Additive schema remains installed. Live r31 model evidence is separate; inspect its actual receipt before claiming semantic relevance or absence of acknowledgement traffic.

Remaining gates: proactive new canonical Goal/Work, shared collaboration/report acceptance, natural effect checkpoints, full enterprise install and transport scaling. Readers are stochastic; tools enforce attribution, audiences and lifecycle rather than semantic correctness. Pending database reads remain periodic; event-driven scale is not claimed.
