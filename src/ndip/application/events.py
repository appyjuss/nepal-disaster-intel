"""Known events. V1 has one; V3 replaces this with a catalogue table."""

from __future__ import annotations

from datetime import date

from ndip.domain.event import DisasterEvent
from ndip.domain.geometry import BBox

TRISHULI_2026_08_26 = DisasterEvent(
    event_id="NPL-2026-08-26-TRISHULI",
    name="Trishuli Valley event",
    occurred_on=date(2026, 8, 26),
    aoi=BBox(west=84.85, south=27.55, east=85.45, north=28.35),
)

REGISTRY: dict[str, DisasterEvent] = {TRISHULI_2026_08_26.event_id: TRISHULI_2026_08_26}
