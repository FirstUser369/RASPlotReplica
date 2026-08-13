"""Multi-reach merge per RASPLOT v3 Section 5.2 (page 22):

  > When a HEC-RAS model contains multiple river reaches, the user must group the
  > reaches into a single reach in order to plot the reaches using RASPLOT...RASPLOT
  > generally plots the profiles from downstream to upstream; hence the reaches should
  > also be selected and arranged in that order with the most downstream reach listed
  > at the top and the most upstream at the bottom...the user will need to check and
  > update the junction distances between the reaches to match the input used in the
  > model.

  CUMDIST (page 24): cumulative downstream reach length from the downstream-most
  point to that cross section, obtained by adding channel length from downstream to
  upstream.

This module concatenates reaches in user-specified order, inserting the junction
distance (channel length at the most downstream cross section of the upstream
reach) between successive reaches, and produces a unified station axis.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .hdf_reader import ReachGeometry, CrossSection, Structure


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
    """Merge reaches in the given order (downstream-first).

    Params:
        reaches: list of all ReachGeometry available.
        order: zero-based indices into ``reaches`` in downstream-first order.
        junction_distances: length of the junction between successive reaches.
            Defaults to 0.0 (the v3 default if not provided by the user).
        start_cumdist: starting cumulative distance (v3 "Set Starting Distance").

    Returns: a MergedReach with one combined station axis and CUMDIST for each xs.
    """
    if not order:
        raise ValueError("order must contain at least one reach index")
    if junction_distances is None:
        junction_distances = [0.0] * max(0, len(order) - 1)
    if len(junction_distances) != max(0, len(order) - 1):
        raise ValueError(
            f"Expected {max(0, len(order) - 1)} junction distances, got "
            f"{len(junction_distances)}"
        )

    merged: list[CrossSection] = []
    merged_structs: list[Structure] = []
    cumdist: list[float] = []
    junctions: list[dict] = []
    rivers: list[str] = []
    reaches_names: list[str] = []

    running = start_cumdist
    for k, idx in enumerate(order):
        r = reaches[idx]
        rivers.append(r.river)
        reaches_names.append(r.reach)

        # v3: cross sections in a reach are ordered upstream-first by HEC-RAS
        # (RS descending). For plotting we want downstream-first, so iterate
        # the reversed list. The downstream-most cross section is used to clamp
        # the junction distance for the NEXT reach up.
        xs_ds_first = list(reversed(r.cross_sections))
        for j, xs in enumerate(xs_ds_first):
            # channel length of this xs to the next downstream xs inside the
            # same reach; for the very first (most downstream) xs of the FIRST
            # reach there's no length to subtract.
            running_inc = xs.length_channel if k > 0 or j > 0 else 0.0
            running += running_inc
            cumdist.append(running)
            merged.append(xs)

        # Structures map onto a reach too; keep them with the same reach index
        merged_structs.extend(r.structures)

        # Junction to next reach (upstream): we add a virtual gap equal to the
        # user-specified junction distance, which becomes the channel length of
        # the MOST UPSTREAM cross section of THIS reach when the next reach
        # starts. v3 page 25: "For multiple reaches, the channel length at the
        # most downstream cross section at the reach should be equal to the
        # junction distance input in the HEC-RAS model."  We model the junction
        # gap by adding it to running so the downstream-most xs of the next
        # reach starts at +junction_distance.
        if k < len(order) - 1:
            jd = junction_distances[k]
            running += jd
            upstream_xs = xs_ds_first[-1] if xs_ds_first else None
            junctions.append({
                "from_reach": r.reach,
                "from_river": r.river,
                "to_reach": reaches[order[k + 1]].reach if k + 1 < len(order) else None,
                "to_river": reaches[order[k + 1]].river if k + 1 < len(order) else None,
                "distance": jd,
                "upstream_rs": upstream_xs.rs if upstream_xs else None,
            })

    label = " + ".join(f"{r.river}\\{r.reach}" for r in [reaches[i] for i in order])
    return MergedReach(
        label=label,
        rivers=rivers,
        reaches=reaches_names,
        cross_sections=merged,
        structures=merged_structs,
        cumdist=cumdist,
        junctions=junctions,
    )


def compute_cumdist(r: ReachGeometry, start: float = 0.0) -> list[float]:
    """Compute the CUMDIST column for a single reach (v3 page 24)."""
    cum = start
    out: list[float] = []
    for xs in reversed(r.cross_sections):  # downstream-first
        out.append(cum)
        cum += xs.length_channel
    return list(reversed(out))  # back to upstream-first matching r.cross_sections order
