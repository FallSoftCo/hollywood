CREATE TABLE IF NOT EXISTS hb_schema (version integer PRIMARY KEY);
INSERT INTO hb_schema VALUES (1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS hb_authority (
 id uuid PRIMARY KEY, name text NOT NULL, next_seq bigint NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS hb_principal (
 id uuid PRIMARY KEY, authority uuid NOT NULL REFERENCES hb_authority(id),
 name text NOT NULL, kind text NOT NULL CHECK(kind IN ('human','agent','service')),
 administrator boolean NOT NULL DEFAULT false, active boolean NOT NULL DEFAULT true,
 UNIQUE(authority,id), UNIQUE(authority,name)
);
CREATE TABLE IF NOT EXISTS hb_token (
 digest text PRIMARY KEY, principal uuid NOT NULL REFERENCES hb_principal(id),
 revoked boolean NOT NULL DEFAULT false
);
CREATE TABLE IF NOT EXISTS hb_room (
 id uuid PRIMARY KEY, authority uuid NOT NULL REFERENCES hb_authority(id),
 name text NOT NULL, purpose text NOT NULL,
 owner uuid NOT NULL, revision bigint NOT NULL DEFAULT 1,
 UNIQUE(authority,id), UNIQUE(authority,name),
 FOREIGN KEY(authority,owner) REFERENCES hb_principal(authority,id)
);
CREATE TABLE IF NOT EXISTS hb_membership (
 authority uuid NOT NULL, room uuid NOT NULL, principal uuid NOT NULL,
 can_read boolean NOT NULL DEFAULT true, can_post boolean NOT NULL DEFAULT false,
 can_request boolean NOT NULL DEFAULT false, revision bigint NOT NULL DEFAULT 1,
 PRIMARY KEY(room,principal),
 FOREIGN KEY(authority,room) REFERENCES hb_room(authority,id),
 FOREIGN KEY(authority,principal) REFERENCES hb_principal(authority,id)
);
CREATE TABLE IF NOT EXISTS hb_agent_role (
 principal uuid PRIMARY KEY, authority uuid NOT NULL,
 primary_role text NOT NULL, home_room uuid, purpose text NOT NULL DEFAULT '',
 revision bigint NOT NULL DEFAULT 1,
 FOREIGN KEY(authority,principal) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,home_room) REFERENCES hb_room(authority,id)
);
INSERT INTO hb_agent_role(principal,authority,primary_role,home_room,purpose)
 SELECT p.id,p.authority,CASE WHEN p.administrator THEN 'global' ELSE 'worker' END,
 (SELECT r.id FROM hb_room r WHERE r.owner=p.id ORDER BY r.name LIMIT 1),
 CASE WHEN p.administrator THEN 'Enterprise coordination and administration' ELSE 'Own explicitly addressed work' END
 FROM hb_principal p WHERE p.kind='agent' ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS hb_host (
 id uuid PRIMARY KEY, authority uuid NOT NULL, name text NOT NULL,
 identity_fingerprint text NOT NULL, capabilities jsonb NOT NULL,
 UNIQUE(authority,id), UNIQUE(authority,name),
 FOREIGN KEY(authority) REFERENCES hb_authority(id)
);
CREATE TABLE IF NOT EXISTS hb_placement (
 authority uuid NOT NULL, agent uuid PRIMARY KEY, host uuid NOT NULL,
 enabled boolean NOT NULL DEFAULT true, generation bigint NOT NULL DEFAULT 1,
 FOREIGN KEY(authority,agent) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,host) REFERENCES hb_host(authority,id)
);
CREATE TABLE IF NOT EXISTS hb_event (
 id uuid PRIMARY KEY, authority uuid NOT NULL, seq bigint NOT NULL,
 room uuid NOT NULL, author uuid NOT NULL, recipient uuid,
 kind text NOT NULL CHECK(kind IN ('message','request','progress','result','question','approval','control')),
 audience text NOT NULL CHECK(audience IN ('room','direct')),
 body text NOT NULL, source uuid, data jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(authority,id), UNIQUE(authority,seq),
 FOREIGN KEY(authority,room) REFERENCES hb_room(authority,id),
 FOREIGN KEY(authority,author) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,recipient) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,source) REFERENCES hb_event(authority,id),
 CHECK(audience='room' OR recipient IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS hb_room_history ON hb_event(room,seq);
CREATE TABLE IF NOT EXISTS hb_command (
 authority uuid NOT NULL, principal uuid NOT NULL, operation text NOT NULL,
 digest text NOT NULL, event uuid NOT NULL,
 PRIMARY KEY(principal,operation),
 FOREIGN KEY(authority,principal) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,event) REFERENCES hb_event(authority,id)
);
CREATE TABLE IF NOT EXISTS hb_delivery (
 id uuid PRIMARY KEY, authority uuid NOT NULL, event uuid NOT NULL,
 agent uuid NOT NULL, host uuid, generation bigint NOT NULL DEFAULT 0,
 claim_id text, native_thread text, native_turn text,
 state text NOT NULL DEFAULT 'pending' CHECK(state IN
 ('pending','claimed','running','completed','failed','unknown','cancel_requested','cancelled')),
 result_event uuid, revision bigint NOT NULL DEFAULT 1,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(event,agent), UNIQUE(authority,id),
 FOREIGN KEY(authority,event) REFERENCES hb_event(authority,id),
 FOREIGN KEY(authority,agent) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,host) REFERENCES hb_host(authority,id),
 FOREIGN KEY(authority,result_event) REFERENCES hb_event(authority,id)
);
CREATE INDEX IF NOT EXISTS hb_pending_delivery ON hb_delivery(agent,state,event);
CREATE TABLE IF NOT EXISTS hb_claim (
 agent uuid NOT NULL REFERENCES hb_principal(id), operation text NOT NULL,
 host uuid NOT NULL REFERENCES hb_host(id), delivery uuid REFERENCES hb_delivery(id),
 PRIMARY KEY(agent,operation)
);
CREATE TABLE IF NOT EXISTS hb_transition (
 delivery uuid NOT NULL REFERENCES hb_delivery(id), operation text NOT NULL,
 digest text NOT NULL, result jsonb NOT NULL,
 PRIMARY KEY(delivery,operation)
);
CREATE TABLE IF NOT EXISTS hb_attention (
 id uuid PRIMARY KEY, authority uuid NOT NULL, delivery uuid NOT NULL,
 agent uuid NOT NULL, human uuid NOT NULL, room uuid NOT NULL, event uuid NOT NULL,
 generation bigint NOT NULL, claim_id text NOT NULL,
 native_thread text NOT NULL, native_turn text NOT NULL, native_slot text NOT NULL,
 method text NOT NULL, request jsonb NOT NULL, digest text NOT NULL,
 state text NOT NULL DEFAULT 'pending' CHECK(state IN
 ('pending','response_submitted','native_submitted','closed','expired','unknown')),
 response jsonb, response_author uuid, response_event uuid, revision bigint NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(delivery,native_turn,native_slot), UNIQUE(authority,id),
 FOREIGN KEY(authority,delivery) REFERENCES hb_delivery(authority,id),
 FOREIGN KEY(authority,agent) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,human) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,room) REFERENCES hb_room(authority,id),
 FOREIGN KEY(authority,event) REFERENCES hb_event(authority,id),
 FOREIGN KEY(authority,response_author) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,response_event) REFERENCES hb_event(authority,id)
);
CREATE INDEX IF NOT EXISTS hb_attention_inbox ON hb_attention(human,state,created_at);
CREATE TABLE IF NOT EXISTS hb_attention_command (
 attention uuid NOT NULL REFERENCES hb_attention(id), principal uuid NOT NULL REFERENCES hb_principal(id),
 operation text NOT NULL, digest text NOT NULL, result jsonb NOT NULL,
 PRIMARY KEY(attention,principal,operation)
);
CREATE TABLE IF NOT EXISTS hb_activity (
 delivery uuid NOT NULL REFERENCES hb_delivery(id), generation bigint NOT NULL,
 native_thread text NOT NULL, native_turn text NOT NULL, item_id text NOT NULL,
 phase text NOT NULL CHECK(phase IN ('started','completed')), digest text NOT NULL,
 event uuid NOT NULL REFERENCES hb_event(id),
 PRIMARY KEY(delivery,generation,native_turn,item_id,phase)
);

-- Goals describe desired outcomes. Work submission, native completion and
-- acceptance are separate records; none implies the other.
CREATE TABLE IF NOT EXISTS hb_goal (
 id uuid PRIMARY KEY, authority uuid NOT NULL REFERENCES hb_authority(id),
 room uuid NOT NULL, owner uuid NOT NULL, creator uuid NOT NULL,
 source uuid NOT NULL, title text NOT NULL, objective text NOT NULL,
 acceptance text NOT NULL, state text NOT NULL DEFAULT 'active'
 CHECK(state IN ('active','paused','accepted','cancelled')),
 revision bigint NOT NULL DEFAULT 1, review_remaining integer NOT NULL DEFAULT 0,
 review_digest text, review_request uuid,
 UNIQUE(authority,id),
 FOREIGN KEY(authority,room) REFERENCES hb_room(authority,id),
 FOREIGN KEY(authority,owner) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,creator) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,source) REFERENCES hb_event(authority,id),
 FOREIGN KEY(authority,review_request) REFERENCES hb_event(authority,id),
 CHECK(review_remaining BETWEEN 0 AND 16)
);
CREATE INDEX IF NOT EXISTS hb_goal_owner ON hb_goal(owner,state,review_remaining);
CREATE TABLE IF NOT EXISTS hb_work (
 id uuid PRIMARY KEY, authority uuid NOT NULL, goal uuid NOT NULL,
 room uuid NOT NULL, owner uuid NOT NULL, specification text NOT NULL,
 acceptance text NOT NULL, state text NOT NULL DEFAULT 'planned'
 CHECK(state IN ('planned','assigned','blocked','submitted','accepted','rejected','cancel_requested','cancelled')),
 revision bigint NOT NULL DEFAULT 1, request_event uuid, delivery uuid,
 report_event uuid, evidence jsonb NOT NULL DEFAULT '[]', note text NOT NULL DEFAULT '',
 UNIQUE(authority,id), UNIQUE(authority,goal,id),
 FOREIGN KEY(authority,goal) REFERENCES hb_goal(authority,id),
 FOREIGN KEY(authority,room) REFERENCES hb_room(authority,id),
 FOREIGN KEY(authority,owner) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,request_event) REFERENCES hb_event(authority,id),
 FOREIGN KEY(authority,delivery) REFERENCES hb_delivery(authority,id),
 FOREIGN KEY(authority,report_event) REFERENCES hb_event(authority,id)
);
CREATE INDEX IF NOT EXISTS hb_work_goal ON hb_work(goal,id);
CREATE INDEX IF NOT EXISTS hb_work_room ON hb_work(room,id);
CREATE UNIQUE INDEX IF NOT EXISTS hb_work_delivery ON hb_work(delivery) WHERE delivery IS NOT NULL;
CREATE TABLE IF NOT EXISTS hb_work_dependency (
 authority uuid NOT NULL, goal uuid NOT NULL, work uuid NOT NULL, prerequisite uuid NOT NULL,
 PRIMARY KEY(work,prerequisite), CHECK(work<>prerequisite),
 FOREIGN KEY(authority,goal,work) REFERENCES hb_work(authority,goal,id),
 FOREIGN KEY(authority,goal,prerequisite) REFERENCES hb_work(authority,goal,id)
);
CREATE TABLE IF NOT EXISTS hb_goal_review (
 goal uuid NOT NULL REFERENCES hb_goal(id), trigger_digest text NOT NULL,
 request_event uuid NOT NULL REFERENCES hb_event(id), PRIMARY KEY(goal,trigger_digest)
);
CREATE TABLE IF NOT EXISTS hb_supervision_cursor (
 authority uuid NOT NULL REFERENCES hb_authority(id),
 principal uuid PRIMARY KEY REFERENCES hb_principal(id), seq bigint NOT NULL DEFAULT 0,
 revision bigint NOT NULL DEFAULT 1
);

-- Additive workflow schema; prior communication state is retained.
INSERT INTO hb_schema VALUES (2) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS hb_native_goal_binding (
 delivery uuid NOT NULL REFERENCES hb_delivery(id), generation bigint NOT NULL,
 goal uuid REFERENCES hb_goal(id), work uuid REFERENCES hb_work(id),
 native_thread text NOT NULL, native_goal_id text NOT NULL, context_id text NOT NULL,
 workspace_id uuid NOT NULL, objective_digest text NOT NULL, digest text NOT NULL,
 PRIMARY KEY(delivery,generation)
);
INSERT INTO hb_schema VALUES (3) ON CONFLICT DO NOTHING;

-- Desired cancellation survives unknown execution; it is not a stop acknowledgement.
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM hb_schema WHERE version=4) THEN
  ALTER TABLE hb_delivery ADD COLUMN IF NOT EXISTS cancellation_requested boolean NOT NULL DEFAULT false;
  UPDATE hb_delivery SET cancellation_requested=true WHERE state IN ('cancel_requested','cancelled');
  INSERT INTO hb_schema VALUES (4);
 END IF;
END $$;

-- Goal cancellation is a durable aggregate control, never a model polling loop.
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM hb_schema WHERE version=5) THEN
  ALTER TABLE hb_goal ADD COLUMN cancellation_event uuid REFERENCES hb_event(id);
  ALTER TABLE hb_goal DROP CONSTRAINT hb_goal_state_check;
  ALTER TABLE hb_goal ADD CONSTRAINT hb_goal_state_check CHECK(state IN ('active','paused','accepted','cancel_requested','cancelled'));
  INSERT INTO hb_schema VALUES (5);
 END IF;
END $$;

-- Ongoing native responsibilities are separate from canonical business goals.
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM hb_schema WHERE version=6) THEN
  ALTER TABLE hb_agent_role ADD COLUMN native_goal_scope text NOT NULL DEFAULT 'work'
   CHECK(native_goal_scope IN ('role','work'));
  UPDATE hb_agent_role SET native_goal_scope='role' WHERE primary_role='global';
  ALTER TABLE hb_native_goal_binding ADD COLUMN binding_kind text NOT NULL DEFAULT 'coordination'
   CHECK(binding_kind IN ('role','work','coordination'));
  ALTER TABLE hb_native_goal_binding ADD COLUMN role_revision bigint CHECK(role_revision>0);
  UPDATE hb_native_goal_binding SET binding_kind='work' WHERE work IS NOT NULL;
  INSERT INTO hb_schema VALUES (6);
 END IF;
END $$;


CREATE TABLE IF NOT EXISTS hb_native_role_goal (
 principal uuid NOT NULL REFERENCES hb_principal(id), context_id text NOT NULL,
 role_revision bigint NOT NULL CHECK(role_revision>0),
 host uuid NOT NULL REFERENCES hb_host(id), workspace_id uuid NOT NULL,
 room uuid NOT NULL REFERENCES hb_room(id), audience text NOT NULL, direct_sender uuid,
 native_thread text NOT NULL, native_goal_id text NOT NULL, objective_digest text NOT NULL,
 PRIMARY KEY(principal,context_id)
);


-- Preserve the legacy binding row shape for read compatibility and bounded-work
-- rollback. New role writers still require the matching connector capability.
CREATE TABLE IF NOT EXISTS hb_native_goal_scope (
 delivery uuid NOT NULL, generation bigint NOT NULL,
 binding_kind text NOT NULL CHECK(binding_kind IN ('role','work','coordination')),
 role_revision bigint CHECK(role_revision>0),
 PRIMARY KEY(delivery,generation),
 FOREIGN KEY(delivery,generation) REFERENCES hb_native_goal_binding(delivery,generation),
 CHECK((binding_kind='role') = (role_revision IS NOT NULL))
);
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM hb_schema WHERE version=7) THEN
  INSERT INTO hb_native_goal_scope
   SELECT delivery,generation,binding_kind,role_revision FROM hb_native_goal_binding;
  ALTER TABLE hb_native_goal_binding DROP COLUMN binding_kind;
  ALTER TABLE hb_native_goal_binding DROP COLUMN role_revision;
  INSERT INTO hb_schema VALUES (7);
 END IF;
END $$;


-- Goal-scoped team organization. Old bounded-work bindings retain their shape.
CREATE TABLE IF NOT EXISTS hb_goal_cooperation (
 authority uuid NOT NULL, goal uuid PRIMARY KEY,
 policy text NOT NULL CHECK(policy IN ('leader_award','kanban_pull')),
 planner uuid NOT NULL, reviewer uuid, revision bigint NOT NULL DEFAULT 1,
 UNIQUE(authority,goal), CHECK(planner IS DISTINCT FROM reviewer),
 FOREIGN KEY(authority,goal) REFERENCES hb_goal(authority,id),
 FOREIGN KEY(authority,planner) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,reviewer) REFERENCES hb_principal(authority,id)
);
CREATE TABLE IF NOT EXISTS hb_cooperation_executor (
 authority uuid NOT NULL, goal uuid NOT NULL, principal uuid NOT NULL,
 PRIMARY KEY(goal,principal),
 FOREIGN KEY(authority,goal) REFERENCES hb_goal_cooperation(authority,goal),
 FOREIGN KEY(authority,principal) REFERENCES hb_principal(authority,id)
);
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM hb_schema WHERE version=8) THEN
  ALTER TABLE hb_work ALTER COLUMN owner DROP NOT NULL;
  ALTER TABLE hb_work ADD CONSTRAINT hb_work_owner_admission
    CHECK(owner IS NOT NULL OR state IN ('planned','cancelled'));
  INSERT INTO hb_schema VALUES (8);
 END IF;
END $$;


-- Explicit current decisions and original work-attempt lineage.
CREATE TABLE IF NOT EXISTS hb_decision (
 id uuid PRIMARY KEY, authority uuid NOT NULL, room uuid NOT NULL, goal uuid,
 key text NOT NULL, creator uuid NOT NULL, revision bigint NOT NULL DEFAULT 0,
 UNIQUE(authority,id), UNIQUE(room,key),
 FOREIGN KEY(authority,room) REFERENCES hb_room(authority,id),
 FOREIGN KEY(authority,goal) REFERENCES hb_goal(authority,id),
 FOREIGN KEY(authority,creator) REFERENCES hb_principal(authority,id)
);
CREATE TABLE IF NOT EXISTS hb_decision_revision (
 decision uuid NOT NULL REFERENCES hb_decision(id), revision bigint NOT NULL CHECK(revision>0),
 state text NOT NULL CHECK(state IN ('active','retracted','conflicted')),
 body text NOT NULL, issuer uuid NOT NULL REFERENCES hb_principal(id),
 source uuid NOT NULL REFERENCES hb_event(id), event uuid NOT NULL UNIQUE REFERENCES hb_event(id),
 PRIMARY KEY(decision,revision)
);
CREATE TABLE IF NOT EXISTS hb_work_attempt (
 delivery uuid PRIMARY KEY, authority uuid NOT NULL, work uuid NOT NULL,
 number bigint NOT NULL CHECK(number>0), owner uuid NOT NULL,
 request_event uuid NOT NULL, specification text NOT NULL, acceptance text NOT NULL,
 origin text NOT NULL CHECK(origin IN ('native-admission','migration-current-binding')),
 report_event uuid REFERENCES hb_event(id), evidence jsonb NOT NULL DEFAULT '[]',
 report_note text NOT NULL DEFAULT '', review_event uuid REFERENCES hb_event(id),
 review_decision text CHECK(review_decision IN ('accepted','rejected')),
 review_note text NOT NULL DEFAULT '', briefing_sequence bigint, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(work,number),
 FOREIGN KEY(authority,delivery) REFERENCES hb_delivery(authority,id),
 FOREIGN KEY(authority,work) REFERENCES hb_work(authority,id),
 FOREIGN KEY(authority,owner) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,request_event) REFERENCES hb_event(authority,id)
);
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM hb_schema WHERE version=9) THEN
  -- This retains the known current binding, not invented earlier attempts.
  INSERT INTO hb_work_attempt
   (delivery,authority,work,number,owner,request_event,specification,acceptance,origin,
    report_event,evidence,report_note,review_decision,created_at)
   SELECT w.delivery,w.authority,w.id,1,w.owner,w.request_event,w.specification,w.acceptance,
    'migration-current-binding',w.report_event,w.evidence,w.note,
    CASE WHEN w.state IN ('accepted','rejected') THEN w.state ELSE NULL END,e.created_at
   FROM hb_work w JOIN hb_event e ON e.id=w.request_event
   WHERE w.delivery IS NOT NULL;
  INSERT INTO hb_schema VALUES (9);
 END IF;
END $$;


-- Enrolled machine services do not acquire agent execution or room membership.
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM hb_schema WHERE version=10) THEN
  ALTER TABLE hb_principal DROP CONSTRAINT hb_principal_kind_check;
  ALTER TABLE hb_principal ADD CONSTRAINT hb_principal_kind_check
   CHECK(kind IN ('human','agent','service'));
 END IF;
END $$;
CREATE TABLE IF NOT EXISTS hb_host_admission (
 host uuid PRIMARY KEY, authority uuid NOT NULL, controller uuid NOT NULL,
 machine_identity text NOT NULL, revision bigint NOT NULL DEFAULT 1,
 max_concurrent integer NOT NULL CHECK(max_concurrent BETWEEN 1 AND 4096),
 reserved_interactive integer NOT NULL CHECK(reserved_interactive BETWEEN 0 AND 4096),
 memory_per_turn bigint NOT NULL CHECK(memory_per_turn>0),
 memory_reserve bigint NOT NULL CHECK(memory_reserve>=0),
 sample_max_age integer NOT NULL CHECK(sample_max_age BETWEEN 5 AND 300),
 connector_max_age integer NOT NULL CHECK(connector_max_age BETWEEN 5 AND 300),
 next_ticket bigint NOT NULL DEFAULT 0,
 CHECK(reserved_interactive<=max_concurrent),
 FOREIGN KEY(authority,host) REFERENCES hb_host(authority,id),
 FOREIGN KEY(authority,controller) REFERENCES hb_principal(authority,id)
);
CREATE TABLE IF NOT EXISTS hb_host_control_agent (
 host uuid NOT NULL REFERENCES hb_host_admission(host),
 agent uuid NOT NULL REFERENCES hb_principal(id), PRIMARY KEY(host,agent)
);
CREATE TABLE IF NOT EXISTS hb_host_sample (
 host uuid PRIMARY KEY REFERENCES hb_host_admission(host),
 cpu_slots integer NOT NULL, available_memory bigint NOT NULL,
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS hb_admission_command (
 principal uuid NOT NULL REFERENCES hb_principal(id), operation text NOT NULL,
 digest text NOT NULL, result jsonb NOT NULL, PRIMARY KEY(principal,operation)
);
CREATE TABLE IF NOT EXISTS hb_connector_observation (
 agent uuid PRIMARY KEY REFERENCES hb_principal(id), host uuid NOT NULL REFERENCES hb_host(id),
 capabilities jsonb NOT NULL, observed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS hb_admission_trace (
 delivery uuid PRIMARY KEY REFERENCES hb_delivery(id), host uuid REFERENCES hb_host(id),
 received_at timestamptz NOT NULL, first_eligible_at timestamptz,
 admitted_at timestamptz, running_at timestamptz, terminal_at timestamptz,
 queue_class text CHECK(queue_class IN ('interactive','worker')), ticket bigint,
 blocker text, blocker_since timestamptz, observed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS hb_admission_host_history ON hb_admission_trace(host,ticket);
CREATE OR REPLACE FUNCTION hb_trace_delivery() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO hb_admission_trace(delivery,received_at)
  SELECT NEW.id,e.created_at FROM hb_event e WHERE e.id=NEW.event ON CONFLICT DO NOTHING;
 IF TG_OP='UPDATE' AND NEW.state IS DISTINCT FROM OLD.state THEN
  UPDATE hb_admission_trace SET
   admitted_at=CASE WHEN NEW.state='claimed' THEN COALESCE(admitted_at,clock_timestamp()) ELSE admitted_at END,
   running_at=CASE WHEN NEW.state='running' THEN COALESCE(running_at,clock_timestamp()) ELSE running_at END,
   terminal_at=CASE WHEN NEW.state IN ('completed','failed','cancelled')
     THEN COALESCE(terminal_at,clock_timestamp()) ELSE terminal_at END,
   blocker=CASE WHEN NEW.state='unknown' THEN 'unresolved_execution'
     WHEN NEW.state='cancel_requested' THEN 'awaiting_stop_acknowledgement' ELSE NULL END,
   blocker_since=CASE WHEN NEW.state IN ('unknown','cancel_requested') THEN clock_timestamp() ELSE NULL END,
   observed_at=clock_timestamp() WHERE delivery=NEW.id;
 END IF;
 RETURN NEW;
END $$;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM hb_schema WHERE version=10) THEN
  INSERT INTO hb_admission_trace(delivery,received_at)
   SELECT d.id,e.created_at FROM hb_delivery d JOIN hb_event e ON e.id=d.event;
  CREATE TRIGGER hb_trace_delivery_change AFTER INSERT OR UPDATE OF state ON hb_delivery
   FOR EACH ROW EXECUTE FUNCTION hb_trace_delivery();
  INSERT INTO hb_schema VALUES (10);
 END IF;
END $$;


CREATE TABLE IF NOT EXISTS hb_portfolio_policy (
 principal uuid PRIMARY KEY REFERENCES hb_principal(id),
 authority uuid NOT NULL UNIQUE REFERENCES hb_authority(id), room uuid NOT NULL REFERENCES hb_room(id),
 enabled boolean NOT NULL, revision bigint NOT NULL DEFAULT 1,
 activation_seq bigint NOT NULL, scanned_seq bigint NOT NULL,
 reviews_per_window integer NOT NULL CHECK(reviews_per_window BETWEEN 1 AND 64),
 window_seconds integer NOT NULL CHECK(window_seconds BETWEEN 60 AND 86400),
 window_started timestamptz NOT NULL DEFAULT clock_timestamp(), reviews_used integer NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS hb_portfolio_item (
 principal uuid NOT NULL REFERENCES hb_portfolio_policy(principal),
 scope text NOT NULL CHECK(scope IN ('goal','delivery')), reference uuid NOT NULL,
 goal uuid REFERENCES hb_goal(id), delivery uuid REFERENCES hb_delivery(id),
 CHECK((scope='goal' AND goal=reference AND delivery IS NULL)
    OR (scope='delivery' AND delivery=reference AND goal IS NULL)),
 source_event uuid NOT NULL REFERENCES hb_event(id), latest_seq bigint NOT NULL,
 dispatched_seq bigint NOT NULL DEFAULT 0, dispatched_digest text, request_event uuid REFERENCES hb_event(id),
 PRIMARY KEY(principal,scope,reference)
);
CREATE TABLE IF NOT EXISTS hb_portfolio_command (
 principal uuid NOT NULL REFERENCES hb_principal(id), operation text NOT NULL,
 digest text NOT NULL, result jsonb NOT NULL, PRIMARY KEY(principal,operation)
);
INSERT INTO hb_schema VALUES (11) ON CONFLICT DO NOTHING;

-- Human guidance targets one existing native turn and never creates a delivery.
CREATE TABLE IF NOT EXISTS hb_native_control (
 id uuid PRIMARY KEY, authority uuid NOT NULL, delivery uuid NOT NULL,
 agent uuid NOT NULL, human uuid NOT NULL, room uuid NOT NULL, event uuid NOT NULL,
 generation bigint NOT NULL, claim_id text NOT NULL,
 native_thread text NOT NULL, native_turn text NOT NULL,
 operation text NOT NULL, body text NOT NULL, digest text NOT NULL, captured_scope jsonb NOT NULL,
 state text NOT NULL DEFAULT 'pending' CHECK(state IN
 ('pending','dispatching','native_admitted','native_recorded','unknown','rejected')),
 evidence jsonb NOT NULL DEFAULT '{}', revision bigint NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(human,operation), UNIQUE(authority,id),
 FOREIGN KEY(authority,delivery) REFERENCES hb_delivery(authority,id),
 FOREIGN KEY(authority,agent) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,human) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,room) REFERENCES hb_room(authority,id),
 FOREIGN KEY(authority,event) REFERENCES hb_event(authority,id)
);
CREATE INDEX IF NOT EXISTS hb_native_control_pending ON hb_native_control(delivery,state);
CREATE TABLE IF NOT EXISTS hb_native_control_command (
 control uuid NOT NULL REFERENCES hb_native_control(id), operation text NOT NULL,
 digest text NOT NULL, result jsonb NOT NULL, PRIMARY KEY(control,operation)
);
INSERT INTO hb_schema VALUES (12) ON CONFLICT DO NOTHING;

-- New command evidence is immutable at acceptance. Older rows stay explicitly unhydrated.
ALTER TABLE hb_command ADD COLUMN IF NOT EXISTS wire_request jsonb;
ALTER TABLE hb_command ADD COLUMN IF NOT EXISTS accepted_result jsonb;
INSERT INTO hb_schema VALUES (13) ON CONFLICT DO NOTHING;

-- Original administrative acceptance is private to its still-authorized issuer.
-- Only an exact request digest is retained; enrollment credentials are excluded.
CREATE TABLE IF NOT EXISTS hb_administrative_command (
 authority uuid NOT NULL, principal uuid NOT NULL, operation text NOT NULL,
 method text NOT NULL CHECK(method IN ('POST','PUT')), path text NOT NULL,
 wire_digest text NOT NULL, accepted_result jsonb NOT NULL,
 accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(principal,operation),
 FOREIGN KEY(authority,principal) REFERENCES hb_principal(authority,id)
);
INSERT INTO hb_schema VALUES (14) ON CONFLICT DO NOTHING;

-- Resource liveness is independent from delivery/effect outcome.
CREATE TABLE IF NOT EXISTS hb_executor (
 id uuid PRIMARY KEY, authority uuid NOT NULL, delivery uuid NOT NULL,
 agent uuid NOT NULL, host uuid NOT NULL, generation bigint NOT NULL, claim_id text NOT NULL,
 descriptor jsonb NOT NULL, descriptor_digest text NOT NULL,
 state text NOT NULL DEFAULT 'captured' CHECK(state IN ('captured','live','unknown','stopped')),
 controller uuid, evidence jsonb, revision bigint NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), observed_at timestamptz,
 UNIQUE(delivery,generation),
 FOREIGN KEY(authority,delivery) REFERENCES hb_delivery(authority,id),
 FOREIGN KEY(authority,agent) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,host) REFERENCES hb_host(authority,id),
 FOREIGN KEY(authority,controller) REFERENCES hb_principal(authority,id)
);
CREATE INDEX IF NOT EXISTS hb_executor_host_state ON hb_executor(host,state);
CREATE TABLE IF NOT EXISTS hb_executor_command (
 principal uuid NOT NULL REFERENCES hb_principal(id), operation text NOT NULL,
 digest text NOT NULL, result jsonb NOT NULL, PRIMARY KEY(principal,operation)
);
INSERT INTO hb_schema VALUES (15) ON CONFLICT DO NOTHING;

-- Preserve launch provenance before a native process exists.
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM hb_schema WHERE version=16) THEN
  ALTER TABLE hb_executor ADD COLUMN launch_phase text NOT NULL DEFAULT 'attached'
    CHECK(launch_phase IN ('reserved','attached'));
  ALTER TABLE hb_executor ADD COLUMN reservation_descriptor jsonb;
  ALTER TABLE hb_executor ADD COLUMN reservation_digest text;
  UPDATE hb_executor SET reservation_descriptor=descriptor,reservation_digest=descriptor_digest;
  INSERT INTO hb_schema VALUES (16);
 END IF;
END $$;


-- Provenance commits atomically with the domain effect. Bodies/credentials are excluded.
CREATE TABLE IF NOT EXISTS hb_native_effect (
 sequence bigserial PRIMARY KEY, authority uuid NOT NULL, principal uuid NOT NULL,
 delivery uuid NOT NULL, generation bigint NOT NULL CHECK(generation>0),
 fingerprint text NOT NULL, source jsonb NOT NULL,
 method text NOT NULL, path text NOT NULL, wire_digest text NOT NULL, operation text,
 accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(principal,fingerprint),
 FOREIGN KEY(authority,principal) REFERENCES hb_principal(authority,id),
 FOREIGN KEY(authority,delivery) REFERENCES hb_delivery(authority,id)
);
CREATE INDEX IF NOT EXISTS hb_native_effect_delivery
 ON hb_native_effect(authority,delivery,sequence);
INSERT INTO hb_schema VALUES (17) ON CONFLICT DO NOTHING;


-- Expand-only commit-ordered provenance cursors; old body evidence stays incomplete.
CREATE TABLE IF NOT EXISTS hb_native_effect_cursor (
 authority uuid PRIMARY KEY REFERENCES hb_authority(id),
 last_seq bigint NOT NULL DEFAULT 0
);
ALTER TABLE hb_native_effect ADD COLUMN IF NOT EXISTS authority_seq bigint;
ALTER TABLE hb_native_effect ADD COLUMN IF NOT EXISTS wire_body_captured boolean NOT NULL DEFAULT false;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM hb_schema WHERE version=18) THEN
  UPDATE hb_native_effect SET authority_seq=sequence WHERE authority_seq IS NULL;
  INSERT INTO hb_native_effect_cursor(authority,last_seq)
   SELECT authority,max(authority_seq) FROM hb_native_effect GROUP BY authority
   ON CONFLICT(authority) DO UPDATE SET last_seq=EXCLUDED.last_seq;
  INSERT INTO hb_schema VALUES (18);
 END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS hb_native_effect_authority_cursor
 ON hb_native_effect(authority,authority_seq);


-- Immutable links to retained source evidence; import never creates execution intents.
CREATE TABLE IF NOT EXISTS hb_imported_event (
 authority uuid NOT NULL, source_system text NOT NULL, source_id bigint NOT NULL,
 event uuid NOT NULL, source_digest text NOT NULL,
 PRIMARY KEY(authority,source_system,source_id),
 FOREIGN KEY(authority,event) REFERENCES hb_event(authority,id)
);
INSERT INTO hb_schema VALUES (19) ON CONFLICT DO NOTHING;

-- Recovery is explicit new intent, retaining immutable original attempt evidence.
CREATE TABLE IF NOT EXISTS hb_work_recovery (
 delivery uuid PRIMARY KEY REFERENCES hb_work_attempt(delivery),
 authority uuid NOT NULL REFERENCES hb_authority(id),
 work uuid NOT NULL REFERENCES hb_work(id), event uuid UNIQUE NOT NULL REFERENCES hb_event(id),
 principal uuid NOT NULL REFERENCES hb_principal(id), evidence jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO hb_schema VALUES (20) ON CONFLICT DO NOTHING;

-- Agent context is independent of rooms, role revisions, goals and work attempts.
-- Legacy goal bindings remain intact for readback and rollback.
CREATE TABLE IF NOT EXISTS hb_agent_context (
 principal uuid PRIMARY KEY REFERENCES hb_principal(id),
 authority uuid NOT NULL REFERENCES hb_authority(id),
 context_id text NOT NULL CHECK(context_id ~ '^[a-f0-9]{64}$'),
 native_thread text NOT NULL, host uuid NOT NULL REFERENCES hb_host(id),
 workspace_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(authority,native_thread)
);
CREATE TABLE IF NOT EXISTS hb_agent_execution (
 delivery uuid NOT NULL REFERENCES hb_delivery(id), generation bigint NOT NULL,
 principal uuid NOT NULL REFERENCES hb_principal(id),
 context_id text NOT NULL, native_thread text NOT NULL,
 workspace_id uuid NOT NULL, role_revision bigint NOT NULL CHECK(role_revision>0),
 digest text NOT NULL,
 PRIMARY KEY(delivery,generation)
);
CREATE TABLE IF NOT EXISTS hb_agent_communication (
 token_digest text PRIMARY KEY REFERENCES hb_token(digest),
 principal uuid NOT NULL REFERENCES hb_principal(id),
 allow_requests boolean NOT NULL DEFAULT false
);
INSERT INTO hb_schema VALUES (21) ON CONFLICT DO NOTHING;

-- A report ties a completed original obligation to later normal participation.
-- Original delivery/attempt/context identities and execution/effect history stay intact.
CREATE TABLE IF NOT EXISTS hb_work_report_reconciliation (
 delivery uuid PRIMARY KEY REFERENCES hb_delivery(id),
 authority uuid NOT NULL REFERENCES hb_authority(id),
 work uuid NOT NULL REFERENCES hb_work(id), event uuid UNIQUE NOT NULL REFERENCES hb_event(id),
 principal uuid NOT NULL REFERENCES hb_principal(id),
 current_delivery uuid NOT NULL REFERENCES hb_delivery(id), current_generation bigint NOT NULL,
 evidence jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK(delivery<>current_delivery)
);
INSERT INTO hb_schema VALUES (22) ON CONFLICT DO NOTHING;
