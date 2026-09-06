"""Synthetic installed-wheel integrity checks, no Node or operational services."""
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import venv
import zipfile
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('paired_gate', ROOT / 'ops/prepare_ui_release.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
SHA = 'a' * 40
VERSION = '0.1.0'


def wheel(directory, ui=False, version=VERSION, dependency=VERSION):
    package = 'autonomo_taxes_ui' if ui else 'autonomo_taxes'
    distribution = 'spain_autonomo_taxes_ui' if ui else 'spain_autonomo_taxes'
    name = distribution.replace('_', '-')
    metadata = f'{distribution}-{version}.dist-info'
    files = {f'{package}/__init__.py': b'', f'{package}/cli.py': b'def main(): return 0\n'}
    if ui:
        resources = {'index.html': b'<script src="/ui-assets/a.js"></script>', 'ui-assets/a.js': b'export {};'}
        manifest = {'contract': 1, 'source_sha': SHA, 'source_dirty': False, 'lock_sha256': hashlib.sha256(b'{}').hexdigest(), 'node':'24.20.0','npm':'11.19.0','files': {key: hashlib.sha256(value).hexdigest() for key,value in resources.items()}}
        files.update({f'{package}/dist/{key}': value for key,value in resources.items()})
        files[f'{package}/dist/build-manifest.json'] = json.dumps(manifest).encode()
    else:
        files[f'{package}/ui_assets.py'] = (ROOT / 'src/autonomo_taxes/ui_assets.py').read_bytes()
        files[f'{package}/ledger_db.py'] = b'LATEST_SCHEMA_VERSION = 1\n'
    required = f'Requires-Dist: spain-autonomo-taxes=={dependency}\n' if ui else ''
    files[f'{metadata}/METADATA'] = f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{required}'.encode()
    files[f'{metadata}/WHEEL'] = b'Wheel-Version: 1.0\nGenerator: synthetic-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n'
    files[f'{metadata}/entry_points.txt'] = f'[console_scripts]\nautonomo-{"web" if ui else "tax"} = {package}.cli:main\n'.encode()
    record = io.StringIO(); writer = csv.writer(record)
    for path in [*files, f'{metadata}/RECORD']:
        writer.writerow([path,'',''])
    files[f'{metadata}/RECORD'] = record.getvalue().encode()
    path = directory / f'{distribution}-{version}-py3-none-any.whl'
    with zipfile.ZipFile(path,'w') as archive:
        for name,value in files.items():archive.writestr(name,value)
    return path


def project(root):
    (root/'packages/ui').mkdir(parents=True)
    for path in [root/'pyproject.toml',root/'packages/ui/pyproject.toml']:
        path.write_text('[project]\nversion="0.1.0"\n')
    (root/'package-lock.json').write_text('{}')
    (root/'.release-sha').write_text(SHA)
    (root/'.schema-version').write_text('1')
    directory=root/'.ui-wheels';directory.mkdir()
    return directory


@pytest.mark.parametrize('version,dependency',[('0.2.0',VERSION),(VERSION,'0.2.0')])
def test_pair_rejects_version_or_dependency_mismatch(tmp_path,version,dependency):
    directory=project(tmp_path);wheel(directory);wheel(directory,True,version,dependency)
    with pytest.raises(RuntimeError):gate.wheel_artifacts(tmp_path)


def test_pair_receipt_checks_installed_core_ui_launchers_and_retained_wheels(tmp_path,monkeypatch):
    directory=project(tmp_path);core=wheel(directory);ui=wheel(directory,True)
    environment=tmp_path/'.venv';venv.EnvBuilder(with_pip=True).create(environment)
    python=environment/'bin/python'
    subprocess.run([str(python),'-m','pip','install','--no-index','--no-deps',str(core),str(ui)],check=True,capture_output=True)
    monkeypatch.setattr(gate,'verify_tree',lambda *_:None)
    monkeypatch.setattr(gate,'contract',lambda *_:2)
    monkeypatch.setattr(gate,'expected_tools',lambda *_:{'node':'24.20.0','npm':'11.19.0'})
    monkeypatch.setattr(gate,'tools',lambda *_:pytest.fail('reuse invoked Node'))
    gate.write_receipt(tmp_path,SHA,tmp_path)
    gate.verify(tmp_path,SHA,tmp_path)
    receipt=json.loads((tmp_path/'.ui-install-complete.json').read_text())
    assert receipt['release_contract']==2 and len(receipt['wheels'])==2
    package=Path(subprocess.check_output([str(python),'-I','-c','import autonomo_taxes;print(autonomo_taxes.__file__)'],text=True).strip()).parent
    targets=[package/'ledger_db.py',package.parent/'autonomo_taxes_ui/dist/ui-assets/a.js',environment/'bin/autonomo-web',core]
    for target in targets:
        saved=target.read_bytes();target.write_bytes(saved+b'\n#tampered')
        with pytest.raises((RuntimeError,subprocess.CalledProcessError)):gate.verify(tmp_path,SHA,tmp_path)
        target.write_bytes(saved)
    gate.main(['preflight',str(tmp_path),SHA,str(tmp_path)])
    (tmp_path/'.ui-install-complete.json').unlink()
    with pytest.raises(RuntimeError,match='Incomplete'):gate.main(['preflight',str(tmp_path),SHA,str(tmp_path)])
