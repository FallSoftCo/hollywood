"""Build a standard pure-Python zipapp from the reviewed wheel, on the build host.

Runtime dependencies stay in the existing pinned interpreter environment. No
pip/network/package installation is performed on an execution host.
"""
import argparse
import json
import tempfile
import zipapp
import zipfile
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--wheel',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
with tempfile.TemporaryDirectory(prefix='node-zipapp-',dir=str(a.output.parent)) as tmp:
 root=Path(tmp)
 with zipfile.ZipFile(a.wheel) as wheel:wheel.extractall(root)
 (root/'__main__.py').write_text('''import sys
if len(sys.argv)>1 and sys.argv[1]=="bootstrap":
    del sys.argv[1]
    from hollywood_backbone.bootstrap import main
elif len(sys.argv)>1 and sys.argv[1]=="--version":
    from importlib.metadata import version
    print(version("hollywood-cli"))
    raise SystemExit(0)
else:
    from hollywood_backbone.server import main
main()
''')
 zipapp.create_archive(root,target=a.output,compressed=True)
print(json.dumps({'artifact':a.output.name,'bytes':a.output.stat().st_size,'format':'Python standard-library zipapp','dependencyInstallationOnExecutionHost':False}))
