import hashlib,json,os,subprocess,time
from pathlib import Path
b=Path('/aosp/development/lax-three-context-20261018-r01');out=b/'qualification/native-adoption-build-r265';target=Path('/opt/harness/native-existing-realm-20261021-r265/venv')
assert not out.exists() and not target.exists();out.mkdir(mode=0o700);(out/'tmp').mkdir();os.environ.update(TMPDIR=str(out/'tmp'),PIP_DISABLE_PIP_VERSION_CHECK='1',PIP_CACHE_DIR=str(out/'cache'))
wheels=out/'wheels';wheels.mkdir()
with (out/'build.log').open('wb') as log:
 subprocess.run(['/opt/lax-three-context-20261018-r01/build-venv/bin/python','-m','pip','wheel','--no-deps','--no-build-isolation',str(b/'hollywood'),'-w',str(wheels)],stdout=log,stderr=subprocess.STDOUT,check=True)
others=[p for p in (b/'qualification/native-adoption-build-r263/wheels').glob('*.whl') if not p.name.startswith('hollywood_cli-')]
allwheels=[*others,*wheels.glob('*.whl')]
with (out/'install.log').open('wb') as log:
 subprocess.run(['python3','-m','venv',str(target)],check=True,stdout=log,stderr=subprocess.STDOUT)
 subprocess.run([str(target/'bin/python'),'-m','pip','install','--no-index','--no-deps','--no-compile',*[str(p) for p in allwheels]],check=True,stdout=log,stderr=subprocess.STDOUT)
 subprocess.run([str(target/'bin/python'),'-m','pip','check'],check=True,stdout=log,stderr=subprocess.STDOUT)
probe=json.loads(subprocess.check_output([str(target/'bin/python'),'-I','-B','-c',"import json;from importlib.metadata import version;print(json.dumps([version('lax-hollywood-connector'),version('hollywood-cli')]))"],text=True));assert probe==['0.1.0.dev116','0.3.0.dev83']
hashes={}
for name,source in [('hollywood_backbone',b/'hollywood/hollywood_backbone'),('lax_connector',b/'lax/losangelex/connector/lax_connector')]:
 for p in source.glob('*'):
  if p.is_file() and p.suffix in ['.py','.sql']:
   assert p.read_bytes()==(target/'lib/python3.12/site-packages'/name/p.name).read_bytes()
   hashes[name+'/'+p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
result={'schema':'lax-native-existing-realm-corrected-candidate/v1','at':time.time(),'runtime':str(target),'versions':probe,'sourceModuleHashes':hashes,'wheels':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in allwheels},'modelsStarted':0,'servicesChanged':False,'supersedes':'H82 candidate never promoted; redundant actor locks removed, existing authority fence retained. Drain guards unchanged.'}
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n');(b/'qualification/native-adoption-runtime-r265.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'runtime':str(target),'versions':probe,'sourceModules':len(hashes),'servicesChanged':False}))
