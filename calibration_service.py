"""Shared 0.2 calibration/backup core, retained by the 0.3 ManualService.

Legacy timed-test/automatic methods are not exposed by the 0.3 plugin RPC/UI.
ManualService rejects Boot/Resume policy; no asyncio countdown is advertised as
an independent crash-proof watchdog.
"""
from __future__ import annotations
import asyncio
from copy import deepcopy
import time
import uuid
from trigger_engine import CalibrationError, Values, CAUTION, check_proposal, slider_window, public_error

TEST_SECONDS = 120


def boot_clock():
    if hasattr(time, 'CLOCK_BOOTTIME'):
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    return time.monotonic()


class Service:
    allow_automatic_initial = True  # Legacy core; manual workflow requires consent.
    def __init__(self, adapter, store, scanner, *, test_seconds=TEST_SECONDS, retry_delays=(1, 2, 4)):
        self.adapter, self.store, self.scanner = adapter, store, scanner
        self.lock = asyncio.Lock()
        self.initial = None
        self.profiles = {'boot': False, 'resume': False, 'saved': None, 'lkg': None}
        self.current = None
        self.identity = None
        self.pending = None
        self.timer = None
        self.deadline = None
        self.conflicts = None
        self.error = None
        self.message = ''
        self.test_seconds, self.retry_delays = test_seconds, retry_delays
        self.ready = False

    async def _io(self, function, *args):
        # Retain the serialization lock if an RPC is cancelled while an I/O
        # thread is still running. Never let a second write race that thread.
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    def _validated_record(self, record):
        if type(record) is not dict or record.get('identity') != self.identity:
            raise CalibrationError('Calibration profile belongs to a different controller or device.')
        Values.from_dict(record['values'])
        return record

    def _validate_initial(self, record):
        try:
            self._validated_record(record)
            if type(record.get('suspected_modified')) is not bool or record.get('factory_verified') is not False:
                raise CalibrationError('Invalid provenance')
        except (CalibrationError,KeyError,TypeError) as exc:
            raise CalibrationError('initial.json: backup is invalid or belongs to another controller. Original preserved.') from exc
        return record

    def _profile_state(self, data):
        if type(data) is not dict or set(data) != {'boot', 'resume', 'saved', 'lkg'}:
            raise CalibrationError('Saved profile metadata is invalid; writes are disabled.')
        if type(data['boot']) is not bool or type(data['resume']) is not bool:
            raise CalibrationError('Saved automatic-application flags are invalid.')
        for name in ('saved', 'lkg'):
            if data[name] is not None:
                self._validated_record(data[name])
                if data[name].get('confirmed') is not True:
                    raise CalibrationError('An unconfirmed saved profile cannot be used.')
        return data

    def _load_profiles(self):
        return self.store.load('profiles.json')

    async def initialize(self):
        if self.store.lease is None:
            self.store.acquire()
        async with self.lock:
            self.error = None
            self.ready = False
            self.current = None
            try:
                self.current, self.identity = await self._io(self.adapter.read)
                self.conflicts = await self._io(self.scanner.scan)
                self.initial = None
                self.initial = self.store.load('initial.json')
                if self.initial is None:
                    if not self.allow_automatic_initial:
                        raise CalibrationError('initial.json: backup missing. Choose Set Current as Default.')
                    if self.store.load('initial-created.json') is not None or self.store.load('profiles.json') is not None or self.store.load('pending.json') is not None:
                        raise CalibrationError('The initial backup is missing. It will not be recreated from changed values. Recover your saved backup.')
                    legacy = self.store.legacy()
                    values = self.current
                    suspect = self.conflicts.suspected_initial
                    provenance = 'Initial Calibration; factory origin is unverified.'
                    if legacy is not None:
                        old = legacy.get('factory_candidate')
                        if type(old) is not dict or set(old) != {'lmax', 'lmin', 'rmax', 'rmin'}:
                            raise CalibrationError('The v0.1.0 initial snapshot is incomplete. It has been preserved.')
                        # Preserve the earliest four values exactly. Old releases
                        # did not save the two opaque fields; do not claim they did.
                        values = Values(**old, lneg=self.current.lneg, rneg=self.current.rneg)
                        suspect = True
                        provenance = ('Imported v0.1.0 snapshot; original four values preserved. '
                                      'Negative-range fields observed during migration; restoration requires review.')
                    self.initial = {'identity': self.identity, 'values': values.as_dict(),
                                    'captured_at': time.time(), 'suspected_modified': suspect,
                                    'factory_verified': False, 'provenance': provenance}
                    self.store.create_initial(self.initial)
                self._validate_initial(self.initial)
                saved = self._load_profiles()
                if saved is not None:
                    self.profiles = self._profile_state(saved)
                self.pending = self.store.load('pending.json')
                if self.pending is not None:
                    self._validate_pending()
                    self._disable_auto()
                    if not self.adapter.can_write:
                        raise CalibrationError('An interrupted test requires recovery, but hardware writes are disabled in this release. Preserve the journal and follow recovery instructions.')
                    await self._rollback('Interrupted test restored on backend restart.')
                self.ready = True
                # No automatic profile can execute in the physical read-only build.
                if not self.adapter.can_write and (self.profiles['boot'] or self.profiles['resume']):
                    self._disable_auto()
                self.message = 'Initial calibration preserved. Factory origin is unverified.'
            except Exception as exc:
                self.error = public_error(exc)
        if self.ready and self.profiles['boot']:
            await self.auto_apply('boot')
        return self.status()

    def _validate_pending(self):
        doc = self.pending
        if type(doc) is not dict or doc.get('identity') != self.identity or type(doc.get('token')) is not str:
            raise CalibrationError('The rollback journal is invalid. Preserve it for recovery.')
        Values.from_dict(doc['previous'])
        Values.from_dict(doc['proposed'])

    async def _scan(self):
        self.conflicts = await self._io(self.scanner.scan)
        if not self.conflicts.verified or self.conflicts.blockers:
            raise CalibrationError('Calibration writes are blocked by external automation or incomplete conflict inspection. See status details.')

    def _write_capability(self, *, test=False, recovery=False):
        if not self.ready or (self.error and not recovery):
            raise CalibrationError(self.error or 'Read Calibration successfully before writing.')
        self._validate_initial(self.store.load('initial.json'))
        if self.store.load('initial.json') != self.initial:
            raise CalibrationError('The initial backup changed since launch. Writes are disabled.')
        if not self.adapter.can_write:
            raise CalibrationError(self.adapter.limitation)
        if test and not self.adapter.independent_rollback:
            raise CalibrationError('Test is disabled because an independent timed rollback is unavailable.')

    async def _read(self):
        values, identity = await self._io(self.adapter.read)
        if identity != self.identity:
            raise CalibrationError('Built-in controller identity changed. No calibration will be written.')
        self.current = values
        return values

    async def _write_verified(self, values):
        await self._io(self.adapter.write, values, self.identity)
        actual = await self._read()
        if actual != values:
            raise CalibrationError('Calibration read-back does not match the expected values.')
        return actual

    def _disable_auto(self):
        next_state = deepcopy(self.profiles)
        next_state['boot'] = next_state['resume'] = False
        self.store.save('profiles.json', next_state)
        self.profiles = next_state

    async def read(self):
        if not self.ready:
            return await self.initialize()
        async with self.lock:
            try:
                if not self.ready:
                    raise CalibrationError(self.error or 'Backend initialization has not completed. Reload the plugin after resolving the error.')
                # Revalidate protected storage even when the UI has been open.
                self._validate_initial(self.store.load('initial.json'))
                await self._read()
                self.conflicts = await self._io(self.scanner.scan)
                self.error = None
                self.message = 'Calibration read successfully.'
            except Exception as exc:
                self.current = None
                self.error = public_error(exc)
            return self.status()

    def status(self):
        writable = bool(self.ready and not self.error and self.adapter.can_write and self.current and
                        self.conflicts and self.conflicts.verified and not self.conflicts.blockers)
        original = self.initial.get('values') if type(self.initial) is dict else None
        try:
            if original is not None:
                Values.from_dict(original)
        except CalibrationError:
            original = None
        windows = None
        if self.current and original:
            windows = {side: slider_window(getattr(self.current, side+'max'), original[side+'max'],
                                           getattr(self.current, side+'min')) for side in ('l', 'r')}
        return {'ok': self.ready and self.error is None, 'error': self.error,
                'message': self.message, 'current': self.current.as_dict() if self.current else None,
                'initial': deepcopy(self.initial), 'profiles': deepcopy(self.profiles),
                'conflicts': self.conflicts.as_dict() if self.conflicts else None,
                'caution': CAUTION, 'windows': windows,
                'write_enabled': writable,
                'test_enabled': writable and self.adapter.independent_rollback,
                'auto_enabled': writable and self.adapter.independent_rollback and self.adapter.automatic_events,
                'restore_enabled': bool(writable and original and not self.initial.get('suspected_modified',True)),
                'limitation': self.adapter.limitation,
                'test': None if not self.pending or type(self.pending) is not dict else {
                    'token': self.pending.get('token'), 'proposed': self.pending.get('proposed'),
                    'expires_at': self.pending.get('expires_at'),
                    'remaining': max(0, self.deadline-boot_clock()) if self.deadline is not None else 0}}

    async def start_test(self, expected, lmax, rmax):
        async with self.lock:
            self._write_capability(test=True)
            if self.pending:
                raise CalibrationError('A test or failed rollback is already pending. Restore it before starting another.')
            await self._scan()
            previous = await self._read()
            if previous != Values.from_dict(expected):
                raise CalibrationError('Calibration changed since the preview. Read Calibration again.')
            proposed = previous.proposal(lmax, rmax)
            check_proposal(previous, proposed)
            token = uuid.uuid4().hex
            self.pending = {'token': token, 'identity': self.identity, 'previous': previous.as_dict(),
                            'proposed': proposed.as_dict(), 'expires_at': time.time()+self.test_seconds}
            try:
                self.store.save('pending.json', self.pending)
            except Exception:
                self.pending = None
                raise
            self.deadline = boot_clock() + self.test_seconds
            try:
                await self._write_verified(proposed)
            except BaseException:
                await self._rollback('Test failed; previous calibration restored.')
                raise
            self.timer = asyncio.create_task(self._countdown(token))
            self.message = 'Temporary calibration applied. Manually verify both triggers; write success is not a hardware test.'
            return self.status()

    async def _countdown(self, token):
        try:
            await asyncio.sleep(max(0, self.deadline-boot_clock()))
            async with self.lock:
                if self.pending and self.pending['token'] == token:
                    await self._rollback('Test expired. Previous calibration restored.')
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.error = f'Automatic rollback failed: {exc}. Recovery is required; the journal has been preserved.'

    def _cancel_timer(self):
        if self.timer and self.timer is not asyncio.current_task():
            self.timer.cancel()
        self.timer = None

    async def _rollback(self, message):
        if not self.pending:
            return
        self._cancel_timer()
        try:
            await self._scan()
            await self._write_verified(Values.from_dict(self.pending['previous']))
            self.store.clear_pending()
        except BaseException:
            self._disable_auto()
            self.error = 'Rollback failed. Automatic application disabled; preserve pending.json and use recovery instructions.'
            raise
        self.pending, self.deadline = None, None
        self.message = message

    async def cancel_test(self):
        async with self.lock:
            self._write_capability(recovery=True)
            await self._rollback('Test cancelled. Previous calibration restored.')
            return self.status()

    async def save_test(self, token, confirmed):
        async with self.lock:
            self._write_capability(test=True)
            if confirmed is not True:
                raise CalibrationError('Confirm zero input at rest, smooth analog response and full travel for BOTH triggers before saving.')
            if not self.pending or type(token) is not str or token != self.pending['token']:
                raise CalibrationError('A matching, active temporary test is required before saving.')
            if self.deadline is None or boot_clock() >= self.deadline:
                await self._rollback('Test expired before confirmation. Previous calibration restored.')
                raise CalibrationError('The test expired. Test again before saving.')
            await self._scan()
            try:
                proposed = Values.from_dict(self.pending['proposed'])
                if await self._read() != proposed:
                    raise CalibrationError('Calibration changed during the test. Save refused.')
                record = {'identity': self.identity, 'values': proposed.as_dict(),
                          'confirmed': True, 'confirmed_at': time.time()}
                next_state = deepcopy(self.profiles)
                next_state['saved'] = deepcopy(record)
                next_state['lkg'] = deepcopy(record)
                self.store.save('profiles.json', next_state)
                self.profiles = next_state
                self.store.clear_pending()
                self.pending, self.deadline = None, None
                self._cancel_timer()
                self.message = 'Saved after explicit manual confirmation. Last Known Good updated.'
            except BaseException:
                await self._rollback('Save failed. Previous calibration restored.')
                raise
            return self.status()

    async def restore(self, confirmed, *, last_good=False):
        if confirmed is not True:
            raise CalibrationError('Confirm restoration before writing calibration.')
        async with self.lock:
            # Persist disabling before any capability check, cancellation or write.
            self._disable_auto()
            self._write_capability(recovery=True)
            await self._scan()
            initial = self.store.load('initial.json')
            self._validated_record(initial)
            if not last_good and initial['suspected_modified']:
                raise CalibrationError('The initial backup may contain previously modified values. No guessed defaults will be written. See recovery instructions.')
            record = self.profiles['lkg'] if last_good else initial
            if record is None or (last_good and record.get('confirmed') is not True):
                raise CalibrationError('No explicitly confirmed Last Known Good profile is available.')
            self._validated_record(record)
            self._cancel_timer()
            try:
                await self._write_verified(Values.from_dict(record['values']))
                self.store.clear_pending()
                self.pending, self.deadline = None, None
                self.error = None
                self.message = ('Last Known Good restored.' if last_good else
                                'Initial calibration restored; this is not factory recalibration.') + ' Automatic application remains disabled.'
            except BaseException:
                self.error = 'Restoration could not be verified. Automatic application is disabled. Follow recovery instructions.'
                raise
            return self.status()

    async def set_automatic(self, boot, resume):
        if type(boot) is not bool or type(resume) is not bool:
            raise CalibrationError('Automatic-application settings must be true or false.')
        async with self.lock:
            if boot or resume:
                self._write_capability(test=True)
                if not self.adapter.automatic_events:
                    raise CalibrationError('Boot/Resume integration has not been verified for this hardware adapter.')
                if self.pending or not self.profiles['saved'] or not self.profiles['lkg']:
                    raise CalibrationError('Save an explicitly confirmed test before enabling automatic application.')
                await self._scan()
            next_state = deepcopy(self.profiles)
            next_state.update(boot=boot, resume=resume)
            self.store.save('profiles.json', next_state)
            self.profiles = next_state
            return self.status()

    async def auto_apply(self, reason):
        if reason not in ('boot', 'resume'):
            raise CalibrationError('Unsupported automatic calibration event.')
        async with self.lock:
            if self.pending:
                # Resume must resolve a suspended test before any saved profile.
                if reason == 'resume':
                    await self._rollback('Interrupted test restored after resume.')
                return self.status()
            if not self.profiles[reason]:
                return self.status()
            self._write_capability(test=True)
            await self._scan()
            record = self.profiles['saved']
            if not record or record.get('confirmed') is not True:
                self._disable_auto()
                raise CalibrationError('Automatic application refused an untested profile.')
            self._validated_record(record)
            previous = None
            for delay in (*self.retry_delays, None):
                try:
                    previous = await self._read()
                    break
                except CalibrationError:
                    if delay is None:
                        self._disable_auto()
                        raise
                    await asyncio.sleep(delay)
            try:
                await self._write_verified(Values.from_dict(record['values']))
                self.message = f'Saved, confirmed calibration verified after {reason}.'
            except BaseException:
                self._disable_auto()
                try:
                    await self._write_verified(previous)
                except Exception as exc:
                    self.error = f'Automatic application and restoration failed: {exc}'
                raise
            return self.status()

    async def close(self):
        try:
            async with self.lock:
                if self.pending and self.adapter.can_write:
                    await self._rollback('Test interrupted by plugin unload. Previous calibration restored.')
                self._cancel_timer()
        finally:
            self.store.close()
