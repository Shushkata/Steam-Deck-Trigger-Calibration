"""Calibration model, replacing v0.1.0's interactive PTY adapter.

No upstream executable is executed or redistributed. Limits describe the wire
format, NOT a universal usable or safe trigger range.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
import errno
import re

CAUTION = 1820  # User-selected reference only; not a hardware safety boundary.
SLIDER_MAX = 1880  # Normal UI endpoint; not a firmware/protocol limit.
STEP = 20
WIRE_MAX = 65535


class CalibrationError(RuntimeError):
    pass


class InvalidCalibration(CalibrationError):
    """Well-formed calibration fields whose MAX/MIN ordering is unusable."""
    pass


def public_error(exc):
    if isinstance(exc, CalibrationError):
        return str(exc)
    if isinstance(exc, OSError):
        code = errno.errorcode.get(exc.errno, type(exc).__name__)
        return f'Storage or controller I/O failed ({code}). Preserve the backup and check the plugin log.'
    return f'Calibration operation failed ({type(exc).__name__}). Preserve the backup and check the plugin log.'


def integer(value, name, maximum=WIRE_MAX):
    if type(value) is not int or not 0 <= value <= maximum:
        raise CalibrationError(f'{name} must be an integer from 0 to {maximum}.')
    return value


@dataclass(frozen=True)
class Values:
    lmax: int
    lmin: int
    rmax: int
    rmin: int
    lneg: int = 0
    rneg: int = 0

    def __post_init__(self):
        for key, value in asdict(self).items():
            integer(value, key, 255 if key.endswith('neg') else WIRE_MAX)
        if self.lmin >= self.lmax or self.rmin >= self.rmax:
            raise InvalidCalibration('Each MAX must be greater than its corresponding MIN.')

    def as_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        if type(data) is not dict or set(data) != set(cls.__dataclass_fields__):
            raise CalibrationError('Incomplete or unexpected calibration fields.')
        return cls(**data)

    @classmethod
    def parse(cls, output):
        """Legacy text parser for migration/tests only; never used for HID reads."""
        pairs = []
        for side in ('Left', 'Right'):
            matches = re.findall(rf'Current {side} Trigger Cal:\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)', output)
            if len(matches) != 1:
                raise CalibrationError(f'Unable to read an unambiguous {side.lower()} trigger calibration.')
            pairs.extend(map(int, matches[0]))
        return cls(*pairs)

    def proposal(self, lmax, rmax):
        return Values(integer(lmax, 'LT MAX'), self.lmin,
                      integer(rmax, 'RT MAX'), self.rmin, self.lneg, self.rneg)


def check_proposal(current, proposed):
    check_manual_proposal(current, proposed)
    if max(abs(proposed.lmax-current.lmax), abs(proposed.rmax-current.rmax)) > STEP:
        raise CalibrationError('Change each MAX by at most 20 per test. Use successive confirmed tests.')


def check_manual_proposal(current, proposed):
    """Manual MAX editing: protocol validity and unchanged non-MAX fields.

    STEP is an interaction increment, not a controller limit. The legacy timed
    test retains its separate incremental policy in check_proposal.
    """
    Values.from_dict(proposed.as_dict())
    if (current.lmin, current.rmin, current.lneg, current.rneg) != (
            proposed.lmin, proposed.rmin, proposed.lneg, proposed.rneg):
        raise CalibrationError('MIN and negative-range parameters must remain unchanged.')


def slider_window(current, original, minimum, reference=None):
    """Local adjustment viewport, clipped to the uint16 format, not a safety zone."""
    anchors = [current,original]
    if reference is not None and minimum < reference <= WIRE_MAX:
        anchors.append(reference)
    return {'min': max(minimum + 1, min(anchors) - STEP),
            'max': min(WIRE_MAX, max(anchors) + STEP)}


def marker_position(value, minimum, maximum):
    if maximum <= minimum or not minimum <= value <= maximum:
        return None
    return (value - minimum) / (maximum - minimum) * 100
