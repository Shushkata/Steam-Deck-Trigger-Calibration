"""On-demand HID report monitoring. No synthetic fallback or calibration writes."""
from __future__ import annotations
import asyncio
from collections import deque
import json
import struct
import time
import uuid
from trigger_engine import CalibrationError, public_error

REPORT_MAX = 32767  # Linux hid-steam/SDL report-axis scale, NOT Steam Input output.
LEASE_SECONDS = 5


def parse_input(data):
    if len(data) != 64 or data[:4] != b'\x01\x00\x09\x40':
        return None
    left, right = struct.unpack_from('<HH', data, 44)
    return {'left': left, 'right': right, 'sequence': struct.unpack_from('<I', data, 4)[0]}


class Measurements:
    def __init__(self):
        self.reset()

    def reset(self):
        self.latest = None
        self.count = 0
        self.minimum = [None,None]
        self.maximum = [None,None]
        self.saw_zero = [False,False]
        self.saw_range = [False,False]
        self.returned_zero = [False,False]
        self.last_sequence = None
        self.last_time = None
        self.invalid_range = False
        self.idle = None
        self.recent = deque(maxlen=512)

    def observe(self, sample, now=None):
        now = time.monotonic() if now is None else now
        if type(sample) is not dict or set(sample) != {'left','right','sequence'}:
            raise CalibrationError('Invalid input-reader sample.')
        if any(type(sample[name]) is not int or not 0 <= sample[name] <= 65535 for name in ('left','right')):
            raise CalibrationError('Invalid trigger report value.')
        if type(sample['sequence']) is not int or not 0 <= sample['sequence'] <= 0xffffffff:
            raise CalibrationError('Invalid controller report sequence.')
        if sample['sequence'] == self.last_sequence:
            return
        self.last_sequence, self.last_time = sample['sequence'], now
        self.latest = [sample['left'],sample['right']]
        self.count += 1
        for i,value in enumerate(self.latest):
            self.minimum[i] = value if self.minimum[i] is None else min(self.minimum[i],value)
            self.maximum[i] = value if self.maximum[i] is None else max(self.maximum[i],value)
            self.saw_zero[i] |= value == 0
            self.saw_range[i] |= value >= REPORT_MAX
            if self.saw_range[i] and value == 0:
                self.returned_zero[i] = True
            self.invalid_range |= value > REPORT_MAX
        self.recent.append((now,tuple(self.latest)))
        if self.idle and self.idle['state'] == 'measuring' and now <= self.idle['deadline']:
            self.idle['samples'] += 1
            for i,value in enumerate(self.latest):
                self.idle['maximum'][i] = max(self.idle['maximum'][i],value)
                old = self.idle['minimum'][i]
                self.idle['minimum'][i] = value if old is None else min(old,value)
                self.idle['nonzero_samples'][i] += int(value != 0)

    def start_idle(self, now=None):
        now = time.monotonic() if now is None else now
        self.idle = {'state':'measuring','started':now,'deadline':now+5,'samples':0,
                     'maximum':[0,0],'minimum':[None,None],'nonzero_samples':[0,0],
                     'result':None}

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        if self.idle and self.idle['state']=='measuring' and now >= self.idle['deadline']:
            self.idle['state'] = 'complete'
            # No experimentally validated noise/Steam Input threshold exists.
            # Never turn an all-zero raw report into a false successful test.
            self.idle['result'] = 'Inconclusive'
        idle = None
        if self.idle:
            idle = {key:value for key,value in self.idle.items() if key not in ('started','deadline')}
            idle['remaining'] = max(0,self.idle['deadline']-now)
            idle['reason'] = 'Raw HID reports are not verified Steam Input output; no validated unwanted-input threshold is available.'
        return {'values':self.latest,'minimum':self.minimum[:],'maximum':self.maximum[:],
                'percent':None if self.latest is None or self.invalid_range else [round(v/REPORT_MAX*100,2) for v in self.latest],
                'report_range':REPORT_MAX,'report_range_reached':self.saw_range[:],
                'zero_observed':self.saw_zero[:],'returned_to_report_zero':self.returned_zero[:],
                'full_analog_travel':'Inconclusive','samples':self.count,
                'stale':self.last_time is None or now-self.last_time > 1,
                'scale_valid':not self.invalid_range,'idle':idle,
                'semantics':'HID report scale only. Calibration mode and Steam Input equivalence are unverified.'}


class InputMonitor:
    def __init__(self, runtime, *, lease_seconds=LEASE_SECONDS):
        self.runtime = runtime
        self.lease_seconds = lease_seconds
        self.token = None
        self.process = None
        self.task = None
        self.lease_deadline = 0
        self.measurements = Measurements()
        self.error = None
        self.lock = asyncio.Lock()

    async def start(self):
        async with self.lock:
            await self._stop()
            command,environment = self.runtime()
            self.process = await asyncio.create_subprocess_exec(*command,'--input',
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,env=environment)
            self.token = uuid.uuid4().hex
            self.lease_deadline = time.monotonic()+self.lease_seconds
            self.measurements.reset()
            self.error = None
            self.task = asyncio.create_task(self._consume())
            return self.state(self.token)

    async def _consume(self):
        try:
            while time.monotonic() < self.lease_deadline:
                try:
                    line = await asyncio.wait_for(self.process.stdout.readline(),.25)
                except asyncio.TimeoutError:
                    continue
                if not line:
                    raise CalibrationError('The input reader stopped. Reopen Trigger Test to retry.')
                if len(line)>4096:
                    raise CalibrationError('Input-reader output exceeded its size limit.')
                sample = json.loads(line)
                if 'error' in sample:
                    raise CalibrationError(str(sample['error'])[:300])
                self.measurements.observe(sample)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.error = public_error(exc)
        finally:
            await self._terminate_reader()

    async def _terminate_reader(self):
        if self.process and self.process.returncode is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                await self.process.wait()
                return
            try:
                await asyncio.wait_for(self.process.wait(),1)
            except asyncio.TimeoutError:
                self.process.kill()  # Reader only; never used for an Apply worker.
                await self.process.wait()

    def state(self, token):
        if type(token) is not str or token != self.token:
            raise CalibrationError('Trigger Test session expired. Reopen Trigger Test.')
        self.lease_deadline = time.monotonic()+self.lease_seconds
        return {'ok':True,'token':token,'active':bool(self.process and self.process.returncode is None),
                'error':self.error,**self.measurements.snapshot()}

    def idle(self, token):
        self.state(token)
        if not self.process or self.process.returncode is not None:
            raise CalibrationError('Live input is unavailable; no idle success can be reported.')
        self.measurements.start_idle()
        return self.state(token)

    def reset(self, token):
        self.state(token)
        self.measurements.reset()
        return self.state(token)

    async def _stop(self):
        if self.task:
            self.task.cancel()
            await self.task
            self.task = None
        await self._terminate_reader()
        self.process = None
        self.token = None

    async def stop(self, token=None):
        async with self.lock:
            if token is None or token == self.token:
                await self._stop()
        return {'ok':True}
