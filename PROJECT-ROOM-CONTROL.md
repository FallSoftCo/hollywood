# Optional ordinary-human project-room control

Source candidate based on `94c1cc81782c2d1d9d8f46e0d031bba688c96161`;
not a published version or live program replacement. The existing room catalog
adds `roomControl` protocol `hollywood-project-rooms/v1`, authenticated authority,
principal and canonical LOCAL readable/requestable source-room IDs. This is a
permission advertisement, not a new endpoint or authority for a client guess.

Existing POST rooms accepts optional `sourceRoomId`. Existing administrators
retain their original create path. For an ordinary active human, a stable command
and that source room are required: human read+request rights and an active agent
owner with read membership there. Foreign/reference rooms cannot confer this
authority. Existing canonical room checks hold membership locks during the
transaction. The new owner and human retain read/post/request membership; existing
configured representative joins remain unchanged. Non-admin creation never
updates the agent's role/home-room. No runtime, host access or business task is
started by metadata creation. Other admin methods remain denied.

No schema/dependency/credential/profile/model changes. Wire digest/idempotent
original acceptance and actor/operation locking are unchanged. Deploy only this
reviewed API program through its existing manager after actual fresh quiet and
rollback pins; do not restart native, gateway, NATS or PostgreSQL. Root owns
versioning/build and actual activation. Paired-device gateway must ALSO advertise
and enforce the additive optional field; an old unadvertised gateway remains
read-only for creation.

Rollback to the exact retained API closure removes the capability advertisement,
so a compatible client holds new human creation rather than guessing. Existing
admin callers, rooms/memberships and saved receipts survive; rollback is NOT
permission to undo an accepted room or replay an old command. A source fixture
is not actual loaded-process or full-fleet compatibility proof.

Qualification uses the companion Pocket isolated HTTP/PostgreSQL journey with
an ordinary human (`administrator=false`), seven exact policy negatives, bounded
head/preceding-page reads, private audience, and lost-ACK reopen without duplicate
POST. Runtime/model processes are never started; synthetic principal rows are
only fixture data. External source/version/attempt hashes are retained separately.

Compatibility revision: both HTTP invocation and Store mutation retain the
original four positional arguments when `sourceRoomId` is omitted/None. Only an
actually supplied source adds the fifth argument. The retained179 administrative
receipt compares method/path/wire digest, not Python arguments; do not claim an
unobserved local receipt conflict. Preserving route arguments also avoids changing
the omitted-field shape carried by the existing federated domain envelope.
The focused upgrade fixture seeds an original four-argument acceptance through
the exact retained179 mutation module, then verifies candidate Store and actual
HTTP reads return it without a second domain mutation or changed original rows.
Changed source payload under the same operation still conflicts. This fixture
is local HTTP/PG, not a NATS transport or production replay qualification.
