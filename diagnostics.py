"""Explicit diagnostics export from an allowlist; never copies ambient logs/env."""
from __future__ import annotations
import io
import json
import os
from pathlib import Path
import re
import time
import uuid
import zipfile
from trigger_engine import CalibrationError

VERSION = '0.3.7'


def sanitize(value):
    if isinstance(value,dict):
        return {key:sanitize(item) for key,item in value.items()
                if key.lower() not in ('identity','token','operation_id','password','secret','access_token')}
    if isinstance(value,list):
        return [sanitize(item) for item in value]
    if isinstance(value,str):
        value = re.sub(r'/home/[^/\s]+','<user-home>',value)
        value = re.sub(r'(?i)(password|token|secret|authorization)\s*[:=]\s*\S+',r'\1=<redacted>',value)
        value = re.sub(r'(?i)C:[\\/]+Users[\\/]+[^\\/\s]+','<user-home>',value)
        return value
    return value


def payload(service,monitor,decky_version):
    state = service.status()
    initial = state.get('initial')
    return sanitize({
        'plugin_version':VERSION,'decky_version':decky_version or 'Unavailable',
        'device_detection':getattr(service.adapter,'detection',{'status':'Unavailable'}),
        'calibration_read_status':{'ok':state['ok'],'refresh_warning':state.get('refresh_warning'),
            'error':state['error'],
            'current_is_stale':state.get('current_is_stale'),'at':state.get('last_read_at')},
        'initial_calibration':None if type(initial) is not dict else {
            'values':initial.get('values'),'provenance':initial.get('provenance'),
            'factory_verified':initial.get('factory_verified'),
            'confirmed_by_user':initial.get('confirmed_by_user',False),
            'confirmed_at':initial.get('confirmed_at'),
            'preserved_files':initial.get('preserved_files',[]),
            'suspected_modified':initial.get('suspected_modified')},
        'current_calibration':state.get('current'),'selected_values':state.get('selected'),
        'current_matches_original':state.get('current_matches_original'),
        'original_backup_storage':state.get('original_backup'),
        'configuration_repairs':state.get('configuration_repairs'),
        'apply_blocked_reason':state.get('apply_blocked_reason'),
        'apply_result':state.get('last_apply'),'last_known_good':state.get('profiles',{}).get('lkg'),
        'trigger_test':{'available':monitor is not None,'active':bool(monitor and monitor.process and monitor.process.returncode is None),
                        'reader_error':monitor.error if monitor else 'Input monitor unavailable',
                        'measurements':monitor.measurements.snapshot() if monitor else None},
        'dependency_errors':[row for row in service.logs if 'error' in row['event']],
        'conflicting_services':state.get('conflicts'),
        'backend_error_log':list(service.logs),
        'hardware_verification':{'read':'User reports 0.2.0 on LCD: startup and numerical readings for both triggers. Personal calibration values are not distributed; full semantics are not independently established.',
                                 'write':'User confirms Read, Apply, Restore and Live Trigger Test on LCD in the last working release. The 0.3.7 backup and UI changes await device confirmation.',
                                 'input':'User reports 0.3.0 Live Trigger Test reacts to LT/RT on LCD; HID report scale is not certified Steam Input output'},
        'automatic_boot_resume_application':False})


def export_archive(document,directory,user=None):
    directory = Path(directory)
    if directory.is_symlink() or directory.parent.is_symlink():
        raise CalibrationError('Diagnostics directory must not be a symbolic link.')
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    owner = None
    if os.name=='posix' and user:
        import pwd
        owner = pwd.getpwnam(user)
        os.chown(directory,owner.pw_uid,owner.pw_gid)
        os.chmod(directory,0o700)
    filename = f'TriggerCalibration-Diagnostics-{time.strftime("%Y%m%d-%H%M%S",time.gmtime())}-{uuid.uuid4().hex[:8]}.zip'
    output = io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('diagnostics.json',json.dumps(sanitize(document),indent=2,allow_nan=False)+'\n')
        archive.writestr('README.txt','Trigger Calibration diagnostics. Exported explicitly by the user.\n'
                         'No passwords, environment variables, hostnames, controller serials, machine IDs or session tokens are collected.\n'
                         'Raw HID percentages are report-scale values; Steam Input equivalence is unverified.\n')
    path = directory/filename
    fd = os.open(path,os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(output.getvalue())
        stream.flush()
        os.fsync(stream.fileno())
        if owner is not None:
            os.fchown(stream.fileno(),owner.pw_uid,owner.pw_gid)
    return {'ok':True,'filename':filename,'path':str(path),'message':'Diagnostics exported. Share this ZIP for analysis.'}
