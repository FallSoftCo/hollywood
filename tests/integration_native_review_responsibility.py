"""Captured native admin wait refusals: actual PG rollback-only machine cases."""
import json,os,time
from pathlib import Path
from psycopg.types.json import Jsonb
from hollywood_backbone.store import Store,Denied
from hollywood_backbone.context_goal_review_frontier import snapshot
from hollywood_backbone.context_goal_dependencies import refresh
from hollywood_backbone.cooperation import review

A="8960d62b-d010-4169-a534-8e3ce6e2cb4a";G="8cd4705a-296b-5eb2-978b-cc1bb1159e91"
OWNER="57696213-e44a-4527-a5f8-891b8b308a29";VERIFIER="81f12324-8cc4-566a-acd0-55717e51593f"
OTHER="1d725607-2ec4-58e0-a910-01b10e892b57";W="629c901e-1128-5b78-b7b2-a96f699dbc9d";P="43f9549e-d392-5285-a4b3-21c765c73fb3"
def one(c,t,k):return c.execute("SELECT * FROM "+t+" WHERE id=%s",(k,)).fetchone()
def main():
 os.umask(0o077);out=Path(os.environ["REVIEW_DUTY_EVIDENCE"]);assert not out.exists();out.mkdir(mode=0o700)
 original=json.loads(Path("/var/lib/lax-review-responsibility-20261024-r325/captured-native-waiting.json").read_text())
 store=Store(json.loads(Path(os.environ["REVIEW_DUTY_DATABASE"]).read_text()));cases=[]
 def rows(c):
  return {t:c.execute(sql,(A,)).fetchall() for t,sql in {
   'goals':'SELECT * FROM hb_goal WHERE authority=%s ORDER BY id',
   'work':'SELECT * FROM hb_work WHERE authority=%s ORDER BY id',
   'delivery':'SELECT * FROM hb_delivery WHERE authority=%s ORDER BY id',
   'material':'SELECT m.* FROM hb_context_material m JOIN hb_principal p ON p.id=m.agent WHERE p.authority=%s ORDER BY m.id',
   'bindings':'SELECT b.* FROM hb_purpose_context b JOIN hb_principal p ON p.id=b.agent WHERE p.authority=%s ORDER BY b.agent,b.purpose',
  }.items()}
 try:
  with store.pool.connection() as c:before=rows(c);assert one(c,'hb_goal',G)['state']=='accepted';c.rollback()
  def fixture(c):
   c.execute("UPDATE hb_goal SET state='active',revision=%s WHERE id=%s",(original['goals'][0]['revision'],G))
   c.execute("UPDATE hb_work SET state='submitted' WHERE id=%s",(W,))
   return store.actor(c,OWNER),one(c,'hb_goal',G)
  def case(name,run):
   with store.pool.connection() as c:
    try:
     a,g=fixture(c);run(c,a,g);cases.append(name)
    finally:c.rollback()
  def delegated(c,a,g):
   f=snapshot(c,store,a,g)['reviewFrontier']
   assert f['canManage'] and f['canReview'] and not f['reviewResponsible'] and not f['actionable'],f
   review(store,c,a,g) # The existing full administrative override is retained.
   v=store.actor(c,VERIFIER);vf=snapshot(c,store,v,g)['reviewFrontier']
   assert vf['reviewResponsible'] and vf['canReview'] and vf['actionable'] and not vf['canManage']
   nonadmin={**a,'administrator':False}
   try:review(store,c,nonadmin,g)
   except Denied:pass
   else:raise AssertionError('Planner acquired verifier permission')
  case('admin_keeps_override_but_designated_verifier_owns_review_wake',delegated)
  def continuation(c,a,g):
   m=one(c,'hb_context_material',P);v={**m['value'],'goalPrerequisites':[G],'goalResults':[snapshot(c,store,a,g)]}
   c.execute("UPDATE hb_context_material SET state='waiting',value=%s WHERE id=%s",(Jsonb(v),P))
   c.execute("UPDATE hb_delivery SET state='running' WHERE id=%s",('0700ea7c-7bf0-45bc-97db-583de0452e1c',))
   waiting=one(c,'hb_context_material',P)
   refresh(c,store,a);assert one(c,'hb_context_material',P)==waiting
   c.execute("UPDATE hb_work SET state='accepted',revision=revision+1 WHERE id=%s",(W,))
   c.execute("UPDATE hb_goal SET revision=revision+1 WHERE id=%s",(G,))
   assert snapshot(c,store,a,one(c,'hb_goal',G))['reviewFrontier']['actionable']
   refresh(c,store,a);after=one(c,'hb_context_material',P)
   assert after['state']=='pending' and after['value']['resumeRevision']==waiting['value'].get('resumeRevision',0)+1
   refresh(c,store,a);assert one(c,'hb_context_material',P)==after
  case('wait_stable_until_accepted_review_then_exact_parent_resumes_once',continuation)
  def unknown(c,a,g):
   c.execute("UPDATE hb_delivery SET state='unknown' WHERE id=(SELECT delivery FROM hb_work WHERE id=%s)",(W,))
   assert snapshot(c,store,a,g)['reviewFrontier']['actionable']
   assert one(c,'hb_work',W)['state']=='submitted'
  case('unknown_effects_remain_actionable_without_acceptance_or_replay',unknown)
  def no_verifier(c,a,g):
   c.execute("UPDATE hb_goal_cooperation SET reviewer=NULL WHERE goal=%s",(G,))
   f=snapshot(c,store,a,g)['reviewFrontier'];assert f['reviewResponsible'] and f['actionable']
  case('default_manager_review_retained_when_not_independently_delegated',no_verifier)
  def observer(c,a,g):
   assert 'reviewFrontier' not in snapshot(c,store,store.actor(c,OTHER),g)
  case('unrelated_executor_gets_no_frontier',observer)
  with store.pool.connection() as c:assert rows(c)==before;c.rollback()
  result={'at':time.time(),'state':'passed','cases':cases,'originalCompletedRowsRestored':True,'nativeOrModelCalls':0,
   'scope':'Actual captured native wait state plus explicit rollback-only machine counterfactuals. No semantic/model, production or unassisted fresh-system claim.'}
  (out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
 finally:store.close()

if __name__=='__main__':main()
