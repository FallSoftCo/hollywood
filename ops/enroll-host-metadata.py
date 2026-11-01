"""Explicit operator enrollment of source-attested host metadata only.

No credential, controller, capacity sample, model or worker is created. Existing
host rows must match exactly. Native agent references remain a separate action.
"""
import argparse,json
from pathlib import Path
from uuid import UUID
from psycopg.types.json import Jsonb
from hollywood_backbone.store import Store,Conflict,Denied,json_value


def enroll(store, administrator, descriptor):
    if set(descriptor) != {'schema','nodeId','host'} or descriptor['schema'] != 'hollywood-host-reference/v1':
        raise ValueError('Explicit original host descriptor required')
    host=descriptor['host'];required={'id','authority','name','identity_fingerprint','capabilities'}
    if not isinstance(host,dict) or set(host)!=required:
        raise ValueError('Exact canonical host metadata required')
    if not isinstance(host["capabilities"],dict):
        raise ValueError("Host capabilities must be an object")
    node=str(UUID(descriptor['nodeId']))
    if str(UUID(host['id']))!=node or not isinstance(host['name'],str) or not 1<=len(host['name'])<=200:
        raise ValueError('Original node/host identity required')
    if not isinstance(host['identity_fingerprint'],str) or not 1<=len(host['identity_fingerprint'])<=256 or len(json.dumps(host['capabilities']))>8192:
        raise ValueError('Bounded source fingerprint and capabilities required')
    with store.pool.connection() as c:
        actor=store.actor(c,administrator);store.admin(actor)
        if str(actor['authority'])!=str(UUID(host['authority'])):
            raise Denied('Host belongs to another organization')
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('host-metadata:'+node,))
        old=c.execute('SELECT * FROM hb_host WHERE id=%s FOR UPDATE',(node,)).fetchone()
        if old and json_value(old)!=host:
            raise Conflict('Existing host metadata differs; explicit owner change required')
        if not old:
            c.execute('INSERT INTO hb_host VALUES(%s,%s,%s,%s,%s)',(node,host['authority'],host['name'],host['identity_fingerprint'],Jsonb(host['capabilities'])))
        return {'hostId':node,'metadataEnrolled':True,'alreadyPresent':bool(old),'controllerCreated':False,'workerCreated':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('database','installation','descriptor','receipt'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();plan=json.loads(a.installation.read_text());store=Store(json.loads(a.database.read_text()))
    try:result=enroll(store,plan['credentials']['Owner']['principalId'],json.loads(a.descriptor.read_text()))
    finally:store.close()
    a.receipt.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
if __name__=='__main__':main()
