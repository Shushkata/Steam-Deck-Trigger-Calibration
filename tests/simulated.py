"""TEST ONLY. Never loaded by main.py and excluded from the installable ZIP."""
import threading
import time
from trigger_engine import CalibrationError, Values
from conflicts import ConflictReport

INITIAL = Values(1814, 518, 1730, 506, 12, 13)
IDENTITY = 'a' * 64


class SimulatedHardware:
    can_write = True
    independent_rollback = True  # Contract injection, NOT a real watchdog test.
    automatic_events = True
    limitation = 'Simulated hardware only; no real Steam Deck validation.'

    def __init__(self):
        self.values = INITIAL
        self.identity = IDENTITY
        self.writes = []
        self.read_error = None
        self.fail_writes = False
        self.ignore_writes = False
        self.fail_reads_count = 0
        self.partial_once = False
        self.active = 0
        self.max_active = 0
        self.mutex = threading.Lock()

    def read(self):
        if self.read_error:
            raise self.read_error
        if self.fail_reads_count:
            self.fail_reads_count -= 1
            raise CalibrationError('Simulated initialization delay')
        return self.values, self.identity

    def write(self, values, identity):
        assert identity == self.identity
        with self.mutex:
            self.active += 1
            self.max_active = max(self.active, self.max_active)
        try:
            time.sleep(.001)
            self.writes.append(values)
            if self.fail_writes:
                raise CalibrationError('Simulated write failure')
            if self.partial_once:
                self.partial_once = False
                self.values = Values(values.lmax, values.lmin, self.values.rmax, self.values.rmin,
                                     values.lneg, self.values.rneg)
                raise CalibrationError('Simulated second-trigger failure')
            if not self.ignore_writes:
                self.values = values
        finally:
            with self.mutex:
                self.active -= 1


class SimulatedScanner:
    def __init__(self):
        self.report = ConflictReport([], [], False)

    def scan(self):
        return self.report
