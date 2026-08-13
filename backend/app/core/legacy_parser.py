"""RASPLOT v3 profile table parser (legacy input).

HEC-RAS 4.x/5.x exported text/CSV table used by RASPLOT v3. Columns:

  RIVER, REACH, RS, STRUC, LENGTHCHNL, CUMDIST, MINCHEL, LOWCHORD, HIGHCHORD,
  LABELLETTER, LABEL TEXT, PROF1, PROF2, ...

Structure rows are duplicated as "-Dn" / "-Up" rows per v3 page 35.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .hdf_reader import CrossSection, Structure, ReachGeometry


STRUCT_RE = re.compile(r"^(BRIDGE|CULVERT|INLINE WEIR|LATERAL)-?(DN|UP|Dn|Up)?\s*$",
                       re.IGNORECASE)


@dataclass
class LegacyTable:
    reaches: list[ReachGeometry]
    columns: list[str]              # header names
    rows: list[list[str]]          # raw rows (string form)

    @property
    def profile_columns(self) -> list[str]:
        return [c for c in self.columns if re.match(r"^PROF\d+$", c, re.IGNORECASE)]


def parse(path: str | Path) -> LegacyTable:
    """Parse a RASPLOT v3 profile-output-table text/CSV export."""
    path = Path(path)
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise ValueError("Empty file")
    header = [h.strip() for h in rows[0]]
    body = [[c.strip() for c in r] for r in rows[1:]]
    reaches: dict[tuple[str, str], ReachGeometry] = {}
    for r in body:
        d = dict(zip(header, r))
        river = d.get("RIVER", "")
        reach = d.get("REACH", "")
        rs = d.get("RS", "")
        struc = d.get("STRUC", "")
        key = (river, reach)
        if key not in reaches:
            reaches[key] = ReachGeometry(river=river, reach=reach, centerline=[],
                                         cross_sections=[], structures=[])
        if struc:
            st = Structure(
                river=river, reach=reach, rs=rs, type=struc, name=struc,
                upstream_distance=0.0, weir_width=0.0,
                low_chord=_f(d.get("LOWCHORD")), high_chord=_f(d.get("HIGHCHORD")),
                minchel=_f(d.get("MINCHEL")),
            )
            reaches[key].structures.append(st)
        else:
            length_chnl = _f(d.get("LENGTHCHNL")) or 0.0
            lb = 0.0
            rb = 0.0
            xs = CrossSection(
                river=river, reach=reach, rs=rs, name="",
                length_channel=length_chnl, left_bank=lb, right_bank=rb,
                minchel=_f(d.get("MINCHEL")),
            )
            reaches[key].cross_sections.append(xs)
            reaches[key].rs_to_index[rs] = len(reaches[key].cross_sections) - 1
    return LegacyTable(reaches=list(reaches.values()), columns=header, rows=body)


def _f(v: str | None) -> float | None:
    if v is None:
        return None
    v = v.strip()
    if not v or v in ("*", "-8888", "-8888.00", "-9999", "-9999.00"):
        return None
    try:
        return float(v)
    except ValueError:
        return None
