from __future__ import annotations

import os
import re
from pathlib import Path

import h5py
import numpy as np

HDF_PATH = Path(r"RAS Plot Database and Model Linework/Model Data/1033_North LOMR As-Built.p04.hdf")

KEYWORDS = [
    "profile", "profiles", "result", "results", "steady", "flow", "water",
    "wsel", "water surface", "annual", "chance", "prob", "frequency", "boundary",
    "output", "cross", "section", "pf"
]


def has_keyword(s: str) -> bool:
    low = s.lower()
    return any(k in low for k in KEYWORDS)


def summarize_dataset(ds: h5py.Dataset, max_items=8):
    shape = ds.shape
    dtype = ds.dtype
    print(f"  PATH: {ds.name}")
    print(f"  SHAPE: {shape}")
    print(f"  DTYPE: {dtype}")
    if ds.size == 0:
        print("  VALUES: []")
        return
    try:
        arr = ds[()]
        if hasattr(arr, "dtype") and arr.dtype.kind in {"S", "O", "U"}:
            flat = arr.reshape(-1)
            values = [str(v).strip() for v in flat[:max_items]]
            print(f"  VALUES: {values}")
            if flat.size > max_items:
                print(f"  ... last {min(max_items, flat.size-max_items)}: {[str(v).strip() for v in flat[-max_items:]]}")
        else:
            arr2 = np.asarray(arr)
            if arr2.ndim == 0:
                print(f"  VALUE: {arr2.item()}")
            else:
                flat = np.asarray(arr2).reshape(-1)
                print(f"  FIRST: {flat[:max_items].tolist()}")
                if flat.size > max_items:
                    print(f"  LAST: {flat[-max_items:].tolist()}")
    except Exception as e:
        print(f"  VALUE_ERROR: {e}")


def print_paths_for_keywords(path: str, obj):
    lname = path.lower()
    if isinstance(obj, h5py.Group):
        if has_keyword(lname):
            print(f"GROUP {path}")
            for k in list(obj.keys())[:35]:
                child = obj[k]
                if isinstance(child, h5py.Dataset):
                    print(f"  DATASET {k} shape={child.shape} dtype={child.dtype}")
                else:
                    print(f"  GROUP {k}")
    else:
        if has_keyword(lname):
            summarize_dataset(obj)


def main():
    hdf = HDF_PATH.resolve()
    print(f"HDF PATH: {hdf}")
    print(f"EXISTS: {hdf.exists()}")
    print()
    with h5py.File(hdf, "r") as f:
        print("ROOT KEYS:")
        for k in f.keys():
            print(" -", k)
        print('\nKEYWORD-RELATED GROUPS/DATASETS:')
        f.visititems(lambda name, obj: print_paths_for_keywords(name, obj))

        # Hardcoded targeted paths
        targets = [
            "Results",
            "Results/Steady",
            "Results/Steady/Output",
            "Results/Steady/Output/Output Blocks",
            "Results/Steady/Output/Output Blocks/Base Output",
            "Results/Steady/Output/Output Blocks/Base Output/Steady Profiles",
            "Results/Steady/Output/Output Blocks/Base Output/Steady Profiles/Cross Sections",
            "Results/Steady/Output/Output Blocks/Base Output/Steady Profiles/Flow Data",
            "Results/Unsteady",
            "Results/Post Process",
            "Geometry",
            "Geometry/Cross Sections",
            "Flow",
            "Plan Data",
            "Boundary Conditions",
        ]
        for p in targets:
            if p in f:
                print(f"\n=== {p} ===")
                g = f[p]
                print("KEYS:")
                for k in list(g.keys())[:40]:
                    obj = g[k]
                    if isinstance(obj, h5py.Group):
                        print(f"  GROUP {k}")
                        for kk in list(obj.keys())[:25]:
                            child = obj[kk]
                            if isinstance(child, h5py.Dataset):
                                print(f"    DATASET {kk} shape={child.shape} dtype={child.dtype}")
                            else:
                                print(f"    GROUP {kk}")
                    else:
                        print(f"  DATASET {k} shape={obj.shape} dtype={obj.dtype}")
                        if obj.size < 200 and obj.dtype.kind in {"S", "U", "O"}:
                            try:
                                print("    VALUES:", obj[()])
                            except Exception as e:
                                print("    VALUE_ERROR:", e)

        # print any profile names / flow names datasets and a few values
        print("\nPROBABLE NAME DATASETS:")
        matches = []
        def add(name, obj):
            if isinstance(obj, h5py.Dataset):
                lname = name.lower()
                if any(token in lname for token in ["name", "profile", "flow", "boundary"]):
                    matches.append(name)
        f.visititems(add)
        for p in sorted(matches):
            if p.count('/') < 10:
                try:
                    ds = f[p]
                    print(f"{p} :: shape={ds.shape} dtype={ds.dtype}")
                    if ds.size < 200:
                        print("  values:", ds[()])
                except Exception as e:
                    print("ERR", p, e)


if __name__ == "__main__":
    main()
