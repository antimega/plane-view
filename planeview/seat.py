"""Seat -> side of the aircraft, and whether the wing is in the way.

Window letters: A is always the left window. Narrow-bodies (3-3) have F on the right; wide-bodies (3-3-3, 3-4-3) K.
Over-wing rows are for the *typical* economy layout of each type (exit rows and cabin plans vary by airline), so the
guide states them as approximate. ICAO type designators, as ADS-B reports them.
"""
from __future__ import annotations

import re

TYPES = {
    # code: (name, right window letter, over-wing rows (approx.), aisle letters left|right of)
    "A319": ("Airbus A319", "F", (7, 13)),
    "A320": ("Airbus A320", "F", (9, 16)),
    "A20N": ("Airbus A320neo", "F", (9, 16)),
    "A321": ("Airbus A321", "F", (11, 21)),
    "A21N": ("Airbus A321neo", "F", (11, 21)),
    "B737": ("Boeing 737-700", "F", (9, 16)),
    "B738": ("Boeing 737-800", "F", (11, 20)),
    "B38M": ("Boeing 737 MAX 8", "F", (11, 20)),
    "B739": ("Boeing 737-900", "F", (13, 22)),
    "B39M": ("Boeing 737 MAX 9", "F", (13, 22)),
    "E190": ("Embraer 190", "F", (10, 16)),
    "E195": ("Embraer 195", "F", (11, 18)),
    "BCS3": ("Airbus A220-300", "F", (10, 17)),
    "B772": ("Boeing 777-200", "K", None),
    "B77W": ("Boeing 777-300ER", "K", None),
    "B788": ("Boeing 787-8", "K", None),
    "B789": ("Boeing 787-9", "K", None),
    "B78X": ("Boeing 787-10", "K", None),
    "A332": ("Airbus A330-200", "K", None),
    "A333": ("Airbus A330-300", "K", None),
    "A339": ("Airbus A330-900", "K", None),
    "A359": ("Airbus A350-900", "K", None),
    "A35K": ("Airbus A350-1000", "K", None),
    "A388": ("Airbus A380", "K", None),
}


def type_name(code: str | None) -> str:
    return TYPES.get(code or "", (code or "unknown type",))[0]


def parse(seat: str | None, actype: str | None = None) -> dict:
    """{'side': 'L'|'R'|None, 'window': bool, 'row': int|None, 'wing': 'over'|'behind'|None, 'note': str}"""
    if not seat:
        return dict(side=None, window=None, row=None, wing=None, seat=None, note="no seat given: both sides")
    s = seat.strip().upper()
    if s in ("L", "LEFT"):
        return dict(side="L", window=True, row=None, wing=None, seat=None, note="left-hand window")
    if s in ("R", "RIGHT"):
        return dict(side="R", window=True, row=None, wing=None, seat=None, note="right-hand window")
    m = re.fullmatch(r"(\d{1,2})([A-L])", s)
    if not m:
        raise ValueError(f"seat '{seat}' not understood (e.g. 16A, 23F, L, R)")
    row, letter = int(m.group(1)), m.group(2)
    name, right, wing_rows = TYPES.get(actype or "", (None, "F" if letter <= "F" else "K", None))
    wide = right == "K"
    if letter == "A":
        side, window = "L", True
    elif letter == right or (not wide and letter == "F") or (wide and letter in ("K", "L")):
        side, window = "R", True
    else:
        if wide:
            side = "L" if letter in "BC" else "R" if letter in "HJ" else None
        else:
            side = "L" if letter in "BC" else "R"
        window = False
    wing = None
    if wing_rows:
        if wing_rows[0] <= row <= wing_rows[1]:
            wing = "over"
        elif wing_rows[1] < row <= wing_rows[1] + 6:
            wing = "behind"
    side_word = {"L": "left", "R": "right", None: "centre"}[side]
    note = f"seat {s}: {'window' if window else 'not a window seat'}, {side_word}-hand side"
    if wing == "over":
        note += f"; row {row} is over the wing on a typical {name or 'layout'} - the view down is partly blocked"
    elif wing == "behind":
        note += f"; row {row} is just behind the wing - the view forward and down is partly blocked"
    elif wide:
        note += "; wing rows on wide-bodies vary by airline - check the seat map"
    return dict(side=side, window=window, row=row, wing=wing, seat=s, note=note)
