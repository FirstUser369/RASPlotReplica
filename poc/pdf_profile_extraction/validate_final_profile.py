import csv
import cv2
import numpy as np
from pathlib import Path

base = Path(__file__).parent

source_path = (
    base
    / "output"
    / "page_4_profile_region.png"
)

csv_path = (
    base
    / "output"
    / "page_4_profile_coordinates.csv"
)

output_path = (
    base
    / "output"
    / "FINAL_profile_validation.png"
)

image = cv2.imread(str(source_path))

if image is None:
    raise FileNotFoundError(
        f"Could not load image: {source_path}"
    )

if not csv_path.exists():
    raise FileNotFoundError(
        f"Could not load CSV: {csv_path}"
    )

# ------------------------------------------------------------
# SAME CALIBRATION USED TO CREATE THE DXF
# ------------------------------------------------------------

STATION_ORIGIN = 19000.0
STATION_ORIGIN_X = 0.0
STATION_PER_PIXEL = 500.0 / 300.0

ELEVATION_AT_Y0 = 6965.0
ELEVATION_PER_PIXEL = 10.0 / 300.0


def station_to_pixel(station):

    return (
        STATION_ORIGIN_X
        +
        (station - STATION_ORIGIN)
        / STATION_PER_PIXEL
    )


def elevation_to_pixel(elevation):

    return (
        (ELEVATION_AT_Y0 - elevation)
        / ELEVATION_PER_PIXEL
    )


# ------------------------------------------------------------
# READ THE SAME ENGINEERING COORDINATES USED FOR DXF
# ------------------------------------------------------------

engineering_points = []

with open(csv_path, newline="") as file:

    reader = csv.DictReader(file)

    for row in reader:

        station = float(row["Station"])
        elevation = float(row["Elevation"])

        engineering_points.append(
            (station, elevation)
        )

if len(engineering_points) < 2:
    raise RuntimeError(
        "Not enough profile coordinates."
    )

# ------------------------------------------------------------
# TRANSFORM DXF/ENGINEERING COORDINATES BACK TO IMAGE PIXELS
# ------------------------------------------------------------

pixel_points = []

for station, elevation in engineering_points:

    x = station_to_pixel(station)
    y = elevation_to_pixel(elevation)

    pixel_points.append(
        [round(x), round(y)]
    )

pixel_points = np.array(
    pixel_points,
    dtype=np.int32
)

# ------------------------------------------------------------
# CREATE VALIDATION IMAGE
# ------------------------------------------------------------

overlay = image.copy()

# Draw generated profile as a thick red line.
cv2.polylines(
    overlay,
    [pixel_points],
    isClosed=False,
    color=(0, 0, 255),
    thickness=4,
    lineType=cv2.LINE_AA
)

# Draw control points.
for x, y in pixel_points:

    cv2.circle(
        overlay,
        (int(x), int(y)),
        radius=4,
        color=(0, 255, 0),
        thickness=-1
    )

# Mark start.
start_x, start_y = pixel_points[0]

cv2.circle(
    overlay,
    (int(start_x), int(start_y)),
    radius=10,
    color=(255, 0, 0),
    thickness=3
)

# Mark end.
end_x, end_y = pixel_points[-1]

cv2.circle(
    overlay,
    (int(end_x), int(end_y)),
    radius=10,
    color=(255, 0, 255),
    thickness=3
)

# ------------------------------------------------------------
# SAVE
# ------------------------------------------------------------

cv2.imwrite(
    str(output_path),
    overlay
)

print("FINAL PROFILE VALIDATION")
print("------------------------")
print()

print(
    f"Engineering points checked: "
    f"{len(engineering_points)}"
)

print()

print(
    f"Start: "
    f"Station {engineering_points[0][0]:.2f}, "
    f"Elevation {engineering_points[0][1]:.2f}"
)

print(
    f"End:   "
    f"Station {engineering_points[-1][0]:.2f}, "
    f"Elevation {engineering_points[-1][1]:.2f}"
)

print()

print("Overlay legend:")
print("  RED     = generated DXF/profile geometry")
print("  GREEN   = extracted control points")
print("  BLUE    = start point")
print("  MAGENTA = end point")

print()
print("Saved:")
print(output_path)