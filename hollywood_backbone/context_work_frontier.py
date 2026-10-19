"""Per-work consumed continuation revision; no model starts from a previous hash."""
from psycopg.types.json import Jsonb

def revision(row):
    return row["value"].get("resumeRevision",0)

def runnable(row):
    return revision(row)>row["value"].get("execution",{}).get("resumeRevision",-1)

def normalize(c,actor):
    # Preserve old completed-inference behavior on a software-only upgrade.
    # This records consumption, not work completion or external-effect outcome.
    c.execute("""UPDATE hb_context_material m SET
        value=jsonb_set(value,'{execution,resumeRevision}',
          to_jsonb(coalesce((value->>'resumeRevision')::bigint,0))),
        revision=revision+1,updated_at=clock_timestamp()
        WHERE agent=%s AND kind='work' AND state IN ('pending','running','waiting')
          AND value->'execution'->>'status' IN ('completed','interrupted','failed')
          AND NOT (value->'execution' ? 'resumeRevision')
          AND NOT EXISTS(SELECT 1 FROM hb_purpose_context b WHERE b.agent=m.agent
            AND b.active_turn IS NOT NULL AND m.id=ANY(b.active_material))""",(actor["id"],))
