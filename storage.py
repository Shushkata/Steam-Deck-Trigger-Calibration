"""Atomic, checksummed persistence outside the installation directory."""
from __future__ import annotations
import hashlib
import json
import os
import stat
from pathlib import Path
import tempfile
import uuid
import time
from trigger_engine import CalibrationError, Values

FILES = {'initial.json', 'initial-created.json', 'profiles.json', 'pending.json', 'legacy-v010.json',
         'preferences.json', 'manual-operation.json', 'events.json', 'initial-reserve.json'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class Store:
    def __init__(self, root, legacy_roots=(), global_lock=None):
        self.root = Path(root)
        if self.root.is_symlink():
            raise CalibrationError('The calibration storage directory must not be a symbolic link.')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Decky mkdir_as_user recursively chowns existing settings to the host
        # user on every plugin load, including root plugins. Remember that UID.
        self.previous_owner = self.root.stat().st_uid
        if os.name == 'posix':
            os.chown(self.root, os.geteuid(), os.getegid())
            os.chmod(self.root, 0o700)
        self.lease = None
        self.legacy_roots = [Path(path) for path in legacy_roots if Path(path) != self.root]
        self.global_lock = Path(global_lock) if global_lock else None
        self.repairs = []
        # Separate directory and independently written file, never a hard link
        # to the primary. Both live in Decky's persistent settings, not plugins.
        self.reserve_root = self.root / 'original-recovery'
        if self.reserve_root.is_symlink():
            raise CalibrationError('Original backup recovery directory must not be a symbolic link.')
        self.reserve_root.mkdir(exist_ok=True,mode=0o700)
        self.reserve_previous_owner = self.reserve_root.stat().st_uid
        if os.name == 'posix':
            os.chown(self.reserve_root,os.geteuid(),os.getegid())
            os.chmod(self.reserve_root,0o700)
        self.original_status = {}

    def acquire(self):
        """Hold a cross-process lock for the entire backend lifetime."""
        acquired = []
        try:
            paths = ([self.global_lock] if self.global_lock else []) + [self.root / '.writer.lock']
            for path in paths:
                fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                acquired.append(fd)
                if os.name == 'posix':
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:  # Windows development tests; the device uses flock.
                    import msvcrt
                    if os.fstat(fd).st_size == 0:
                        os.write(fd, b'\0')
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            for fd in acquired:
                os.close(fd)
            raise CalibrationError('Another Trigger Calibration worker owns the controller lock. Wait; after an update restart Decky Loader or the Deck.') from exc
        self.lease = acquired

    def close(self):
        if self.lease is not None:
            for fd in self.lease:
                os.close(fd)
            self.lease = None

    def _path(self, name):
        if name not in FILES:
            raise CalibrationError('Unsupported storage operation.')
        path = self.reserve_root / 'initial.json' if name == 'initial-reserve.json' else self.root / name
        if path.is_symlink():
            raise CalibrationError(f'{name} must not be a symbolic link.')
        return path

    def load(self, name):
        if name == 'initial.json':
            return self.load_original()
        return self._load_file(name)

    def _load_file(self, name):
        path = self._path(name)
        try:
            metadata = path.stat()
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError('State path is not a regular file')
            if metadata.st_size > 131072:
                raise ValueError('Oversized state file')
            doc = json.loads(path.read_text(encoding='utf-8'))
            if set(doc) != {'schema', 'payload', 'sha256'} or doc['schema'] != 1:
                raise ValueError('Unknown state schema')
            if hashlib.sha256(canonical(doc['payload'])).hexdigest() != doc['sha256']:
                raise ValueError('Checksum mismatch')
            if os.name == 'posix' and (metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077):
                previous_owner = self.reserve_previous_owner if name == 'initial-reserve.json' else self.previous_owner
                if self.lease is None or metadata.st_uid not in {previous_owner,os.geteuid()}:
                    raise ValueError('Insecure state-file owner or permissions')
                # Under the existing writer lock, reclaim a validated file from
                # Decky's host UID. This changes metadata only, including for
                # initial.json: its bytes and checksum remain exactly intact.
                os.chown(path,os.geteuid(),os.getegid())
                os.chmod(path,0o600)
            return doc['payload']
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            reason = 'Invalid JSON' if isinstance(exc,json.JSONDecodeError) else str(exc)[:100]
            raise CalibrationError(f'{name}: {reason}. Original preserved.') from exc

    def load_original(self):
        """Read-only arbitration: damaged/history files are NEVER repaired in place."""
        valid = {};errors = {};missing = []
        for name in ('initial.json','initial-reserve.json'):
            try:
                record = self._load_file(name)
                if record is None:
                    missing.append(name);continue
                if type(record) is not dict or type(record.get('identity')) is not str or not record['identity']:
                    raise CalibrationError(f'{name}: invalid original controller identity.')
                Values.from_dict(record['values'])
                if 'factory_verified' in record and record['factory_verified'] is not False:
                    raise CalibrationError(f'{name}: invalid original provenance.')
                if 'suspected_modified' in record and type(record['suspected_modified']) is not bool:
                    raise CalibrationError(f'{name}: invalid original provenance.')
                valid[name] = record
            except (CalibrationError,KeyError,TypeError) as exc:
                errors[name] = str(exc)
        self.original_status = {'primary':str(self.root/'initial.json'),
            'reserve':str(self.reserve_root/'initial.json'),'valid_copies':len(valid),
            'errors':errors,'missing':missing,'source':None,'locked':bool(valid)}
        if len(valid)==2 and any(valid['initial.json'][key]!=valid['initial-reserve.json'][key]
                                 for key in ('identity','values')):
            self.original_status.update(locked=True,conflict=True)
            raise CalibrationError('Original Calibration copies disagree. Both archives are preserved; recover the correct historical backup before writing.')
        if valid:
            source = 'initial.json' if 'initial.json' in valid else 'initial-reserve.json'
            self.original_status['source'] = source
            return valid[source]
        if errors:
            raise CalibrationError('Original Calibration backups are invalid. Both files are preserved; recover an exported backup. '+next(iter(errors.values())))
        return None

    def ensure_original_redundancy(self):
        """Upgrade a valid older primary by copying its stored payload, never HID."""
        record = self.load_original()
        if record is not None and self.original_status['source']=='initial.json' and 'initial-reserve.json' in self.original_status['missing']:
            self.save('initial-reserve.json',record,exclusive=True)
            if self.load_original() != record:
                raise CalibrationError('Original recovery-copy verification failed.')
        return record

    def preserve(self, name):
        """Preserve exact bytes before an explicit reset; never handles backups."""
        if name not in {'profiles.json','preferences.json','events.json','manual-operation.json'}:
            raise CalibrationError(f'{name} cannot be reset automatically.')
        return self._preserve_file(name)

    def _preserve_file(self, name):
        source = self._path(name)
        destination = self.root / f'{name}.preserved-{uuid.uuid4().hex}'
        import shutil
        with source.open('rb') as original, destination.open('xb') as copy:
            if os.name == 'posix':
                os.fchmod(copy.fileno(),0o600)
            shutil.copyfileobj(original,copy)
            copy.flush()
            os.fsync(copy.fileno())
        self._sync()
        return destination.name

    def set_current_initial(self, payload, *, confirmed):
        """Create Original Calibration once; never replace an existing archive."""
        if confirmed is not True:
            raise CalibrationError('Confirm saving Original Calibration first.')
        if not self.can_create_initial():
            raise CalibrationError('Original Calibration was already saved. Restore its archive; current values cannot replace it.')
        self.create_initial(payload)
        if self.load('initial.json') != payload:
            raise CalibrationError('Original Calibration verification failed. Preserve the archive.')
        return payload

    def can_create_initial(self):
        # The sentinel survives a missing/damaged archive and interrupted first
        # capture. Existing history must not turn modified Current into Original.
        names=('initial-created.json','manual-operation.json','pending.json','legacy-v010.json')
        if any((self.root/name).exists() or (self.root/name).is_symlink() for name in names):
            return False
        for root in (self.root,*self.legacy_roots):
            if any(root.glob('initial.json*')) or any(root.glob('calibration.json*')):
                return False
        if any(self.reserve_root.glob('initial.json*')):
            return False
        return True

    def load_configuration(self, name, default, validate=None):
        # Operation journals and immutable initial snapshots are NOT ordinary
        # configuration. They are deliberately excluded from this repair path.
        if name not in {'profiles.json','preferences.json','events.json'}:
            raise CalibrationError('Only optional configuration can be recovered.')
        try:
            value = self.load(name)
            if value is not None and validate:
                validate(value)
            return default if value is None else value
        except (CalibrationError,ValueError,KeyError,TypeError):
            preserved = self.preserve(name)  # If preservation fails, do not reset.
            self.save(name,default)
            self.repairs.append({'file':name,'preserved':preserved})
            return default

    def _sync(self, directory=None):
        if os.name == 'posix':
            fd = os.open(directory or self.root, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def save(self, name, payload, *, exclusive=False):
        path = self._path(name)
        if name in {'initial.json','initial-created.json','initial-reserve.json'}:
            exclusive = True  # Original Calibration is create-only at every call site.
        envelope = {'schema': 1, 'payload': payload,
                    'sha256': hashlib.sha256(canonical(payload)).hexdigest()}
        fd, temporary = tempfile.mkstemp(prefix='.atomic-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(canonical(envelope))
                stream.flush()
                os.fsync(stream.fileno())
            if exclusive:
                os.link(temporary, path)  # Atomic create-if-absent of a fully synced file.
            else:
                os.replace(temporary, path)
            self._sync(path.parent)
        except FileExistsError as exc:
            raise CalibrationError(f'{name} already exists and will never be replaced.') from exc
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def clear_pending(self):
        self._path('pending.json').unlink(missing_ok=True)
        self._sync()

    def create_initial(self, payload):
        # Crash between sentinel and backup requires recovery, never adoption of
        # a potentially altered baseline on the next launch.
        if any(self._path(name).exists() or self._path(name).is_symlink()
               for name in ('initial-created.json','initial.json','initial-reserve.json')):
            raise CalibrationError('Original Calibration already exists or has history; it cannot be replaced.')
        self.save('initial-created.json', {'identity': payload['identity']}, exclusive=True)
        self.save('initial.json', payload, exclusive=True)
        self.save('initial-reserve.json', payload, exclusive=True)
        if self.load_original()!=payload or self.original_status['valid_copies']!=2:
            raise CalibrationError('Original Calibration copies could not both be verified.')

    def export_original(self, directory, user=None):
        record = self.load_original()
        if record is None:
            raise CalibrationError('Original Calibration backup is missing. Nothing can be exported.')
        directory = Path(directory)
        if directory.is_symlink() or directory.parent.is_symlink():
            raise CalibrationError('Original Backup export directory must not be a symbolic link.')
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        owner = None
        if os.name=='posix' and user:
            import pwd
            owner = pwd.getpwnam(user)
            os.chown(directory,owner.pw_uid,owner.pw_gid)
            os.chmod(directory,0o700)
        envelope = {'schema':1,'payload':record,'sha256':hashlib.sha256(canonical(record)).hexdigest()}
        name = f'TriggerCalibration-Original-{time.strftime("%Y%m%d-%H%M%S",time.gmtime())}-{uuid.uuid4().hex[:8]}.json'
        path = directory/name
        fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'wb') as stream:
            stream.write(canonical(envelope));stream.flush();os.fsync(stream.fileno())
            if owner is not None:os.fchown(stream.fileno(),owner.pw_uid,owner.pw_gid)
        self._sync(directory)
        if json.loads(path.read_text(encoding='utf-8'))!=envelope:
            raise CalibrationError('Exported Original Backup verification failed.')
        return {'ok':True,'path':str(path),'message':'Original Backup exported. Copy this JSON to another device.'}

    def legacy(self):
        path = self.root / 'calibration.json'
        if not path.exists():
            candidates = [root / 'calibration.json' for root in self.legacy_roots
                          if (root / 'calibration.json').exists()]
            if len(candidates) > 1:
                raise CalibrationError('Multiple v0.1.0 backups were found. Preserve them and identify the correct initial snapshot before continuing.')
            if not candidates:
                return None
            path = candidates[0]
        if path.is_symlink() or path.stat().st_size > 131072:
            raise CalibrationError('The v0.1.0 snapshot is invalid; preserve it for recovery.')
        try:
            doc = json.loads(path.read_text(encoding='utf-8'))
            if type(doc) is not dict:
                raise ValueError()
        except (ValueError, OSError) as exc:
            raise CalibrationError('The v0.1.0 snapshot is corrupt; it will not be overwritten.') from exc
        if self.load('legacy-v010.json') is None:
            self.save('legacy-v010.json', doc, exclusive=True)
        return doc
