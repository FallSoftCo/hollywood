"""Idle disposable installed Work: actual PG projection and rollback-only lifecycle cases."""
import json,time,os
from pathlib import Path
from hollywood_backbone.store import Store,json_value
from hollywood_backbone import work_resources as candidate
assert os.environ.get('WORK_ROUTING_DISPOSABLE')=='1','Explicit idle disposable database required'
out=Path(os.environ['WORK_ROUTING_EVIDENCE']);assert not out.exists();out.mkdir(mode=0o700)
work=os.environ['WORK_ROUTING_WORK']
store=Store(json.loads(Path(os.environ['WORK_ROUTING_DATABASE']).read_text()))
try:
 with store.pool.connection() as c:
  row=c.execute("SELECT authority FROM hb_work WHERE id=%s",(work,)).fetchone();assert row
  a=str(row['authority'])
  assert not c.execute("SELECT 1 FROM hb_purpose_context x JOIN hb_principal p ON p.id=x.agent WHERE p.authority=%s AND (x.active_turn IS NOT NULL OR x.accepting) LIMIT 1",(a,)).fetchone(),'Native inference must be idle'
  initial=candidate.snapshot(c,a,work);assert initial['effectiveState']=='selected'
  row=initial['selection'];agent=str(row['principal']);workspace=str(row['workspace'])
  def originals():
   return json_value({
    'route':c.execute("SELECT * FROM hb_work_resource WHERE work=%s",(work,)).fetchone(),
    'placement':c.execute("SELECT revision,enabled,host,agent FROM hb_placement WHERE agent=%s",(agent,)).fetchone(),
    'owner':c.execute("SELECT owner,revision FROM hb_work WHERE id=%s",(work,)).fetchone(),
    'principal':c.execute("SELECT active FROM hb_principal WHERE id=%s",(agent,)).fetchone(),
    'grant':c.execute("SELECT * FROM hb_physical_agent_grant WHERE workspace=%s AND principal=%s",(workspace,agent)).fetchone(),
    'root':c.execute("SELECT * FROM hb_physical_workspace WHERE id=%s",(workspace,)).fetchone()})
  before=originals();(out/'actual-before.json').write_text(json.dumps(before,indent=2)+'\n')
  cases=[
   ('unchanged',[], 'selected','selected'),
   ('paused', [("UPDATE hb_placement SET enabled=false,revision=revision+1 WHERE agent=%s",(agent,))],'held','held'),
   ('same_host_resume', [("UPDATE hb_placement SET enabled=false,revision=revision+1 WHERE agent=%s",(agent,)),("UPDATE hb_placement SET enabled=true,revision=revision+1 WHERE agent=%s",(agent,))],'held','selected'),
   ('wrong_context',[("UPDATE hb_work_resource SET context_id=context_id||'-changed' WHERE work=%s",(work,))],'held','held'),
   ('wrong_work_revision',[("UPDATE hb_work_resource SET work_revision=work_revision+1 WHERE work=%s",(work,))],'held','held'),
   ('inactive_principal',[("UPDATE hb_principal SET active=false WHERE id=%s",(agent,))],'held','held'),
   ('write_revoked',[("UPDATE hb_physical_agent_grant SET can_write=false WHERE workspace=%s AND principal=%s",(workspace,agent))],'held','selected'),
   ('grant_revised',[("UPDATE hb_physical_agent_grant SET revision=revision+1 WHERE workspace=%s AND principal=%s",(workspace,agent))],'held','held'),
   ('root_revised',[("UPDATE hb_physical_workspace SET revision=revision+1 WHERE id=%s",(workspace,))],'held','held')]
  results=[]
  for name,updates,_old_reference,want_new in cases:
   with c.transaction(force_rollback=True):
    for query,args in updates:c.execute(query,args)
    changed=candidate.snapshot(c,a,work)
    assert changed['effectiveState']==want_new,(name,'new',changed['effectiveState'])
    if want_new=='selected':
     assert changed['selection']['revision']==initial['selection']['revision']
     assert changed['selection']['workspace']==initial['selection']['workspace']
    results.append({'case':name,'candidate':changed['effectiveState'],'sameSelectionReceipt':changed['selection']==initial['selection'] if name not in ('wrong_context','wrong_work_revision') else False,'nativeEffectsStarted':0,'rolledBack':True})
  assert originals()==before,'Counterfactual transaction did not preserve original captured rows'
  result={'at':time.time(),'state':'passed','cases':results,'actualCapturedWork':work,'sameOriginalRowsAfterCounterfactuals':True,'modelRequests':0,'nativeEffectsStarted':0,'liveBusinessStateRepaired':False,'interpretation':'Actual domain projection and PostgreSQL state with explicitly rolled-back counterfactuals; not a semantic model or native lifecycle proof.'}
  (out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'state':'passed','cases':len(results),'originalRowsPreserved':True,'modelRequests':0}))
finally:store.close()
