"""0.3 workflow layered on the existing calibration/backup state machine."""
from __future__ import annotations
from collections import deque
from contextlib import nullcontext
from copy import deepcopy
import time
import uuid
from calibration_service import Service
from trigger_engine import CAUTION, SLIDER_MAX, CalibrationError, Values, integer, check_manual_proposal, public_error


class ReadbackUnavailable(CalibrationError):
    """A completed write must not be repeated because its display refresh failed."""


class ManualService(Service):
    allow_automatic_initial = False

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.selected = None
        self.selection_revision = 0
        self.last_apply = None
        self.receipt = None
        self.logs = deque(maxlen=100)
        self.last_read_at = None
        self.logs_loaded = False
        self.persist_logs = True
        self.refresh_warning = None

    def event(self,event,message):
        self.logs.append({'at':time.time(),'event':event,'message':str(message)[:500]})
        if self.logs_loaded and self.persist_logs and self.store.lease and ('error' in event or event.endswith('_verified') or event=='confirmed'):
            try:
                self.store.save('events.json',list(self.logs))
            except Exception:
                self.logs.append({'at':time.time(),'event':'log_error','message':'Unable to persist the bounded event log; current-session diagnostics remain available.'})

    def _profile_state(self,data):
        state = super()._profile_state(data)
        # Migration only: keep old confirmed records, discard automatic policy.
        if state['boot'] or state['resume']:
            state = deepcopy(state)
            state['boot'] = state['resume'] = False
            self.store.save('profiles.json',state)
        return state

    def _load_profiles(self):
        return self.store.load_configuration('profiles.json',
            {'boot':False,'resume':False,'saved':None,'lkg':None},super()._profile_state)

    def _load_operation(self):
        try:
            record = self.store.load('manual-operation.json')
            if record is not None and (type(record) is not dict or record.get('status') not in
                    {'prepared','running','verified','failed','rollback_failed'}):
                raise CalibrationError('manual-operation.json: invalid operation journal.')
            return record
        except CalibrationError:
            # Both call sites follow a successful hardware read. The same global
            # and local locks exclude surviving/orphaned independent writers.
            process = getattr(self.adapter,'active_process',None)
            if not self.store.lease or not self.current or (process and process.poll() is None):
                raise
            self._validate_initial(self.store.load('initial.json'))
            preserved = self.store.preserve('manual-operation.json')
            record = {'status':'failed','identity':self.identity,'finished_at':time.time(),
                      'error':'Old unreadable journal preserved; no new write attempted.',
                      'preserved_file':preserved}
            self.store.save('manual-operation.json',record)
            self.store.repairs.append({'file':'manual-operation.json','preserved':preserved})
            return record

    def _initial_problem(self):
        try:
            record = self.store.load('initial.json')
            if record is None:
                return ('Original Calibration is not saved.' if self.store.can_create_initial() else
                        'Original Calibration is missing. Restore the saved archive; current values cannot replace it.')
            if self.identity is not None:
                self._validate_initial(record)
            else:
                # Hardware may not have initialized yet. Recognize file damage
                # without pretending to have verified its device binding.
                Values.from_dict(record['values'])
                if type(record.get('identity')) is not str or not record['identity'] or \
                   type(record.get('suspected_modified')) is not bool or record.get('factory_verified') is not False:
                    raise CalibrationError('initial.json: invalid backup metadata.')
            return None
        except (CalibrationError,KeyError,TypeError,AttributeError) as exc:
            return 'Original Calibration is invalid. Preserve and recover the saved archive.'

    async def set_current_as_default(self, confirmed):
        if confirmed is not True:
            raise CalibrationError('Confirm saving Original Calibration first.')
        async with self.lock:
            if self.store.lease is None:
                self.store.acquire()
            process = getattr(self.adapter,'active_process',None)
            if process is not None and process.poll() is None:
                raise CalibrationError('A calibration worker is running. Wait, then retry.')
            if self.initial is not None or not self.store.can_create_initial():
                raise CalibrationError('Original Calibration was already saved. Current values cannot replace it.')
            # Read after the user's confirmation, never from selected/UI values.
            current,identity = await self._io(self.adapter.read)
            Values.from_dict(current.as_dict())
            self.identity = identity
            repeated,repeated_identity = await self._io(self.adapter.read)
            if current != repeated or identity != repeated_identity:
                raise CalibrationError('Calibration changed during reading. Default was not saved; retry Read Calibration.')
            self.conflicts = await self._io(self.scanner.scan)
            now = time.time()
            record = {'identity':identity,'values':current.as_dict(),'captured_at':now,
                      'confirmed_at':now,'confirmed_by_user':True,'factory_verified':False,
                      'suspected_modified':self.conflicts.suspected_initial,
                      'provenance':'Original Calibration explicitly confirmed by the user; factory origin is not guaranteed.'}
            self.ready = False
            self.initial = None
            self.receipt = None
            try:
                record = self.store.set_current_initial(record,confirmed=True)
                self._validate_initial(record)
                self.event('default_verified','User-confirmed current calibration saved and re-read. No hardware write.')
            except Exception as exc:
                self.error = public_error(exc)
                raise
        # Reuse the normal initialization/preflight; do not bypass other gates.
        state = await self.initialize()
        if state['ok']:
            self.message = 'Original Calibration saved.'
            state = self.status()
        return state

    def _load_selection(self):
        def validate(preferences):
            if type(preferences) is not dict or preferences.get('identity')!=self.identity:
                raise CalibrationError('Preferences belong to another controller.')
            integer(preferences['lmax'],'Selected LT MAX')
            integer(preferences['rmax'],'Selected RT MAX')
        preferences = self.store.load_configuration('preferences.json',None,validate)
        if preferences is not None:
            self.selected = {key:preferences[key] for key in ('lmax','rmax')}
        elif self.selected is None and self.current is not None:
            # Initial display only; Read never replaces an existing selection.
            self.selected = {'lmax':self.current.lmax,'rmax':self.current.rmax}

    async def auto_apply(self,*_):
        raise CalibrationError('Automatic Boot/Resume application is not supported in 0.3.0.')

    async def set_automatic(self,boot,resume):
        if boot is not False or resume is not False:
            raise CalibrationError('Automatic Boot/Resume application is not supported in 0.3.0.')
        return await super().set_automatic(False,False)

    async def initialize(self):
        if self.store.lease is None:
            self.store.acquire()
        archived = None
        try:
            archived = self.store.ensure_original_redundancy()
            if archived is not None:
                Values.from_dict(archived['values'])
        except (CalibrationError,KeyError,TypeError):
            archived = None
        result = await super().initialize()
        if self.initial is None and archived is not None:
            self.initial = archived  # Display the archive even if Current cannot be read.
        if self.current is not None:
            self.refresh_warning = None
            self.last_read_at = time.time()
            try:self._load_selection()
            except Exception as exc:self.error = public_error(exc)
        if not self.ready and self.pending and self.initial and self.current:
            try:
                self._validate_pending()
                self._validated_record(self.initial)
                self.ready = True
                self.error = 'An interrupted test requires manual recovery. Use Restore Default; no calibration was automatically applied.'
            except Exception:
                pass
        if not self.ready:
            problem = self._initial_problem()
            if problem is not None and self.current is not None:
                self.error = problem
                self.initial = archived
            self.event('read_error',self.error)
            return self.status()
        async with self.lock:
            try:
                if not self.logs_loaded:
                    try:
                        def validate_logs(logs):
                            if type(logs) is not list or len(logs)>100 or any(type(row) is not dict or set(row)!={'at','event','message'} for row in logs):
                                raise CalibrationError('Invalid event log.')
                        logs = self.store.load_configuration('events.json',[],validate_logs)
                        if logs is not None:
                            if type(logs) is not list or len(logs)>100 or any(type(row) is not dict or set(row)!={'at','event','message'} for row in logs):
                                raise CalibrationError('The saved event log is invalid; it has been preserved.')
                            self.logs.extend(logs)
                    except Exception as exc:
                        self.persist_logs = False
                        self.logs.append({'at':time.time(),'event':'log_error','message':public_error(exc)})
                    self.logs_loaded = True
                self.last_apply = self._load_operation()
                self.last_read_at = time.time()
                self.message = ''
                self.event('read','Calibration read and initial backup loaded. No profile was automatically applied.')
            except Exception as exc:
                self.error = public_error(exc)
                self.event('storage_error',self.error)
        return self.status()

    async def _read(self):
        process = getattr(self.adapter,'active_process',None)
        if process is not None and process.poll() is None:
            raise CalibrationError('The independent worker is still running. Wait before Read Calibration.')
        values = await super()._read()
        self.last_read_at = time.time()
        self.refresh_warning = None
        return values

    async def _write_verified(self, values):
        previous = self.store.load('manual-operation.json')
        previous_id = previous.get('operation_id') if previous else None
        # Identical adapter/worker invocation to 0.3.3; it waits for completion.
        await self._io(self.adapter.write,values,self.identity)
        try:
            actual = await self._read()  # One refresh after the worker, no loop.
        except Exception as exc:
            try:
                record = self.store.load('manual-operation.json')
            except CalibrationError:
                record = None
            if record and record.get('status')=='verified' and record.get('identity')==self.identity and \
               record.get('operation_id') and record['operation_id']!=previous_id and \
               record.get('mode')==getattr(self.adapter,'operation_mode',None) and \
               record.get('target')==values.as_dict() and record.get('readback')==values.as_dict():
                # This is the new worker's actual read-back, never guessed values.
                self.current = Values.from_dict(record['readback'])
                self.last_read_at = record.get('finished_at')
                self.refresh_warning = 'Use Read Calibration to refresh; the worker read-back was verified.'
                self.event('refresh_error',public_error(exc))
                return self.current
            self.refresh_warning = 'Read Calibration to verify the completed operation.'
            raise ReadbackUnavailable(self.refresh_warning) from exc
        if actual != values:
            self.refresh_warning = 'Read Calibration: fresh values differ from the requested write.'
            raise ReadbackUnavailable(self.refresh_warning)
        return actual

    async def read(self):
        previous_current,previous_initial = self.current,self.initial
        result = await super().read()
        if self.current is None and previous_current is not None:
            self.current = previous_current
            self.refresh_warning = 'Last Read only. Use Read Calibration to refresh.'
        if self.initial is None and previous_initial is not None:
            self.initial = previous_initial
        if self.ready and result['ok']:
            try:
                self.last_apply = self._load_operation()
            except CalibrationError as exc:
                self.error = public_error(exc)
            if self.current and self.last_apply and self.last_apply.get('status')=='verified' and \
               self.last_apply.get('mode')=='apply' and self.last_apply.get('identity')==self.identity and \
               self.last_apply.get('readback')==self.current.as_dict():
                self.receipt={'values':self.current.as_dict(),'at':self.last_apply.get('finished_at',time.time())}
        self.event('read' if result['ok'] else 'read_error',result.get('error') or 'Current calibration refreshed; selected values unchanged.')
        return self.status()

    def _apply_blocked_reason(self):
        if self.error:
            return self.error
        problem = self._initial_problem()
        if problem is not None:
            return problem
        if not self.ready or not self.current:
            return 'Read Calibration first.'
        if not self.initial:
            return 'initial.json: original backup is missing.'
        if self.last_apply and self.last_apply.get('status') in ('prepared','running','rollback_failed'):
            return 'Previous write needs recovery. Use Restore Default.'
        if self.pending:
            return 'Interrupted calibration test: use Restore Default.'
        if not self.conflicts or not self.conflicts.verified:
            return 'External calibration service inspection failed. See Diagnostics.'
        if self.conflicts.blockers:
            return self.conflicts.blockers[0]
        if not (getattr(self.adapter,'manual_apply_supported',False) or self.adapter.can_write):
            return self.adapter.limitation
        return None

    def _manual_available(self):
        return self._apply_blocked_reason() is None

    def _known_good_target(self,proposed):
        lkg = self.profiles['lkg']
        return bool(lkg and lkg.get('confirmed') is True and lkg.get('identity')==self.identity
                    and lkg.get('values')==proposed.as_dict())

    def _validate_proposed(self,current,proposed):
        if self._known_good_target(proposed):
            if (current.lmin,current.rmin,current.lneg,current.rneg)!=(proposed.lmin,proposed.rmin,proposed.lneg,proposed.rneg):
                raise CalibrationError('The saved tested MAX preset has incompatible MIN or negative-range parameters.')
        else:
            check_manual_proposal(current,proposed)

    def status(self):
        state = super().status()
        state.update(selected=deepcopy(self.selected),selection_revision=self.selection_revision,
                     last_read_at=self.last_read_at,manual_apply_enabled=self._manual_available(),
                     automatic_application=False,hardware_validation_build=True,
                     last_apply=deepcopy(self.last_apply),working_confirmation_available=self.receipt is not None)
        state.update(apply_blocked_reason=self._apply_blocked_reason(),configuration_repairs=list(self.store.repairs))
        state['original_backup'] = deepcopy(self.store.original_status)
        state['can_set_current_as_default'] = self.current is not None and self.initial is None and self.store.can_create_initial()
        state['current_matches_original'] = None
        original_values = None
        try:
            if self.initial is not None:
                original_values = Values.from_dict(self.initial['values']).as_dict()
        except (CalibrationError,KeyError,TypeError):
            state['initial'] = None
        if self.current and original_values:
            state['current_matches_original'] = all(self.current.as_dict()[key]==original_values[key]
                for key in ('lmax','lmin','rmax','rmin'))
        state.update(current_is_stale=self.refresh_warning is not None,refresh_warning=self.refresh_warning)
        if self.ready and self.current and original_values and self.selected:
            state['selected_matches_current'] = (self.selected['lmax'],self.selected['rmax']) == (self.current.lmax,self.current.rmax)
            anchors=[self.current.lmax,self.current.rmax,original_values['lmax'],original_values['rmax'],CAUTION]
            selected=[self.selected[key] for key in ('lmax','rmax')]
            window={'min':min(*selected,max(min(self.current.lmin,self.current.rmin)+1,min(anchors)-20)),
                    'max':max(SLIDER_MAX,*anchors,*selected)}
            state['windows'] = {'l':dict(window),'r':dict(window)}
        return state

    async def select_values(self,lmax,rmax):
        async with self.lock:
            if not self.current or self.identity is None:
                raise CalibrationError('Read Calibration before selecting values.')
            # Validation/persistence only. There is intentionally no adapter.write.
            self.current.proposal(lmax,rmax)
            record = {'identity':self.identity,'lmax':lmax,'rmax':rmax,'updated_at':time.time()}
            self.store.save('preferences.json',record)
            self.selected = {'lmax':lmax,'rmax':rmax}
            self.selection_revision += 1
            return self.status()

    def _authorization(self,mode):
        if hasattr(self.adapter,'authorized_operation'):
            return self.adapter.authorized_operation(mode)
        return nullcontext()  # Explicit test adapters only.

    async def apply_selected(self,expected,lmax,rmax,confirmed):
        if confirmed is not True:
            raise CalibrationError('Confirm the temporary hardware Apply and recovery warning before proceeding.')
        async with self.lock:
            if self.pending:
                raise CalibrationError('A timed test or interrupted operation requires recovery first.')
            if not self._manual_available():
                raise CalibrationError(self.error or 'Manual hardware Apply is unavailable. Read Calibration and resolve conflict warnings.')
            await self._scan()
            current = await self._read()
            if current != Values.from_dict(expected):
                raise CalibrationError('Current Calibration changed. Read it again before Apply.')
            proposed = current.proposal(lmax,rmax)
            self._validate_proposed(current,proposed)
            # The exact desired MAX values are preferences, independent of whether
            # the following hardware operation succeeds.
            self.store.save('preferences.json',{'identity':self.identity,'lmax':lmax,'rmax':rmax,'updated_at':time.time()})
            self.selected = {'lmax':lmax,'rmax':rmax}
            self.receipt = None
            try:
                with self._authorization('apply'):
                    self._write_capability()
                    await self._write_verified(proposed)
                self.receipt = {'values':proposed.as_dict(),'at':time.time()}
                self.last_apply = self.store.load('manual-operation.json') or {
                    'status':'verified','readback':proposed.as_dict(),'finished_at':time.time()}
                self.message = 'Calibration Applied. '+self.refresh_warning if self.refresh_warning else 'Calibration Applied'
                self.event('apply_verified',self.message)
            except ReadbackUnavailable as exc:
                self.receipt = None
                self.error = self.message = str(exc)
                self.event('refresh_error',self.message)
                return self.status()
            except BaseException as exc:
                self.last_apply = self.store.load('manual-operation.json')
                # Physical Hardware owns rollback in its independent worker. A
                # simulated adapter exercises the same before/after restoration.
                if not hasattr(self.adapter,'authorized_operation'):
                    try:
                        await self._write_verified(current)
                    except Exception:
                        self.error = 'Apply and rollback could not be verified. Restore Default is required.'
                elif self.last_apply and self.last_apply.get('rollback')=='verified' and self.last_apply.get('readback'):
                    self.current=Values.from_dict(self.last_apply['readback'])
                    self.last_read_at=self.last_apply.get('finished_at')
                else:
                    self.refresh_warning='Last Read only. Use Read Calibration or Restore Default.'
                    self.error='Current Calibration is unknown after the incomplete operation. Wait for any independent worker, then Read Calibration or Restore Default.'
                self.event('apply_error',public_error(exc))
                raise
            return self.status()

    async def confirm_working(self,expected,confirmed):
        if confirmed is not True:
            raise CalibrationError('Explicitly confirm correct behavior of BOTH triggers before updating Last Known Good.')
        async with self.lock:
            if not self.receipt:
                raise CalibrationError('A verified Apply matching Current Calibration is required before confirmation.')
            await self._scan()
            current = await self._read()
            if current != Values.from_dict(expected) or current.as_dict()!=self.receipt['values']:
                self.receipt = None
                raise CalibrationError('Current Calibration no longer matches the verified Apply. Last Known Good was not changed.')
            record = {'identity':self.identity,'values':current.as_dict(),'confirmed':True,'confirmed_at':time.time()}
            state = deepcopy(self.profiles)
            state.update(boot=False,resume=False,saved=deepcopy(record),lkg=record)
            self.store.save('profiles.json',state)
            self.profiles = state
            self.receipt = None
            self.message = 'Last Known Good updated after your explicit confirmation. No automatic application is enabled.'
            self.event('confirmed',self.message)
            return self.status()

    async def restore(self,confirmed,*,last_good=False):
        if confirmed is not True:
            raise CalibrationError('Confirm restoration before writing calibration.')
        async with self.lock:
            if self.store.lease is None:
                self.store.acquire()
            if self.identity is None:
                if hasattr(self.adapter,'identify'):
                    self.identity = await self._io(self.adapter.identify)
                else:
                    _,self.identity = await self._io(self.adapter.read)
            self._disable_auto()
            if hasattr(self.adapter,'cancel_pending'):
                await self._io(self.adapter.cancel_pending)
            await self._scan()
            self._cancel_timer()
            initial = self.store.load('initial.json')
            if initial is None:
                raise CalibrationError('initial.json: original backup is missing. Restore your saved backup.')
            self._validate_initial(initial)
            if self.initial is not None and initial != self.initial:
                raise CalibrationError('The protected initial backup changed. No guessed defaults will be written.')
            self.initial = initial
            record = self.profiles['lkg'] if last_good else initial
            if record is None or (last_good and record.get('confirmed') is not True):
                raise CalibrationError('No confirmed Last Known Good profile exists.')
            self._validated_record(record)
            # Recovery depends on the protected backup and device identity,
            # never on selected sliders or the usability of current values.
            self.ready = True
            try:
                # Unusual current values or slider positions do not select the
                # recovery target. The protected record is the only source.
                with self._authorization('restore'):
                    self._write_capability(recovery=True)
                    await self._write_verified(Values.from_dict(record['values']))
                self.store.clear_pending()
                self.pending = None
                self.receipt = None
                self.error = None
                self.last_apply = self.store.load('manual-operation.json')
                self.message = ('Last Known Good restored and verified.' if last_good else
                                'Default restored and read back.')
                if self.refresh_warning:
                    self.message += ' '+self.refresh_warning
                self.event('restore_verified',self.message)
            except ReadbackUnavailable as exc:
                self.receipt = None
                self.error = self.message = str(exc)
                self.event('refresh_error',self.message)
                return self.status()
            except BaseException as exc:
                self.event('restore_error',public_error(exc))
                self.refresh_warning='Last Read only. Use Read Calibration or Restore Default.'
                self.error = 'Restoration could not be verified. Preserve diagnostics and follow recovery instructions.'
                raise
            return self.status()
