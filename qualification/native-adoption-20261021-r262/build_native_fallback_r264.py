import hashlib,json,os,subprocess,time
from pathlib import Path
root=Path('/aosp/development/lax-three-context-20261018-r01')
out=root/'qualification/native-adoption-fallback-r264';target=Path('/opt/harness/native-existing-realm-20261021-r263/fallback-h81')
assert not out.exists() and not target.exists();out.mkdir(mode=0o700);(out/'tmp').mkdir()
os.environ.update(TMPDIR=str(out/'tmp'),PIP_DISABLE_PIP_VERSION_CHECK='1',PIP_CACHE_DIR=str(out/'cache'))
wheels=[p for p in (root/'qualification/native-adoption-build-r263/wheels').glob('*.whl') if not p.name.startswith('hollywood_cli-')]
old=root/'qualification/role-decision-build-r251/wheels/hollywood_cli-0.3.0.dev81-py3-none-any.whl'
assert old.is_file();wheels.append(old)
with (out/'install.log').open('wb') as log:
 subprocess.run(['python3','-m','venv',str(target)],check=True,stdout=log,stderr=subprocess.STDOUT)
 subprocess.run([str(target/'bin/python'),'-m','pip','install','--no-index','--no-deps','--no-compile',*[str(p) for p in wheels]],check=True,stdout=log,stderr=subprocess.STDOUT)
 subprocess.run([str(target/'bin/python'),'-m','pip','check'],check=True,stdout=log,stderr=subprocess.STDOUT)
probe=json.loads(subprocess.check_output([str(target/'bin/python'),'-I','-B','-c',"import json;from importlib.metadata import version;print(json.dumps([version('lax-hollywood-connector'),version('hollywood-cli')]))"],text=True))
assert probe==['0.1.0.dev116','0.3.0.dev81']
source=Path('/opt/lax-three-context-20261018-r01/venv-r258/lib/python3.12/site-packages')
dest=target/'lib/python3.12/site-packages';hashes={}
for name in ['lax_connector','hollywood_backbone']:
 for p in (source/name).glob('*'):
  if p.is_file() and p.suffix in ['.py','.sql']:
   assert p.read_bytes()==(dest/name/p.name).read_bytes()
   hashes[name+'/'+p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
result={'schema':'lax-native-compatible-fallback/v1','at':time.time(),'runtime':str(target),'versions':probe,'exactQualifiedModuleHashes':hashes,'oldHollywoodWheelSha256':hashlib.sha256(old.read_bytes()).hexdigest(),'modelsStarted':0,'servicesChanged':False,'activationPolicy':'Forward-compatible fallback only. Keep schema34 and original receipts/native contexts; pause affected execution and new agent enrollments while diagnosing. Never restore H29 or an old database snapshot after new native effects.'}
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
(root/'qualification/native-adoption-fallback-r264.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'fallback':str(target),'versions':probe,'matchedQualifiedModules':len(hashes),'modelsStarted':0}))
