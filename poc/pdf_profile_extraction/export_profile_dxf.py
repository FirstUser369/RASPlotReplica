import csv
from pathlib import Path
import ezdxf

base = Path(__file__).parent

csv_path = (
    base
    / "output"
    / "page_4_profile_coordinates.csv"
)

dxf_path = (
    base
    / "output"
    / "page_4_profile.dxf"
)

if not csv_path.exists():
    raise FileNotFoundError(
        f"Coordinate CSV not found: {csv_path}"
    )

# ------------------------------------------------------------
# Read calibrated Station / Elevation coordinates
# ------------------------------------------------------------

points = []

with open(csv_path, newline="") as file:

    reader = csv.DictReader(file)

    for row in reader:

        station = float(row["Station"])
        elevation = float(row["Elevation"])

        points.append(
            (station, elevation)
        )

if len(points) < 2:
    raise RuntimeError(
        "Not enough profile points to create DXF."
    )

# ------------------------------------------------------------
# Create DXF
# ------------------------------------------------------------

doc = ezdxf.new("R2010")

doc.layers.add(
    name="FEMA_PROFILE"
)

msp = doc.modelspace()

# Create ONE continuous polyline.
#
# X = Station
# Y = Elevation
#
# This keeps the geometry in real engineering coordinates.

msp.add_lwpolyline(
    points,
    dxfattribs={
        "layer": "FEMA_PROFILE"
    }
)

# ------------------------------------------------------------
# Add endpoint markers for easy CAD verification
# ------------------------------------------------------------

start_station, start_elevation = points[0]
end_station, end_elevation = points[-1]

msp.add_circle(
    center=(
        start_station,
        start_elevation
    ),
    radius=2.0,
    dxfattribs={
        "layer": "FEMA_PROFILE"
    }
)

msp.add_circle(
    center=(
        end_station,
        end_elevation
    ),
    radius=2.0,
    dxfattribs={
        "layer": "FEMA_PROFILE"
    }
)

# ------------------------------------------------------------
# Save
# ------------------------------------------------------------

doc.saveas(dxf_path)

print("DXF created successfully.")
print()

print(f"Profile points: {len(points)}")

print()

print(
    f"Start: Station {start_station:.2f}, "
    f"Elevation {start_elevation:.2f}"
)

print(
    f"End:   Station {end_station:.2f}, "
    f"Elevation {end_elevation:.2f}"
)

print()

print(f"Layer: FEMA_PROFILE")

print()

print("Saved:")
print(dxf_path)