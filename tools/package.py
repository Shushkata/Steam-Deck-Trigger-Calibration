"""Create and structurally verify separate Decky install and source archives."""
from __future__ import annotations
import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FOLDER = 'DeckyTriggerCalibration'
PYTHON_FILES = ('main.py','trigger_engine.py','hardware.py','hardware_worker.py',
                'calibration_service.py','storage.py','conflicts.py','manual_service.py','live_input.py','diagnostics.py')
INSTALL_ROOT = (*PYTHON_FILES,'plugin.json','package.json','README.md','LICENSE',
                'THIRD_PARTY_NOTICES.md','RELEASE.json','BUILD_MANIFEST.json')
SOURCE_ROOT = (*INSTALL_ROOT,'pnpm-lock.yaml','tsconfig.json','rollup.config.js','.gitignore')


def files(root_files, directories):
    selected = []
    for name in root_files:
        path = ROOT/name
        if not path.is_file():
            raise RuntimeError(f'Missing required release file: {name}')
        selected.append(path)
    for name in directories:
        for path in (ROOT/name).rglob('*'):
            if path.is_file() and not {'__pycache__','node_modules','.preview-output'}.intersection(path.parts) and path.suffix != '.pyc' and path.name!='preview.js':
                selected.append(path)
    return sorted(set(selected))


def make_archive(target, selected):
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for path in selected:
            member = f'{FOLDER}/{path.relative_to(ROOT).as_posix()}'
            info = zipfile.ZipInfo(member,date_time=(2026,11,15,0,0,0))
            info.create_system = 3
            info.external_attr = (0o100644 << 16)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info,path.read_bytes())


def verify_install(path):
    with zipfile.ZipFile(path) as archive:
        members = archive.namelist()
        if archive.testzip() is not None or len(members) != len(set(members)):
            raise RuntimeError('ZIP integrity failure')
        if {PurePosixPath(name).parts[0] for name in members} != {FOLDER}:
            raise RuntimeError('ZIP must contain exactly one plugin folder')
        if any('..' in PurePosixPath(name).parts or PurePosixPath(name).is_absolute() for name in members):
            raise RuntimeError('Unsafe archive path')
        if any('/tests/' in name or '/node_modules/' in name or '/__pycache__/' in name for name in members):
            raise RuntimeError('Development-only file found in installation archive')
        expected = {f'{FOLDER}/{name}' for name in (*INSTALL_ROOT,'dist/index.js','dist/index.js.map')}
        if not expected.issubset(members):
            raise RuntimeError(f'Missing install files: {expected-set(members)}')
        metadata = json.loads(archive.read(f'{FOLDER}/plugin.json'))
        package = json.loads(archive.read(f'{FOLDER}/package.json'))
        if metadata.get('flags') != ['root'] or metadata.get('api_version') != 1 or package.get('type') != 'module':
            raise RuntimeError('Incorrect current Decky metadata')
        bundle = archive.read(f'{FOLDER}/dist/index.js').decode()
        if len(bundle) < 5000 or 'export' not in bundle or 'Read Calibration' not in bundle:
            raise RuntimeError('Missing or incomplete compiled frontend')
        release = json.loads(archive.read(f'{FOLDER}/RELEASE.json'))
        manifest = json.loads(archive.read(f'{FOLDER}/BUILD_MANIFEST.json'))
        for file,digest in manifest['compiled_sha256'].items():
            if hashlib.sha256(archive.read(f'{FOLDER}/{file}')).hexdigest()!=digest:
                raise RuntimeError(f'Compiled asset checksum mismatch: {file}')
        if manifest['version'] != package['version'] or not manifest['source_map_matches']:
            raise RuntimeError('Build manifest is inconsistent')
        if release['hardware_tested'] is not False or release['general_release_write_certified'] is not False:
            raise RuntimeError('Unverified hardware must not be labelled certified')
        if release['automatic_application'] is not False or release['independent_120s_watchdog_implemented'] is not False:
            raise RuntimeError('Incorrect 0.3 automation or rollback promise')
        for name in PYTHON_FILES:
            ast.parse(archive.read(f'{FOLDER}/{name}').decode(),filename=name,feature_version=(3,10))
        if 'simulated' in archive.read(f'{FOLDER}/main.py').decode().lower():
            raise RuntimeError('Production main must not load the simulated adapter')
        return {'members':len(members),'compiled_frontend_bytes':len(bundle),
                'name':metadata['name'],'version':package['version'],
                'hardware_tested':False,'hardware_validation_apply_path':True,
                'general_release_write_certified':False,'automatic_application':False}


def build_manifest(baseline=None):
    mapping = json.loads((ROOT/'dist/index.js.map').read_text(encoding='utf-8'))
    sources = {}
    for path in sorted((ROOT/'src').rglob('*')):
        if path.suffix not in ('.ts','.tsx'):
            continue
        relative = path.relative_to(ROOT).as_posix()
        matches = [index for index,name in enumerate(mapping['sources']) if name.endswith('/'+relative)]
        if len(matches)!=1 or mapping['sourcesContent'][matches[0]]!=path.read_bytes().decode('utf-8'):
            raise RuntimeError(f'Stale frontend build or missing source map content: {relative}. Rebuild before packaging.')
        sources[relative]=hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ('package.json','pnpm-lock.yaml','rollup.config.js','tsconfig.json'):
        sources[name]=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
    unchanged = {}
    if baseline:
        with zipfile.ZipFile(baseline) as previous:
            for name in ('hardware.py','hardware_worker.py','calibration_service.py','conflicts.py',
                         'live_input.py','trigger_engine.py','src/TriggerControls.tsx','src/TriggerTest.tsx','src/index.tsx'):
                data=(ROOT/name).read_bytes()
                if previous.read(f'{FOLDER}/{name}')!=data:
                    raise RuntimeError(f'Protected hardware or UI component changed: {name}')
                unchanged[name]=hashlib.sha256(data).hexdigest()
            old_service=ast.parse(previous.read(f'{FOLDER}/manual_service.py').decode())
            new_service=ast.parse((ROOT/'manual_service.py').read_text())
            old_class=next(node for node in old_service.body if isinstance(node,ast.ClassDef) and node.name=='ManualService')
            new_class=next(node for node in new_service.body if isinstance(node,ast.ClassDef) and node.name=='ManualService')
            for function in ('apply_selected','restore','_write_verified','_authorization'):
                before=next(node for node in old_class.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name==function)
                after=next(node for node in new_class.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name==function)
                if function=='restore':
                    class KeepSelected(ast.NodeTransformer):
                        # The only intentional Restore change is retaining Selected
                        # preferences. All hardware/guard/read-back steps must match.
                        def visit_Assign(self,node):
                            if any(ast.unparse(target)=='self.selected' for target in node.targets):return None
                            return self.generic_visit(node)
                        def visit_Expr(self,node):
                            call=node.value
                            if isinstance(call,ast.Call) and ast.unparse(call.func)=='self.store.save' and call.args and isinstance(call.args[0],ast.Constant) and call.args[0].value=='preferences.json':return None
                            return self.generic_visit(node)
                    before=KeepSelected().visit(copy.deepcopy(before))
                    after=KeepSelected().visit(copy.deepcopy(after))
                if ast.dump(before)!=ast.dump(after):
                    raise RuntimeError(f'Working Apply/Restore path changed: {function}')
                unchanged[f'manual_service.py:{function}']=hashlib.sha256(ast.dump(after).encode()).hexdigest()
            old_main=ast.parse(previous.read(f'{FOLDER}/main.py').decode())
            new_main=ast.parse((ROOT/'main.py').read_text())
            for function in ('read_calibration','apply_selected','restore_default','start_input','input_state','stop_input','test_idle_input','reset_input_test','_unload','_uninstall'):
                before=next(node for node in ast.walk(old_main) if isinstance(node,ast.AsyncFunctionDef) and node.name==function)
                after=next(node for node in ast.walk(new_main) if isinstance(node,ast.AsyncFunctionDef) and node.name==function)
                if ast.dump(before)!=ast.dump(after):raise RuntimeError(f'Working RPC path changed: {function}')
                unchanged[f'main.py:{function}']=hashlib.sha256(ast.dump(after).encode()).hexdigest()
            # Confirm the native helper's read functions also remain identical.
            name='hardware_worker.py'
            old=ast.parse(previous.read(f'{FOLDER}/{name}').decode())
            new=ast.parse((ROOT/name).read_text())
            for function in ('discover','request','parse_report','read_fd','read_native','stable_read','stream_native'):
                before=next(node for node in old.body if isinstance(node,ast.FunctionDef) and node.name==function)
                after=next(node for node in new.body if isinstance(node,ast.FunctionDef) and node.name==function)
                if ast.dump(before)!=ast.dump(after):
                    raise RuntimeError(f'Working reader function changed: {function}')
                unchanged[f'{name}:{function}']=hashlib.sha256(ast.dump(after).encode()).hexdigest()
    manifest={'version':json.loads((ROOT/'package.json').read_text())['version'],
              'source_map_matches':True,'source_sha256':sources,
              'compiled_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                                 for name in ('dist/index.js','dist/index.js.map')},
              'preserved_reader_from_previous_release':unchanged,'native_steamui_verified':False,
              'hardware_write_verified_on_device':False}
    (ROOT/'BUILD_MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--baseline',type=Path)
    options = parser.parse_args()
    options.output.mkdir(parents=True,exist_ok=True)
    version = json.loads((ROOT/'package.json').read_text())['version']
    prefix = f'Trigger_Calibration_{version}'
    build_manifest(options.baseline)
    install = options.output/f'{prefix}_INSTALL.zip'
    source = options.output/f'{prefix}_SOURCE.zip'
    install_files=files(INSTALL_ROOT,('dist','licenses'))
    install_files += [ROOT/'docs/REPORT_037_BG.md',ROOT/'docs/FIRST_TEST_037_BG.md']
    make_archive(install,install_files)
    verification = verify_install(install)
    make_archive(source,files(SOURCE_ROOT,('src','tests','tools','licenses','docs','dist')))
    digests = {path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in (install,source)}
    (options.output/'SHA256SUMS.txt').write_text(''.join(f'{digest}  {name}\n' for name,digest in digests.items()),encoding='utf-8')
    (options.output/'PACKAGE_VERIFICATION.json').write_text(json.dumps(verification,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'install':str(install),'source':str(source),'verification':verification,'sha256':digests},indent=2))


if __name__ == '__main__':
    main()
