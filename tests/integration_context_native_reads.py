"""Actual authenticated purpose read boundary; no inference or work submission."""
import argparse,json,urllib.request,urllib.error,time
from pathlib import Path
from hollywood_backbone.store import Store

p=argparse.ArgumentParser()
p.add_argument("--agent-config",type=Path,required=True)
p.add_argument("--database-config",type=Path,required=True)
p.add_argument("--evidence-output",type=Path,required=True)
args=p.parse_args()
config=json.loads(args.agent_config.read_text())
store=Store(json.loads(args.database_config.read_text()))
def counts():
 with store.pool.connection() as c:
  return {table:c.execute("SELECT count(*) AS n FROM "+table+" WHERE authority=%s",
                          (config["authorityId"],)).fetchone()["n"]
          for table in ("hb_event","hb_delivery","hb_principal")}
def request(purpose,method,path):
 data=None if method=="GET" else b"{}"
 req=urllib.request.Request(config["hollywoodUrl"]+"/hollywood/v3/"+path,
  method=method,data=data,headers={"Authorization":"Bearer "+config["tokens"][purpose],
                                  "Content-Type":"application/json"})
 try:
  with urllib.request.urlopen(req,timeout=10) as f:return f.status,json.load(f)
 except urllib.error.HTTPError as e:return e.code,json.load(e)
before=counts();checks=[]
try:
 for purpose in ("reader","writer","responder"):
  status,value=request(purpose,"GET","rooms")
  assert status==(200 if purpose=="writer" else 403),(purpose,status)
  checks.append({"purpose":purpose,"method":"GET","route":"rooms","status":status})
  status,_=request(purpose,"POST","principals")
  assert status==403,(purpose,status)
  checks.append({"purpose":purpose,"method":"POST","route":"principals","status":status})
 # The newly admitted writer GET transport cannot read another purpose receipt.
 other="agents/"+config["agentId"]+"/contexts/reader/operations/context-begin:unavailable-original"
 status,_=request("writer","GET",other)
 assert status==403,status
 checks.append({"purpose":"writer","method":"GET","route":"other-purpose-original-receipt","status":status})
 after=counts();assert after==before
 receipt={"passed":True,"at":time.time(),"checks":checks,"before":before,"after":after,
          "domainAudienceChecksRetained":True,"modelsStarted":0,"workSubmissions":0,
          "note":"Actual authenticated HTTP boundary, not native model behavior proof."}
 args.evidence_output.write_text(json.dumps(receipt,indent=2)+"\n")
 print(json.dumps(receipt))
finally:store.close()
