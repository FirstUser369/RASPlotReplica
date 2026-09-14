"""HDF reader for HEC-RAS 6.x/7.x result and geometry files.

Implements a read-only adapter over h5py that exposes the data RAS-Plot v3 requires:

  * River/reach centreline + cross-section locations and bank stations
  * Cross-section attributes (River, Reach, RS, Length Channel, bank stations)
  * Structures (bridges, culverts, inline weirs) with low/high chord, invert
  * Profile results: WSEL, energy, crit depth (where present), flow per cross-section
  * Time-series (unsteady) and steady-profile result blocks

Schema references checked against the Muncie sample (HEC-RAS 6.4.1 June 2023).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

import h5py
import numpy as np


# --------------------------------------------------------------------------- #
# Data classes                                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class CrossSection:
    river: str
    reach: str
    rs: str
    name: str
    length_channel: float
    left_bank: float
    right_bank: float
    station_elevation: list[tuple[float, float]] = field(default_factory=list)
    polypoints: list[tuple[float, float]] = field(default_factory=list)  # x,y of cross-section line
    minchel: Optional[float] = None
    index: int = 0


@dataclass
class Structure:
    river: str
    reach: str
    rs: str
    type: str            # Bridge / Culvert / Inline Weir / Lateral Structure
    name: str
    upstream_distance: float
    weir_width: float
    low_chord: Optional[float] = None
    high_chord: Optional[float] = None
    minchel: Optional[float] = None
    poly_points: list[tuple[float, float]] = field(default_factory=list)
    profile_data: list[tuple[float, float]] = field(default_factory=list)  # station, elevation of centerline
    index: int = 0
    us_rs: str = ""
    ds_rs: str = ""
    culvert_length: Optional[float] = None
    culvert_us_distance: Optional[float] = None
    invert_us: Optional[float] = None
    invert_ds: Optional[float] = None


@dataclass
class ProfileResult:
    name: str                    # profile date/label as HEC-RAS records it
    wsel: list[float]            # per cross-section, length == number of cross sections
    energy: Optional[list[float]] = None
    flow: Optional[list[float]] = None


@dataclass
class ReachGeometry:
    river: str
    reach: str
    centerline: list[tuple[float, float]]
    cross_sections: list[CrossSection]
    structures: list[Structure]
    rs_to_index: dict[str, int] = field(default_factory=dict)  # RS string -> index in cross_sections


@dataclass
class HecRasProject:
    prj_path: Path
    plans: list["Plan"] = field(default_factory=list)


@dataclass
class Plan:
    name: str
    plan_path: Path
    geometry_path: Optional[Path]
    flow_path: Optional[Path]
    unsteady_path: Optional[Path]
    results_path: Optional[Path]            # plan.*.hdf
    flow_type: str = "steady"               # steady / unsteady
    profiles: list[ProfileResult] = field(default_factory=list)
    reaches: list[ReachGeometry] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _s(b: Any) -> str:
    """Decode an hdf byte-string into a clean python str."""
    if b is None:
        return ""
    if isinstance(b, bytes):
        return b.decode("utf-8", errors="replace").rstrip("\x00").strip()
    if isinstance(b, np.ndarray):
        if b.dtype.kind == "S":
            return b.astype(str).rstrip("\x00").strip()
        return str(b)
    return str(b).strip()


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(f):
        return None
    return f


def _sorted_rs(xs_attrs: np.ndarray) -> np.ndarray:
    """Sort cross-section attributes by descending numeric RS (upstream first),
    which matches HEC-RAS's internal convention."""
    def rs_key(r):
        try:
            return -float(_s(r["RS"]))
        except Exception:
            return 0.0
    order = sorted(range(len(xs_attrs)), key=rs_key)
    return xs_attrs[order]


# --------------------------------------------------------------------------- #
# Reader                                                                       #
# --------------------------------------------------------------------------- #


class HdfReader:
    """Reads HEC-RAS 6.x/7.x plan/geometry HDF files."""

    def __init__(self, plan_hdf_path: str | Path):
        self.plan_hdf_path = Path(plan_hdf_path)

    # -- entry points ------------------------------------------------------- #

    def read_plan(self) -> Plan:
        if not self.plan_hdf_path.exists():
            raise FileNotFoundError(self.plan_hdf_path)
        plan = Plan(
            name=self.plan_hdf_path.stem,
            plan_path=self.plan_hdf_path,
            geometry_path=self._find_geometry(),
            flow_path=None,
            unsteady_path=None,
            results_path=self.plan_hdf_path,
            flow_type="unsteady" if self._has_unsteady_results() else "steady",
        )
        plan.reaches = self._read_reaches_geometry()
        plan.profiles = self._read_profiles(plan.reaches)
        return plan

    # -- file / sibling helpers -------------------------------------------- #

    def _find_geometry(self) -> Optional[Path]:
        with h5py.File(self.plan_hdf_path, "r") as f:
            if "Geometry" in f:
                return self.plan_hdf_path

        # Some HEC-RAS plans embed geometry directly in the plan HDF; otherwise,
        # look for the sibling geometry HDF by convention. Plain .g## files are
        # not HDF5 and must not be treated as geometry sources.
        prj_root = self.plan_hdf_path.parent
        stem = self.plan_hdf_path.stem  # Muncie.p04
        base = stem.split(".")[0]      # Muncie
        candidates = sorted(prj_root.glob(f"{base}.g*.hdf"))
        if candidates:
            return candidates[0]
        return None

    def _has_unsteady_results(self) -> bool:
        with h5py.File(self.plan_hdf_path, "r") as f:
            return "Results/Unsteady" in f

    # -- geometry ----------------------------------------------------------- #

    def _read_reaches_geometry(self) -> list[ReachGeometry]:
        geom_path = self._find_geometry()
        if geom_path is None or not geom_path.exists():
            return []
        reaches_map: dict[tuple[str, str], ReachGeometry] = {}

        with h5py.File(geom_path, "r") as g:
            xs = g.get("/Geometry/Cross Sections")
            structs = g.get("/Geometry/Structures")
            centerlines = g.get("/Geometry/River Centerlines")

            # ----- River centerline attrs ------------------------------ #
            center_attrs = None
            if centerlines is not None and "Attributes" in centerlines:
                center_attrs = centerlines["Attributes"][()]
            cl_info = centerlines["Polyline Info"][()] if centerlines is not None and "Polyline Info" in centerlines else None
            cl_parts = centerlines["Polyline Parts"][()] if centerlines is not None and "Polyline Parts" in centerlines else None
            cl_pts   = centerlines["Polyline Points"][()] if centerlines is not None and "Polyline Points" in centerlines else None

            # ----- Cross-section attrs --------------------------------- #
            if xs is not None and "Attributes" in xs:
                xs_attrs = _sorted_rs(xs["Attributes"][()])
                xs_info = xs["Polyline Info"][()] if "Polyline Info" in xs else None
                xs_parts = xs["Polyline Parts"][()] if "Polyline Parts" in xs else None
                xs_pts = xs["Polyline Points"][()] if "Polyline Points" in xs else None
                se_info = xs["Station Elevation Info"][()] if "Station Elevation Info" in xs else None
                se_values = xs["Station Elevation Values"][()] if "Station Elevation Values" in xs else None

                fields = xs_attrs.dtype.names or ()
                for i, rec in enumerate(xs_attrs):
                    river = _s(rec["River"])
                    reach = _s(rec["Reach"])
                    rs = _s(rec["RS"])
                    if "Name" in fields:
                        name = _s(rec["Name"])
                    elif "Node Name" in fields:
                        name = _s(rec["Node Name"])
                    else:
                        name = ""
                    length_channel = _f(rec["Len Channel"]) or 0.0
                    left_bank = _f(rec["Left Bank"]) or 0.0
                    right_bank = _f(rec["Right Bank"]) or 0.0
                    # Polyline points for this xs (index i into the parts/pts arrays)
                    polypts = self._slice_polyline(xs_info, xs_parts, xs_pts, i) if xs_info is not None else []
                    # Station-elevation pairs for invert computation
                    se_pairs = self._slice_station_elevation(se_info, se_values, i) if se_info is not None else []
                    minchel = self._compute_minchel(se_pairs, left_bank, right_bank)
                    cx = CrossSection(
                        river=river,
                        reach=reach,
                        rs=rs,
                        name=name,
                        length_channel=length_channel,
                        left_bank=left_bank,
                        right_bank=right_bank,
                        station_elevation=se_pairs,
                        polypoints=polypts,
                        minchel=minchel,
                        index=i,
                    )
                    key = (river, reach)
                    if key not in reaches_map:
                        reaches_map[key] = ReachGeometry(
                            river=river, reach=reach, centerline=[], cross_sections=[], structures=[],
                        )
                    reaches_map[key].cross_sections.append(cx)
                    reaches_map[key].rs_to_index[rs] = i

            # ----- Structures ----------------------------------------- #
            if structs is not None and "Attributes" in structs:
                s_attrs = structs["Attributes"][()]
                s_cl_info = structs["Centerline Info"][()] if "Centerline Info" in structs else None
                s_cl_parts = structs["Centerline Parts"][()] if "Centerline Parts" in structs else None
                s_cl_pts = structs["Centerline Points"][()] if "Centerline Points" in structs else None
                s_profile_data = structs["Profile Data"][()] if "Profile Data" in structs else None
                s_table_info = structs["Table Info"][()] if "Table Info" in structs else None
                culvert_groups = structs.get("Culvert Groups")
                culvert_group_attrs = None
                if culvert_groups is not None and "Attributes" in culvert_groups:
                    culvert_group_attrs = culvert_groups["Attributes"][()]
                fields = s_attrs.dtype.names
                for i, rec in enumerate(s_attrs):
                    river = _s(rec["River"]) if "River" in fields else ""
                    reach_name = _s(rec["Reach"]) if "Reach" in fields else ""
                    rs = _s(rec["RS"]) if "RS" in fields else ""
                    stype = _s(rec["Type"]) if "Type" in fields else ""
                    name = ""
                    if "Node Name" in fields and rec["Node Name"]:
                        name = _s(rec["Node Name"])
                    elif "Groupname" in fields and rec["Groupname"]:
                        name = _s(rec["Groupname"])
                    up_dist = _f(rec["Upstream Distance"]) if "Upstream Distance" in fields else 0.0
                    weir_w = _f(rec["Weir Width"]) if "Weir Width" in fields else 0.0
                    weir_min = _f(rec["Weir Min Elevation"]) if "Weir Min Elevation" in fields else None
                    polypts = self._slice_polyline(s_cl_info, s_cl_parts, s_cl_pts, i) if s_cl_info is not None else []
                    pdata = self._slice_table_profile(
                        s_table_info, s_profile_data, i, "Centerline Profile"
                    )
                    low_chord, high_chord = self._structure_chords(
                        s_table_info, s_profile_data, i, stype, weir_min
                    )
                    # Channel invert near structure: nearest XS minchel later in API; store weir
                    # min as a base when available for laterals / inline weirs
                    minchel = None
                    st = Structure(
                        river=river, reach=reach_name, rs=rs, type=stype, name=name,
                        upstream_distance=up_dist or 0.0, weir_width=weir_w or 0.0,
                        low_chord=low_chord, high_chord=high_chord, minchel=minchel,
                        poly_points=polypts, profile_data=pdata, index=i,
                    )
                    if stype.lower() == "culvert":
                        st.us_rs = _s(rec["US RS"]) if "US RS" in fields else ""
                        st.ds_rs = _s(rec["DS RS"]) if "DS RS" in fields else ""
                        st.us_rs = st.us_rs.strip()
                        st.ds_rs = st.ds_rs.strip()
                        if culvert_group_attrs is not None:
                            group_fields = culvert_group_attrs.dtype.names or ()
                            culvert_rec = None
                            for grp in culvert_group_attrs:
                                if "River" in group_fields and _s(grp["River"]) != river:
                                    continue
                                if "Reach" in group_fields and _s(grp["Reach"]) != reach_name:
                                    continue
                                if "RS" in group_fields and _s(grp["RS"]) != rs:
                                    continue
                                if "Struct ID" in group_fields and "Struct ID" in fields:
                                    if _s(grp["Struct ID"]) != _s(rec["Struct ID"]):
                                        continue
                                culvert_rec = grp
                                break
                            if culvert_rec is not None:
                                st.culvert_length = _f(culvert_rec["Length"]) if "Length" in group_fields else None
                                st.culvert_us_distance = _f(culvert_rec["US Distance"]) if "US Distance" in group_fields else None
                                st.invert_us = _f(culvert_rec["Invert US"]) if "Invert US" in group_fields else None
                                st.invert_ds = _f(culvert_rec["Invert DS"]) if "Invert DS" in group_fields else None
                    key = (river, reach_name)
                    if key in reaches_map:
                        reaches_map[key].structures.append(st)

            # ----- Centerlines (one per reach; index matches attribute rows) ---- #
            if center_attrs is not None and cl_info is not None and cl_pts is not None:
                for i, rec in enumerate(center_attrs):
                    river = _s(rec["River Name"])
                    reach = _s(rec["Reach Name"])
                    polypts = self._slice_polyline(cl_info, cl_parts, cl_pts, i)
                    key = (river, reach)
                    if key in reaches_map:
                        reaches_map[key].centerline = polypts

        # Order reaches consistently (by upstream-most RS descending)
        return list(reaches_map.values())

    # ----- polyline / station-elevation slicing ----------------------------- #

    @staticmethod
    def _slice_polyline(info: np.ndarray, parts: np.ndarray, pts: np.ndarray, idx: int) -> list[tuple[float, float]]:
        try:
            p_start, p_count, p_dstart, p_dcount = info[idx]
            out = []
            for p in range(p_start, p_start + p_count):
                pp_start, pp_count = parts[p]
                for q in range(pp_start, pp_start + pp_count):
                    x, y = pts[q]
                    out.append((float(x), float(y)))
            return out
        except Exception:
            return []

    @staticmethod
    def _slice_table_profile(
        table_info: np.ndarray | None,
        profile_data: np.ndarray | None,
        idx: int,
        prefix: str,
    ) -> list[tuple[float, float]]:
        """Slice station-elevation pairs for one structure profile from Table Info."""
        if table_info is None or profile_data is None or idx >= len(table_info):
            return []
        row = table_info[idx]
        names = table_info.dtype.names or ()
        ikey, ckey = f"{prefix} (Index)", f"{prefix} (Count)"
        if ikey not in names or ckey not in names:
            return []
        start = int(row[ikey])
        count = int(row[ckey])
        if count <= 0:
            return []
        chunk = profile_data[start: start + count]
        out: list[tuple[float, float]] = []
        for row2 in chunk:
            try:
                out.append((float(row2[0]), float(row2[1])))
            except Exception:
                continue
        return out

    @classmethod
    def _structure_chords(
        cls,
        table_info: np.ndarray | None,
        profile_data: np.ndarray | None,
        idx: int,
        stype: str,
        weir_min: float | None,
    ) -> tuple[Optional[float], Optional[float]]:
        """Derive low/high chord elevations for FIS structure glyphs.

        Bridge / culvert: high chord = max lid elev (road), low chord = min opening
        elev under deck (US/DS BR Profile). Weir/lateral: crest = Weir Min Elevation
        or centerline profile elev used as high chord.
        """
        t = (stype or "").lower()
        low: Optional[float] = None
        high: Optional[float] = None

        lid_elevs: list[float] = []
        open_elevs: list[float] = []
        for prefix in ("US BR Lid Profile", "DS BR Lid Profile"):
            for _s, e in cls._slice_table_profile(table_info, profile_data, idx, prefix):
                if e == e:  # not NaN
                    lid_elevs.append(e)
        for prefix in ("US BR Profile", "DS BR Profile"):
            for _s, e in cls._slice_table_profile(table_info, profile_data, idx, prefix):
                if e == e:
                    open_elevs.append(e)
        weir_elevs: list[float] = []
        for prefix in ("US BR Weir Profile", "DS BR Weir Profile", "Centerline Profile"):
            for _s, e in cls._slice_table_profile(table_info, profile_data, idx, prefix):
                if e == e:
                    weir_elevs.append(e)

        if lid_elevs:
            high = float(max(lid_elevs))
        if open_elevs:
            # Opening soffit ≈ max of internal BR profile points near deck;
            # low chord for FIS is typically min deck underside ≈ max open elev
            # when open profile is the flow-opening outline under the roadway.
            low = float(max(open_elevs)) if "bridge" in t or "culvert" in t else float(min(open_elevs))
            if high is None:
                high = float(max(open_elevs))

        if high is None and weir_min is not None and weir_min == weir_min:
            high = float(weir_min)
        if high is None and weir_elevs:
            high = float(min(weir_elevs)) if ("lateral" in t or "weir" in t or "inline" in t) else float(max(weir_elevs))
        if low is None and high is not None and ("lateral" in t or "weir" in t or "inline" in t):
            # Discrete weir crest: same elev (horizontal mark)
            low = high
        return low, high

    @staticmethod
    def _slice_station_elevation(se_info: np.ndarray, se_values: np.ndarray, idx: int) -> list[tuple[float, float]]:
        try:
            start, count = se_info[idx]
            chunk = se_values[start:start + count]
            return [(float(s), float(e)) for s, e in chunk]
        except Exception:
            return []

    @staticmethod
    def _compute_minchel(se_pairs: list[tuple[float, float]], lb: float, rb: float) -> Optional[float]:
        if not se_pairs:
            return None
        lo, hi = min(lb, rb), max(lb, rb)
        inband = [e for (s, e) in se_pairs if lo <= s <= hi]
        if not inband:
            inband = [e for (_s, e) in se_pairs]
        return float(min(inband)) if inband else None

    # -- results ------------------------------------------------------------ #

    def _read_profiles(self, reaches: list[ReachGeometry]) -> list[ProfileResult]:
        if not reaches:
            return []
        with h5py.File(self.plan_hdf_path, "r") as f:
            # Unsteady time series — sample maxima per cross-section across the time axis
            ut_path = "Results/Unsteady/Output/Output Blocks/Base Output/Unsteady Time Series"
            sp_path = "Results/Steady/Output/Output Blocks/Base Output/Steady Profiles"
            if "Results/Unsteady" in f and ut_path in f:
                return self._read_unsteady_profiles(f, reaches, ut_path)
            elif "Results/Steady" in f and sp_path in f:
                return self._read_steady_profiles(f, reaches, sp_path)
            # Fallback: post-process steady
            pp_path = "Results/Post Process/Steady/Output/Output Blocks/Base Output/Post Process/Post Process Profiles"
            if pp_path in f:
                return self._read_steady_profiles(f, reaches, pp_path + "/Cross Sections", wsel="Water Surface")
        return []

    def _read_unsteady_profiles(self, f: h5py.File, reaches: list[ReachGeometry], ut_path: str) -> list[ProfileResult]:
        cs = f[ut_path + "/Cross Sections"]
        attrs = cs["Cross Section Attributes"][()]
        # Build sorted order (descending RS float) — attributes use "Station"
        rs_field = "Station" if "Station" in attrs.dtype.names else "RS"
        order = sorted(range(len(attrs)), key=lambda i: -self._rs_to_float(_s(attrs[i][rs_field])))
        wsel_all = cs["Water Surface"][()]  # shape (T, N)
        profiles: list[ProfileResult] = []
        t_ds_name = "Time Date Stamp" if "Time Date Stamp" in cs else "Time Date Stamp (ms)"
        # Provide a downsampled time series (every 12th step + first/last/maxWSEL) plus the max profile
        T = wsel_all.shape[0]
        idxs = list(range(0, T, max(1, T // 30))) or [0]
        if T - 1 not in idxs:
            idxs.append(T - 1)
        for k, t in enumerate(idxs):
            try:
                date = _s(cs[t_ds_name][t])
            except Exception:
                date = f"step {t}"
            wsel_ordered = [float(wsel_all[t, order_pos]) for order_pos in order]
            profiles.append(ProfileResult(name=date or f"step {t}", wsel=wsel_ordered))
        # Plus a "Maximum WSEL" synthetic profile
        max_per_xs = wsel_all.max(axis=0)
        profiles.insert(0, ProfileResult(
            name="Maximum WSEL",
            wsel=[float(max_per_xs[order_pos]) for order_pos in order],
        ))
        return profiles

    def _read_steady_profiles(self, f: h5py.File, reaches: list[ReachGeometry], cs_path: str, wsel: str = "Water Surface") -> list[ProfileResult]:
        base = f[cs_path]
        cs = base
        if wsel not in base and "Water Surface" not in base and "Cross Sections" in base:
            cs = base["Cross Sections"]

        wsel_ds = cs[wsel] if wsel in cs else None
        if wsel_ds is None:
            wsel_ds = cs.get("Water Surface", None)
        if wsel_ds is None:
            return []

        arr = wsel_ds[()]
        names: list[str] = []
        parent = base
        if cs_path.endswith("/Cross Sections"):
            parent = f[cs_path.rsplit("/Cross Sections", 1)[0]]
        if "Profile Names" in parent:
            names = [_s(n) for n in parent["Profile Names"][()]]
        elif "Profile Names" in cs:
            names = [_s(n) for n in cs["Profile Names"][()]]
        if not names:
            names = [f"Profile {i+1}" for i in range(arr.shape[0])]

        attrs = cs.get("Cross Section Attributes")
        if attrs is not None:
            attrs_a = attrs[()]
            order = sorted(range(len(attrs_a)), key=lambda i: -self._rs_to_float(_s(attrs_a[i]["RS"])))
        else:
            order = list(range(arr.shape[1]))
        profiles = []
        for i, name in enumerate(names):
            wsel = [float(arr[i, order_pos]) for order_pos in order]
            profiles.append(ProfileResult(name=name, wsel=wsel))
        return profiles

    @staticmethod
    def _rs_to_float(rs: str) -> float:
        try:
            return float(rs)
        except Exception:
            return 0.0


# --------------------------------------------------------------------------- #
# Convenience JSON serialisers                                                  #
# --------------------------------------------------------------------------- #


def reach_to_dict(r: ReachGeometry) -> dict[str, Any]:
    return {
        "river": r.river,
        "reach": r.reach,
        "centerline": r.centerline,
        "cross_sections": [asdict(c) for c in r.cross_sections],
        "structures": [asdict(s) for s in r.structures],
    }


def plan_to_dict(p: Plan) -> dict[str, Any]:
    return {
        "name": p.name,
        "plan_path": str(p.plan_path),
        "geometry_path": str(p.geometry_path) if p.geometry_path else None,
        "flow_type": p.flow_type,
        "reaches": [reach_to_dict(r) for r in p.reaches],
        "profiles": [{"name": pr.name, "wsel": pr.wsel} for pr in p.profiles],
    }
