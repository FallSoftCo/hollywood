"""Actual captured native Goal in rollback-only PG; no model behavior claim."""
import copy,json,os,time
from pathlib import Path
from hollywood_backbone.store import Store,Denied
from hollywood_backbone.goal_contract import current,mutate,accept_goal,ContractConflict
from hollywood_backbone.context_goal_review_frontier import snapshot,changed

G="613066f4-da61-5b6a-8ec6-8c6ac54924e0";OWNER="d4419ed7-1a26-4d3b-b074-bf2d4949aad3";OTHER="6750895c-0ed5-563b-9879-65c268c43a7a"
def main():
 os.umask(0o077);out=Path(os.environ["PROPOSAL_EVIDENCE"]);assert not out.exists();out.mkdir(mode=0o700)
 store=Store(json.loads(Path(os.environ["PROPOSAL_DATABASE"]).read_text()));cases=[]
 def goal(c):return c.execute("SELECT * FROM hb_goal WHERE id=%s",(G,)).fetchone()
 def records(c):
  return {name:c.execute(sql,(G,)).fetchall() for name,sql in {
   'goal':'SELECT * FROM hb_goal WHERE id=%s',
   'definitions':'SELECT * FROM hb_goal_definition WHERE goal=%s ORDER BY revision',
   'strategies':'SELECT * FROM hb_goal_strategy WHERE goal=%s ORDER BY revision',
   'work':'SELECT * FROM hb_work WHERE goal=%s ORDER BY id'}.items()}
 with store.pool.connection() as c:
  before=records(c);assert goal(c)['state']=='accepted';c.rollback()
 def fixture(c):
  c.execute("UPDATE hb_goal SET state='active' WHERE id=%s",(G,))
  a=store.actor(c,OWNER);g=goal(c)
  d=c.execute("SELECT * FROM hb_goal_definition WHERE goal=%s AND revision=%s",(G,g["definition_revision"])).fetchone()
  b=copy.deepcopy(d["definition"]);b["criteria"].append({"id":"optional-extra-proposal","requirement":"Proposed optional extra scope, not yet adopted.","method":"artifact","verification":"Requires separate evidence if adopted."})
  mutate(store,c,a,g,"goal.definition",{"definitionRevision":g["definition_revision"],"sourceEventId":str(d["source"]),"definition":b,"note":"Disclosed rolled-back proposal lifecycle case."})
  c.execute("UPDATE hb_goal SET revision=revision+1 WHERE id=%s",(G,))
  g=goal(c);p=current(c,g)["definition"]
  payload={"definitionRevision":p["revision"],"definitionSha256":p["digest"],"sourceEventId":str(d["source"]),"note":"Decline/defer optional draft; adopted requirements and original evidence remain current."}
  return a,g,payload,b
 def case(name,f):
  with store.pool.connection() as c:
   try:f(c,*fixture(c));cases.append(name)
   finally:c.rollback()
 def rejected(c,a,g,p,b):
  p["disposition"]="rejected";works=records(c)["work"];prior=snapshot(c,store,a,g)
  result=mutate(store,c,a,g,"goal.definition_resolve",p)
  g2=goal(c);v=current(c,g2)
  assert v["definitionPending"] is False and v["definitionResolution"]["disposition"]=="rejected"
  assert result["activeDefinitionChanged"] is False and result["achievementProved"] is False
  assert v["definitionResolution"]["actorId"]==OWNER and v["definitionResolution"]["actorKind"]=="agent"
  assert records(c)["work"]==works and g2["adopted_definition_revision"]==g["adopted_definition_revision"] and g2["plan_revision"]==g["plan_revision"]
  accept_goal(c,g2)
  after=snapshot(c,store,a,g2);assert changed([prior],[after]) and not changed([after],[after])
 def pending(c,a,g,p,b):
  assert current(c,g)["definitionPending"] and current(c,g)["definitionResolution"] is None
  try:accept_goal(c,g)
  except ContractConflict as e:assert e.code=="GOAL_PROPOSAL_UNRESOLVED"
  else:raise AssertionError("Unresolved proposal accepted")
 case("pending_latest_proposal_fences_goal_acceptance",pending)
 case("explicit_rejection_preserves_accepted_work_and_changes_stable_cut",rejected)
 def deferred(c,a,g,p,b):
  p["disposition"]="deferred";works=records(c)["work"]
  mutate(store,c,a,g,"goal.definition_resolve",p);g=goal(c);accept_goal(c,g)
  assert current(c,g)["definitionResolution"]["disposition"]=="deferred" and records(c)["work"]==works
 case("explicit_deferment_keeps_adopted_contract_and_evidence",deferred)
 def invalid(c,a,g,p,b,field,value):
  p={**p,"disposition":"rejected",field:value}
  old=records(c)
  try:mutate(store,c,a,g,"goal.definition_resolve",p)
  except ContractConflict as e:assert e.code=="GOAL_DEFINITION_CHANGED"
  else:raise AssertionError("Stale proposal resolved")
  assert records(c)==old
 case("wrong_digest_fenced",lambda c,a,g,p,b:invalid(c,a,g,p,b,"definitionSha256","0"*64))
 case("wrong_revision_fenced",lambda c,a,g,p,b:invalid(c,a,g,p,b,"definitionRevision",999))
 def unauthorized(c,a,g,p,b):
  p["disposition"]="rejected";old=records(c)
  try:mutate(store,c,store.actor(c,OTHER),g,"goal.definition_resolve",p)
  except Denied:pass
  else:raise AssertionError("Verifier resolved manager proposal")
  assert records(c)==old
 case("independent_verifier_cannot_resolve_owner_proposal",unauthorized)
 def duplicate(c,a,g,p,b):
  p["disposition"]="rejected";mutate(store,c,a,g,"goal.definition_resolve",p);g=goal(c)
  try:mutate(store,c,a,g,"goal.definition_resolve",p)
  except ContractConflict as e:assert e.code=="GOAL_PROPOSAL_ALREADY_RESOLVED"
  else:raise AssertionError("New decision silently overwrote original resolution")
 case("new_decision_for_already_resolved_proposal_rejected",duplicate)
 def next_proposal(c,a,g,p,b):
  p["disposition"]="deferred";mutate(store,c,a,g,"goal.definition_resolve",p);g=goal(c)
  newer=copy.deepcopy(b);newer["objective"]+=" Further proposed alternative."
  mutate(store,c,a,g,"goal.definition",{"definitionRevision":g["definition_revision"],"sourceEventId":p["sourceEventId"],"definition":newer,"note":"Explicit next proposed revision."})
  g=goal(c);assert current(c,g)["definitionResolution"] is None and current(c,g)["definitionPending"]
  try:accept_goal(c,g)
  except ContractConflict as e:assert e.code=="GOAL_PROPOSAL_UNRESOLVED"
  else:raise AssertionError("Earlier disposition cleared later proposal")
 case("newer_proposal_invalidates_prior_resolution",next_proposal)
 def adopt(c,a,g,p,b):
  p["disposition"]="deferred";mutate(store,c,a,g,"goal.definition_resolve",p);g=goal(c)
  mutate(store,c,a,g,"goal.definition_adopt",p);g=goal(c)
  assert not current(c,g)["definitionPending"] and current(c,g)["definitionResolution"] is None
  assert g["adopted_definition_revision"]==p["definitionRevision"]
  try:accept_goal(c,g)
  except ContractConflict as e:assert e.code=="GOAL_STRATEGY_REQUIRED"
  else:raise AssertionError("Adoption silently accepted old work/plan")
 case("reconsidered_adoption_keeps_existing_replan_and_coverage_fences",adopt)
 def public_source(c,a,g,p,b):
  p.update(disposition="rejected",sourceEventId="ee3ea8af-dd2b-47cf-9bdf-33c4f7830aa4")
  try:mutate(store,c,a,g,"goal.definition_resolve",p)
  except Denied:pass
  else:raise AssertionError("Private different-room source expanded")
 case("goal_local_public_source_still_required",public_source)
 with store.pool.connection() as c:
  assert records(c)==before;accept_goal(c,goal(c));c.rollback()
 result={"at":time.time(),"state":"passed","cases":cases,"count":len(cases),"allOriginalCapturedRowsRestored":True,"nativeOrModelCalls":0,
  "scope":"Real completed disposable native Goal and Work with explicitly rolled-back lifecycle counterfactuals. Deterministic state/authority checks, not HTTP/native semantic proof."}
 (out/"result.json").write_text(json.dumps(result,indent=2)+"\n");print(json.dumps(result));store.close()

if __name__=="__main__":main()
