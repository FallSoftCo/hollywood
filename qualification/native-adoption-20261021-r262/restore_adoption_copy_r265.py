import hashlib,json,os,re,subprocess,time
from pathlib import Path
import psycopg
from psycopg import sql
from hollywood_backbone.store import Store
old=Path('/var/lib/lax-live-promotion-preview-20261021-r260')
root=Path('/var/lib/lax-native-adoption-proof-20261021-r265');name='harness_native_adoption_20261021_r265'
assert not root.exists();os.umask(0o077);root.mkdir(mode=0o700)
cfg=json.loads((old/'database.json').read_text());cfg['dbname']=name;cfg['application_name']='native-adoption-r265'
(root/'database.json').write_text(json.dumps(cfg)+'\n')
(root/'intent.json').write_text(json.dumps({'targetDatabase':name,'snapshotSha256':hashlib.sha256((old/'live-snapshot.dump').read_bytes()).hexdigest(),'purpose':'Fresh trial of corrected existing-authority-fence interpretation and legacy drain guard. Prior trial retained. No live restore/model/FS install.'})+'\n')
env=dict(x.split('=',1) for x in json.loads(subprocess.check_output(['docker','inspect','--format','{{json .Config.Env}}','harness-postgres'],text=True)) if '=' in x)
admin={**cfg,'dbname':'postgres','user':env['POSTGRES_USER'],'password':env['POSTGRES_PASSWORD']}
with psycopg.connect(**admin,autocommit=True) as c:
 assert c.execute('SELECT 1 FROM pg_database WHERE datname=%s',(name,)).fetchone() is None
 c.execute(sql.SQL('CREATE DATABASE {} OWNER {} TEMPLATE template0').format(sql.Identifier(name),sql.Identifier(cfg['user'])))
 c.execute(sql.SQL('REVOKE CONNECT ON DATABASE {} FROM PUBLIC').format(sql.Identifier(name)))
source=old/'restore.sql'
assert not re.findall(r'(?im)^\s*(?:(?:CREATE|ALTER|DROP)\s+DATABASE\b|(?:GRANT|REVOKE|COMMENT|SECURITY\s+LABEL)\b[^\n]*\bON\s+DATABASE\b|\\(?:connect|c)\b)[^\n]*',source.read_text())
with source.open('rb') as stream,(root/'restore.log').open('wb') as log:
 subprocess.run(['docker','exec','-i','-e','PGPASSWORD','harness-postgres','psql','--username='+env['POSTGRES_USER'],'--dbname='+name,'--single-transaction','--set=ON_ERROR_STOP=1'],env={**os.environ,'PGPASSWORD':env['POSTGRES_PASSWORD']},stdin=stream,stdout=log,stderr=subprocess.STDOUT,check=True)
s=Store(cfg);s.migrate();s.close()
(root/'restored.json').write_text(json.dumps({'at':time.time(),'database':name,'sourceSnapshot':'r260 original exported production snapshot','liveChanged':False,'modelsStarted':0})+'\n')
print(json.dumps({'database':name,'restored':True,'liveChanged':False}))
