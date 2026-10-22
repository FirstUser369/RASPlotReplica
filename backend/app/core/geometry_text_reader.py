from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re


@dataclass
class GeometryTextStructure:
    river: str = ""
    reach: str = ""
    rs: str = ""
    type: str = ""
    deck_up: list[tuple[float, float]] = field(default_factory=list)
    deck_dn: list[tuple[float, float]] = field(default_factory=list)
    rise: float | None = None
    length: float | None = None
    us_distance: float | None = None
    invert_us: float | None = None
    invert_ds: float | None = None
    barrel_us_station: float | None = None
    barrel_ds_station: float | None = None
    name: str = ""
    source_path: str = ""
    source_line: int = 0
    culvert_records: list[str] = field(default_factory=list)
    deck_header: list[str] = field(default_factory=list)



def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip().replace(",", "")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _extract_rs(line: str) -> str:
    match = re.search(r"Type RM Length L Ch R\s*=\s*.*?,\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", line)
    if match:
        return match.group(1)
    return ""


def _extract_type(line: str) -> str:
    line = line.strip()
    if not line:
        return ""
    if "Culvert" in line:
        return "Culvert"
    if "Bridge" in line:
        return "Bridge"
    if "Weir" in line:
        return "Weir"
    return line.split()[0].title() if line.split() else ""


def parse_geometry_text_structures(path: str | Path) -> list[GeometryTextStructure]:
    file_path = Path(path)
    if not file_path.exists():
        return []

    lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    entries: list[GeometryTextStructure] = []
    i = 0
    river, reach = "", ""

    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("River Reach="):
            parts = line.split("=", 1)[1].split(",", 1)
            river, reach = (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ("", "")
        if "Type RM Length L Ch R" not in line:
            i += 1
            continue

        rs = _extract_rs(line)
        block_rs = rs
        type_line = ""
        j = i + 1
        while j < len(lines):
            candidate = lines[j].strip()
            if not candidate:
                j += 1
                continue
            if "Type RM Length L Ch R" in candidate or "Culvert=" in candidate:
                break
            type_line = candidate
            break

        deck_up: list[tuple[float, float]] = []
        deck_dn: list[tuple[float, float]] = []
        k = i + 2
        while k < len(lines):
            candidate = lines[k].strip()
            if "Type RM Length L Ch R" in candidate or candidate.startswith("River Reach="):
                break
            if "Culvert=" in candidate:
                break
            if not candidate:
                k += 1
                continue
            if re.fullmatch(r"[-+]?\d*\.?\d+\s+[-+]?\d*\.?\d+", candidate):
                try:
                    station_vals = candidate.split()
                    s0, s1 = float(station_vals[0]), float(station_vals[1])
                except ValueError:
                    k += 1
                    continue
                elev_line = None
                n = k + 1
                while n < len(lines) and not lines[n].strip():
                    n += 1
                if n < len(lines):
                    elev_line = lines[n].strip()
                    vals = re.findall(r"[-+]?(?:\d+\.\d*|\d*\.\d+|\d+)", elev_line)
                    if len(vals) >= 2:
                        e0, e1 = float(vals[0]), float(vals[1])
                        pair = [(s0, e0), (s1, e1)]
                        if not deck_up:
                            deck_up = pair
                        elif not deck_dn:
                            deck_dn = pair
                        k = n + 1
                        continue
            k += 1

        culvert_line = ""
        culvert_records = []
        deck_header = []
        for idx in range(i + 2, len(lines)):
            cand = lines[idx].strip()
            if "Type RM Length L Ch R" in cand or cand.startswith("River Reach="):
                break
            if "Culvert=" in cand:
                culvert_records.append(cand)
            if cand.startswith("Deck Dist Width") and idx + 1 < len(lines):
                deck_header = [v.strip() for v in lines[idx + 1].split(",")]
        if len(culvert_records) == 1:
            culvert_line = culvert_records[0]

        rise = None
        length = None
        invert_us = None
        invert_ds = None
        barrel_us_station = None
        barrel_ds_station = None
        name = ""
        if culvert_line:
            body = culvert_line.split("Culvert=", 1)[1].strip()
            parts = [p.strip() for p in body.split(",")]
            if len(parts) > 12:
                rise = _parse_float(parts[1])
                length = _parse_float(parts[3])
                invert_us = _parse_float(parts[9])
                barrel_us_station = _parse_float(parts[10])
                invert_ds = _parse_float(parts[11])
                barrel_ds_station = _parse_float(parts[12])
                if len(parts) > 13:
                    name = parts[13]

        entry = GeometryTextStructure(
            river=river,
            reach=reach,
            rs=block_rs,
            type=_extract_type(type_line),
            deck_up=deck_up,
            deck_dn=deck_dn,
            rise=rise,
            length=length,
            invert_us=invert_us,
            invert_ds=invert_ds,
            barrel_us_station=barrel_us_station,
            barrel_ds_station=barrel_ds_station,
            name=name, source_path=str(file_path), source_line=i+1,
            culvert_records=culvert_records, deck_header=deck_header,
        )
        entries.append(entry)
        i = max(i + 1, k)

    return entries
