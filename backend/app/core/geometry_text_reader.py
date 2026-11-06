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
    low_chord_up: list[tuple[float, float]] = field(default_factory=list)
    low_chord_dn: list[tuple[float, float]] = field(default_factory=list)
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
    culvert_groups: list[dict] = field(default_factory=list)
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


def _fixed_width_values(lines: list[str], start: int, count: int) -> tuple[list[float], int]:
    """Read one HEC-RAS array whose fields are eight characters wide.

    Geometry arrays occupy a known number of physical records (ten values per
    record).  A blank record is an absent optional chord array, not whitespace
    that may be skipped into the next array.
    """
    values: list[float] = []
    index = start
    records = (count + 9) // 10
    for _ in range(records):
        if index >= len(lines):
            break
        raw = lines[index]
        index += 1
        if not raw.strip():
            continue
        fields = [raw[pos:pos + 8].strip() for pos in range(0, len(raw), 8)]
        parsed = [_parse_float(field) for field in fields if field]
        if not parsed or any(value is None for value in parsed):
            return [], index
        values.extend(float(value) for value in parsed if value is not None)
    return (values[:count] if len(values) >= count else []), index


def _parse_culvert_group(lines: list[str], index: int) -> tuple[dict | None, int]:
    raw = lines[index].strip()
    multiple = raw.startswith("Multiple Barrel Culv=")
    prefix = "Multiple Barrel Culv=" if multiple else "Culvert="
    if not raw.startswith(prefix):
        return None, index + 1
    parts = [part.strip() for part in raw.split(prefix, 1)[1].split(",")]
    try:
        if multiple:
            if len(parts) < 15:
                return None, index + 1
            barrels = int(float(parts[11]))
            stations: list[float] = []
            next_index = index + 1
            while next_index < len(lines) and len(stations) < barrels * 2:
                candidate = lines[next_index].strip()
                if not candidate or candidate.startswith(("BC ", "Culvert Bottom", "Type RM", "River Reach=")):
                    break
                parsed = [_parse_float(value) for value in candidate.split()]
                if not parsed or any(value is None for value in parsed):
                    break
                stations.extend(float(value) for value in parsed if value is not None)
                next_index += 1
            if len(stations) != barrels * 2:
                return None, next_index
            barrel_stations = [(stations[i], stations[i + 1]) for i in range(0, len(stations), 2)]
            group = {
                "record_kind": "MULTIPLE_BARREL", "shape_code": _parse_float(parts[0]),
                "rise": _parse_float(parts[1]), "span": _parse_float(parts[2]),
                "length": _parse_float(parts[3]),
                "invert_us": _parse_float(parts[9]), "invert_ds": _parse_float(parts[10]),
                "barrels": barrels, "name": parts[12], "us_distance": _parse_float(parts[14]),
                "barrel_stations": barrel_stations, "source_line": index + 1, "raw": raw,
            }
            return group, next_index
        if len(parts) < 16:
            return None, index + 1
        group = {
            "record_kind": "SINGLE_BARREL", "shape_code": _parse_float(parts[0]),
            "rise": _parse_float(parts[1]), "span": _parse_float(parts[2]),
            "length": _parse_float(parts[3]),
            "invert_us": _parse_float(parts[9]), "invert_ds": _parse_float(parts[11]),
            "barrels": 1, "name": parts[13], "us_distance": _parse_float(parts[15]),
            "barrel_stations": [(_parse_float(parts[10]), _parse_float(parts[12]))],
            "source_line": index + 1, "raw": raw,
        }
        return group, index + 1
    except (ValueError, TypeError):
        return None, index + 1


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
        low_chord_up: list[tuple[float, float]] = []
        low_chord_dn: list[tuple[float, float]] = []
        deck_header: list[str] = []
        deck_header_index = None
        k = i + 2
        while k < len(lines):
            candidate = lines[k].strip()
            if "Type RM Length L Ch R" in candidate or candidate.startswith("River Reach="):
                break
            if candidate.startswith("Deck Dist Width") and k + 1 < len(lines):
                deck_header = [value.strip() for value in lines[k + 1].split(",")]
                deck_header_index = k + 1
            k += 1

        if deck_header_index is not None and len(deck_header) >= 6:
            up_count = int(_parse_float(deck_header[4]) or 0)
            dn_count = int(_parse_float(deck_header[5]) or 0)
            cursor = deck_header_index + 1
            # HEC-RAS geometry text writes three arrays for each face:
            # station, deck/roadway (high chord), then low chord.  Consuming
            # only four arrays swaps the downstream axes on authentic files.
            up_stations, cursor = _fixed_width_values(lines, cursor, up_count)
            up_elevations, cursor = _fixed_width_values(lines, cursor, up_count)
            up_low_elevations, cursor = _fixed_width_values(lines, cursor, up_count)
            dn_stations, cursor = _fixed_width_values(lines, cursor, dn_count)
            dn_elevations, cursor = _fixed_width_values(lines, cursor, dn_count)
            dn_low_elevations, cursor = _fixed_width_values(lines, cursor, dn_count)
            if len(up_stations) == len(up_elevations) == up_count:
                deck_up = list(zip(up_stations, up_elevations))
            if len(up_stations) == len(up_low_elevations) == up_count:
                low_chord_up = list(zip(up_stations, up_low_elevations))
            if len(dn_stations) == len(dn_elevations) == dn_count:
                deck_dn = list(zip(dn_stations, dn_elevations))
            if len(dn_stations) == len(dn_low_elevations) == dn_count:
                low_chord_dn = list(zip(dn_stations, dn_low_elevations))

        culvert_records = []
        culvert_groups = []
        for idx in range(i + 2, len(lines)):
            cand = lines[idx].strip()
            if "Type RM Length L Ch R" in cand or cand.startswith("River Reach="):
                break
            if cand.startswith(("Culvert=", "Multiple Barrel Culv=")):
                culvert_records.append(cand)
                group, _ = _parse_culvert_group(lines, idx)
                if group is not None:
                    culvert_groups.append(group)

        rise = None
        length = None
        invert_us = None
        invert_ds = None
        barrel_us_station = None
        barrel_ds_station = None
        name = ""
        if len(culvert_groups) == 1:
            group = culvert_groups[0]
            rise, length = group["rise"], group["length"]
            invert_us, invert_ds = group["invert_us"], group["invert_ds"]
            stations = group["barrel_stations"]
            if len(stations) == 1:
                barrel_us_station, barrel_ds_station = stations[0]
            name = group["name"]

        entry = GeometryTextStructure(
            river=river,
            reach=reach,
            rs=block_rs,
            type=_extract_type(type_line),
            deck_up=deck_up,
            deck_dn=deck_dn,
            low_chord_up=low_chord_up,
            low_chord_dn=low_chord_dn,
            rise=rise,
            length=length,
            invert_us=invert_us,
            invert_ds=invert_ds,
            barrel_us_station=barrel_us_station,
            barrel_ds_station=barrel_ds_station,
            name=name, source_path=str(file_path), source_line=i+1,
            culvert_records=culvert_records, culvert_groups=culvert_groups,
            deck_header=deck_header,
        )
        entries.append(entry)
        i = max(i + 1, k)

    return entries
