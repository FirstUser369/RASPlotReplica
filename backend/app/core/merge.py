"""Canonical upstream-first single-reach stationing.

Continuous multi-reach stationing is deliberately blocked pending verified
source topology/junction semantics. User ordering alone is not topology.
"""
from __future__ import annotations

from dataclasses import dataclass

from .hdf_reader import ReachGeometry, CrossSection, Structure, channel_length, station_number, AlignmentError
import math


@dataclass
class MergedReach:
    label: str
    rivers: list[str]
    reaches: list[str]
    cross_sections: list[CrossSection]
    structures: list[Structure]
    cumdist: list[float]          # parallel to cross_sections
    junctions: list[dict]         # gap description between reaches


def merge_reaches(
    reaches: list[ReachGeometry],
    order: list[int],
    junction_distances: list[float] | None = None,
    start_cumdist: float = 0.0,
) -> MergedReach:
    """Return a single reach in canonical upstream-first order.

    Multi-reach calls fail closed until a source-verified topology adapter and
    junction rule exist. Numeric user gaps do not establish connectivity.
    """
    if len(order) != 1:
        raise ValueError(
            "Continuous multi-reach merge is unsupported without verified HEC-RAS "
            "junction topology and channel-distance semantics. Supply the plan HDF, "
            "linked geometry text/HDF and junction editor distances for a connected "
            "multi-reach project. A reach order and numeric gaps alone do not prove connectivity."
        )
    if order[0] < 0 or order[0] >= len(reaches):
        raise ValueError("Reach index out of range")
    if junction_distances:
        raise ValueError("A single reach has no junction distances")
    reach = reaches[order[0]]
    return MergedReach(
        label=f"{reach.river} / {reach.reach}", rivers=[reach.river], reaches=[reach.reach],
        cross_sections=list(reach.cross_sections), structures=list(reach.structures),
        cumdist=compute_cumdist(reach, start_cumdist), junctions=[],
    )


def compute_cumdist_from_lengths(
    upstream_first_lengths: list[float],
    start: float = 0.0,
) -> list[float]:
    """Return upstream-first CUMDIST values from upstream-owned reach lengths.

    Each ordinary XS owns the channel length from itself to the next downstream
    XS.  The downstream-most XS is the supplied origin and its length is not an
    adjacent-XS interval.
    """
    if not math.isfinite(start):
        raise AlignmentError("Starting CUMDIST must be finite.")
    lengths = [channel_length(value, f"XS interval {i}") for i, value in enumerate(upstream_first_lengths)]
    if not lengths:
        return []
    out = [float(start)] * len(upstream_first_lengths)
    running = float(start)
    for i in range(len(upstream_first_lengths) - 2, -1, -1):
        running += lengths[i]
        if not math.isfinite(running):
            raise AlignmentError("CUMDIST overflow: check channel lengths and starting distance.")
        out[i] = running
    return out


def compute_cumdist(r: ReachGeometry, start: float = 0.0) -> list[float]:
    """Compute canonical ordinary-XS CUMDIST for one upstream-first reach."""
    ids = [xs.identity for xs in r.cross_sections]
    stations = [station_number(xs.rs) for xs in r.cross_sections]
    if len(set(ids)) != len(ids) or any(a <= b for a, b in zip(stations, stations[1:])):
        raise AlignmentError("Reach XS must have unique identities and strictly descending numeric RS (upstream first).")
    if any((xs.river, xs.reach) != (r.river, r.reach) for xs in r.cross_sections):
        raise AlignmentError("Cross-section membership does not match its reach.")
    return compute_cumdist_from_lengths(
        [xs.length_channel for xs in r.cross_sections],
        start=start,
    )
