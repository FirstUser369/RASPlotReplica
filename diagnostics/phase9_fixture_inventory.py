"""Read-only inventory of authorized Phase 9 HEC-RAS fixtures."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import zipfile
import sys

import h5py

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.core.hdf_reader import HdfReader
from backend.app.core.structure_model import models_from_rows
from backend.app.core.rasplot_db import build_db_from_plan


DOWNLOADS = Path.home() / "Downloads"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(name: str, path: Path) -> dict:
    reader = HdfReader(path)
    capabilities = reader.inspect()
    plan = reader.read_plan()
    db = build_db_from_plan(plan)
    structures = models_from_rows(db.working)
    return {
        "name": name,
        "path": str(path),
        "sha256": sha256(path),
        "hec_ras_version": capabilities.ras_version,
        "unit_system": capabilities.unit_system,
        "schema_adapter": reader.result_identity_adapter,
        "reaches": len(plan.reaches),
        "profiles": len(plan.profiles),
        "cross_sections": sum(len(reach.cross_sections) for reach in plan.reaches),
        "structures": len(structures),
        "structure_capabilities": sorted({model.capability_level for model in structures}),
        "structure_statuses": sorted({model.status for model in structures}),
    }


def safe_extract(zip_path: Path, destination: Path) -> Path:
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            target = (destination / info.filename).resolve()
            target.relative_to(destination.resolve())
            archive.extract(info, destination)
    matches = list(destination.rglob("CherryCk_ConAuraria.p01.hdf"))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one Cherry Creek plan HDF, found {len(matches)}")
    return matches[0]


def main() -> None:
    fixtures = [
        ("North", ROOT / "RAS Plot Database and Model Linework/Model Data/1033_North LOMR As-Built.p04.hdf"),
        ("Muncie", ROOT.parent / "Muncie/Muncie.p04.hdf"),
        ("Industrial Park", DOWNLOADS / "Multiple Reach Example/HEC-RAS Submittal/Industrial_Park.p02.hdf"),
    ]
    records = [inventory(name, path) for name, path in fixtures]
    with tempfile.TemporaryDirectory(prefix="rasplot-phase9-cherry-") as directory:
        cherry = safe_extract(DOWNLOADS / "Auraria_HEC-RAS.zip", Path(directory))
        records.append(inventory("Cherry Creek", cherry))

    candidates = []
    candidate_root = DOWNLOADS / "Example for Profile Tool/RAS Model"
    for path in sorted(candidate_root.glob("*.p??.hdf")):
        try:
            with h5py.File(path, "r") as source:
                units = source.attrs.get("Units System", "UNKNOWN")
                if isinstance(units, bytes):
                    units = units.decode("utf-8", "replace")
                candidates.append({"path": str(path), "units": str(units)})
        except OSError as exc:
            candidates.append({"path": str(path), "error": str(exc)})
    print(json.dumps({"fixtures": records, "si_candidates": candidates}, indent=2))


if __name__ == "__main__":
    main()
