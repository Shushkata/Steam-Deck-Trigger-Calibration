"""Conservative read-only inspection of known external calibration automation."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from pathlib import Path
import re
import subprocess

KNOWN = {'trigger-cal-boot.service', 'trigger-cal-resume.service',
         'auto_trigger_cal-resume.service', 'overwrite_trigger_cal.service',
         'trigger-calibration.service', 'steamdeck-trigger-calibration.service'}
PATTERN = re.compile(r'overwrite_trigger_cal|auto_trigger_cal|trigger-autocal|trigger_set_cal', re.I)
ENABLED = {'enabled', 'enabled-runtime', 'linked', 'linked-runtime', 'alias'}


@dataclass
class ConflictReport:
    blockers: list
    warnings: list
    suspected_initial: bool
    verified: bool = True

    def as_dict(self):
        return asdict(self)


class Scanner:
    def __init__(self, home=Path('/home/deck'), run=subprocess.run,
                 system_dirs=None, proc=Path('/proc')):
        self.home, self.run, self.proc = home, run, proc
        self.custom_dirs = system_dirs is not None
        self.system_dirs = system_dirs or [Path('/etc/systemd/system'), Path('/usr/lib/systemd/system'),
                                          Path('/run/systemd/system'),Path('/etc/systemd/user'),
                                          Path('/usr/lib/systemd/user'),home / '.config/systemd/user',
                                          Path('/run/user/1000/systemd/user')]

    def scan(self):
        warnings, blockers = [], []
        verified = True
        for filename in ('overwrite_trigger_cal', 'auto_trigger_cal.sh', 'trigger-autocal.sh'):
            if (self.home / filename).exists():
                warnings.append(f'Existing external tool: {self.home / filename}. The initial reading may already be modified.')
        units = set(KNOWN)
        user_installed = set()
        try:
            for directory in self.system_dirs:
                for path in directory.rglob('*.service'):
                    if path.name in KNOWN or (path.is_file() and path.stat().st_size < 131072 and PATTERN.search(path.read_text(errors='replace'))):
                        units.add(path.name)
                        if self.custom_dirs or 'user' in directory.parts:
                            user_installed.add(path.name)
                        warnings.append(f'External calibration unit found: {path.name}.')
            for pid in self.proc.glob('[0-9]*'):
                try:
                    raw = (pid / 'cmdline').read_bytes()[:8192].replace(b'\0', b' ').decode(errors='replace')
                    if PATTERN.search(raw):
                        blockers.append(f'External calibration process PID {pid.name}. Stop it before any calibration write.')
                except (FileNotFoundError, ProcessLookupError):
                    pass
            # Query both managers. A missing Deck user bus is common in Gaming
            # Mode/root helpers. If no matching service exists on disk and the
            # process scan is clean, its absence is not a competing writer.
            for user in (False, True):
                command = ['systemctl']
                if user:
                    command += ['--user', '--machine=deck@.host']
                command += ['show', '--no-pager', '--property=Id,LoadState,ActiveState,UnitFileState', *sorted(units)]
                try:
                    result = self.run(command, capture_output=True, text=True, timeout=5, check=False)
                except (OSError,subprocess.TimeoutExpired):
                    if user and not user_installed:
                        warnings.append('User service manager unavailable; no installed external calibration units found.')
                        continue
                    raise
                blocks = [dict(line.split('=',1) for line in block.splitlines() if '=' in line)
                          for block in result.stdout.strip().split('\n\n')]
                complete = {row.get('Id') for row in blocks if row.get('LoadState')} >= units
                # systemctl can return nonzero for missing units while still
                # returning complete not-found properties. Absence of an
                # external calibration service is not a failed inspection.
                if result.returncode != 0 and not complete:
                    if user and not user_installed:
                        warnings.append('User service manager unavailable; no installed external calibration units found.')
                        continue
                    verified = False
                    warnings.append(f'Could not inspect {"user" if user else "system"} calibration services; writes are blocked.')
                    continue
                for fields in blocks:
                    if fields.get('LoadState') == 'not-found':
                        continue
                    if fields.get('ActiveState') in ('active', 'activating', 'reloading', 'deactivating') or fields.get('UnitFileState') in ENABLED:
                        blockers.append(f'{fields.get("Id", "External service")} is active or enabled. Disable it before calibration writes.')
        except (OSError, subprocess.TimeoutExpired) as exc:
            verified = False
            warnings.append(f'Conflict inspection failed ({type(exc).__name__}); writes are blocked.')
        return ConflictReport(sorted(set(blockers)), sorted(set(warnings)), bool(warnings or blockers), verified)
