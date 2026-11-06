"""Explicit engineering-unit contracts for source, working and drawing spaces.

The existing authoritative workflow stores US Customary engineering values in
feet and engineering DXF page coordinates in inches.  This module makes those
boundaries explicit and provides deterministic SI/US length conversion without
silently enabling an incompletely converted SI workflow.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Iterable


FEET_PER_METER = 3.280839895013123
INCHES_PER_FOOT = 12.0


class UnitContractError(ValueError):
    """A source unit or engineering quantity violates the unit contract."""


class UnitSystem(str, Enum):
    US_CUSTOMARY = "US Customary"
    SI = "SI"
    UNKNOWN = "UNKNOWN"


class LengthUnit(str, Enum):
    FOOT = "ft"
    METER = "m"
    PAPER_INCH = "paper_in"


def _finite(value: float, label: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise UnitContractError(f"{label} must be finite.")
    return result


def convert_length(value: float, source: LengthUnit, target: LengthUnit) -> float:
    """Convert a finite length without inferring its unit from magnitude."""
    amount = _finite(value, "Length")
    if source == target:
        return amount
    feet = {
        LengthUnit.FOOT: amount,
        LengthUnit.METER: amount * FEET_PER_METER,
        LengthUnit.PAPER_INCH: amount / INCHES_PER_FOOT,
    }[source]
    return {
        LengthUnit.FOOT: feet,
        LengthUnit.METER: feet / FEET_PER_METER,
        LengthUnit.PAPER_INCH: feet * INCHES_PER_FOOT,
    }[target]


@dataclass(frozen=True)
class LengthQuantity:
    value: float
    unit: LengthUnit

    def __post_init__(self) -> None:
        _finite(self.value, "Length")

    def to(self, unit: LengthUnit) -> "LengthQuantity":
        return LengthQuantity(convert_length(self.value, self.unit, unit), unit)


@dataclass(frozen=True)
class EngineeringUnitContract:
    """Unit spaces used by one model and its drawings.

    `working_length_unit` deliberately defaults to feet because that is the
    validated legacy calculation contract.  Constructing an SI source contract
    does not by itself authorize processing: callers must explicitly convert
    every source quantity before marking the workflow authoritative.
    """

    source_system: UnitSystem
    source_length_unit: LengthUnit
    working_length_unit: LengthUnit = LengthUnit.FOOT
    paper_length_unit: LengthUnit = LengthUnit.PAPER_INCH
    conversion_contract: str = "rasplot-length-units-v1"

    @classmethod
    def from_source_system(cls, source_system: str | UnitSystem) -> "EngineeringUnitContract":
        aliases = {
            "US CUSTOMARY": UnitSystem.US_CUSTOMARY,
            "ENGLISH": UnitSystem.US_CUSTOMARY,
            "ENGLISH UNITS": UnitSystem.US_CUSTOMARY,
            "SI": UnitSystem.SI,
            "SI UNITS": UnitSystem.SI,
            "METRIC": UnitSystem.SI,
            "METRIC UNITS": UnitSystem.SI,
            "UNKNOWN": UnitSystem.UNKNOWN,
        }
        try:
            system = source_system if isinstance(source_system, UnitSystem) else aliases[str(source_system).strip().upper()]
        except KeyError as exc:
            raise UnitContractError(f"Unsupported source unit system {source_system!r}.") from exc
        if system is UnitSystem.UNKNOWN:
            raise UnitContractError("Source unit system is UNKNOWN; authoritative conversion is unavailable.")
        source_unit = LengthUnit.FOOT if system is UnitSystem.US_CUSTOMARY else LengthUnit.METER
        return cls(system, source_unit)

    @property
    def conversion_required(self) -> bool:
        return self.source_length_unit is not self.working_length_unit

    def source_to_working(self, value: float) -> float:
        return convert_length(value, self.source_length_unit, self.working_length_unit)

    def working_to_source(self, value: float) -> float:
        return convert_length(value, self.working_length_unit, self.source_length_unit)

    def working_to_paper(self, value: float, working_units_per_paper_inch: float) -> float:
        scale = _finite(working_units_per_paper_inch, "Engineering scale")
        if scale <= 0:
            raise UnitContractError("Engineering scale must be positive.")
        return _finite(value, "Working length") / scale

    def source_points_to_working(
        self, points: Iterable[tuple[float, float]],
    ) -> tuple[tuple[float, float], ...]:
        return tuple((self.source_to_working(x), self.source_to_working(y)) for x, y in points)

    def to_manifest(self) -> dict[str, str]:
        return {
            "contract_version": self.conversion_contract,
            "source_system": self.source_system.value,
            "source_length_unit": self.source_length_unit.value,
            "working_length_unit": self.working_length_unit.value,
            "paper_length_unit": self.paper_length_unit.value,
        }

