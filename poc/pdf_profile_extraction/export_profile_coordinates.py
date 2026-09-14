import cv2
import numpy as np
import csv
from pathlib import Path
import math

base = Path(__file__).parent

image_path = base / "output" / "page_4_profile_region.png"
csv_path = base / "output" / "page_4_profile_coordinates.csv"

image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)

if image is None:
    raise FileNotFoundError(f"Could not load {image_path}")

# ------------------------------------------------------------
# 1. Detect diagonal profile segments
# ------------------------------------------------------------

edges = cv2.Canny(image, 50, 150)

lines = cv2.HoughLinesP(
    edges,
    rho=1,
    theta=np.pi / 180,
    threshold=30,
    minLineLength=30,
    maxLineGap=12
)

segments = []

if lines is not None:
    for line in lines:
        x1, y1, x2, y2 = np.asarray(line).reshape(-1)[:4]

        dx = x2 - x1
        dy = y2 - y1

        if dx == 0:
            continue

        angle = math.degrees(math.atan2(dy, dx))

        if -35 <= angle <= -5:
            xm = (x1 + x2) / 2
            ym = (y1 + y2) / 2

            segments.append({
                "coords": (x1, y1, x2, y2),
                "xm": xm,
                "ym": ym,
            })

if not segments:
    raise RuntimeError("No candidate profile segments found.")

# ------------------------------------------------------------
# 2. Cluster the diagonal segments
# ------------------------------------------------------------

xs = np.array([s["xm"] for s in segments])
ys = np.array([s["ym"] for s in segments])

slope, intercept = np.polyfit(xs, ys, 1)

for segment in segments:
    expected_y = slope * segment["xm"] + intercept
    segment["offset"] = segment["ym"] - expected_y

segments.sort(key=lambda s: s["offset"])

cluster_gap = 7

clusters = []
current_cluster = [segments[0]]

for previous, current in zip(segments, segments[1:]):

    if current["offset"] - previous["offset"] <= cluster_gap:
        current_cluster.append(current)

    else:
        clusters.append(current_cluster)
        current_cluster = [current]

clusters.append(current_cluster)

clusters = [
    cluster
    for cluster in clusters
    if len(cluster) >= 3
]

if len(clusters) < 2:
    raise RuntimeError(
        "Expected lower profile cluster was not found."
    )

# Same lower solid profile that worked in reconstruction.
target_cluster = clusters[1]

# ------------------------------------------------------------
# 3. Build profile control points
# ------------------------------------------------------------

points = []

for segment in target_cluster:

    x1, y1, x2, y2 = segment["coords"]

    points.append((x1, y1))
    points.append((x2, y2))

points = np.array(points, dtype=np.float64)

points = points[
    np.argsort(points[:, 0])
]

bin_width = 8

profile_points = []

min_x = int(points[:, 0].min())
max_x = int(points[:, 0].max())

for start_x in range(
    min_x,
    max_x + 1,
    bin_width
):

    end_x = start_x + bin_width

    mask = (
        (points[:, 0] >= start_x)
        &
        (points[:, 0] < end_x)
    )

    bin_points = points[mask]

    if len(bin_points) == 0:
        continue

    profile_points.append(
        (
            float(np.median(bin_points[:, 0])),
            float(np.median(bin_points[:, 1]))
        )
    )

# ------------------------------------------------------------
# 4. FEMA PANEL 135P CALIBRATION
# ------------------------------------------------------------
#
# Known directly from the original FEMA panel:
#
# Horizontal:
#   Left plot boundary = Station 19000
#   FEMA horizontal scale = 500 ft / major box
#
# Vertical:
#   Bottom plot boundary = Elevation 6920
#   Top plot boundary    = Elevation 7010
#   FEMA vertical scale  = 10 ft / major box
#
# From the confirmed image/grid geometry:
#
#   300 px = 500 station ft
#   300 px = 10 elevation ft
#
# The profile-region crop starts at the Station 19000
# plotting boundary.
#
# The profile-region top is approximately Elevation 6965.
#
# Therefore we use the FEMA grid itself as the engineering
# coordinate system.
# ------------------------------------------------------------

STATION_ORIGIN_X = 0.0
STATION_ORIGIN = 19000.0

STATION_PER_PIXEL = 500.0 / 300.0


# From the original FEMA image:
# profile-region y=0 lies at approximately Elevation 6965.
#
# Elevation decreases as image Y increases.

ELEVATION_AT_Y0 = 6965.0

ELEVATION_PER_PIXEL = 10.0 / 300.0


def pixel_to_station(x):

    return (
        STATION_ORIGIN
        +
        (x - STATION_ORIGIN_X)
        * STATION_PER_PIXEL
    )


def pixel_to_elevation(y):

    return (
        ELEVATION_AT_Y0
        -
        y * ELEVATION_PER_PIXEL
    )

# ------------------------------------------------------------
# 5. Export engineering coordinates
# ------------------------------------------------------------

rows = []

for i, (x, y) in enumerate(
    profile_points,
    start=1
):

    station = pixel_to_station(x)
    elevation = pixel_to_elevation(y)

    rows.append({
        "Point": i,
        "Pixel_X": round(x, 2),
        "Pixel_Y": round(y, 2),
        "Station": round(station, 2),
        "Elevation": round(elevation, 2),
    })


with open(
    csv_path,
    "w",
    newline=""
) as file:

    writer = csv.DictWriter(
        file,
        fieldnames=[
            "Point",
            "Pixel_X",
            "Pixel_Y",
            "Station",
            "Elevation",
        ]
    )

    writer.writeheader()
    writer.writerows(rows)


# ------------------------------------------------------------
# 6. Print sanity-check information
# ------------------------------------------------------------

print(
    f"Profile cluster segments: "
    f"{len(target_cluster)}"
)

print(
    f"Exported points: "
    f"{len(rows)}"
)

print()

print(
    f"Station scale: "
    f"{STATION_PER_PIXEL:.6f} ft/pixel"
)

print(
    f"Elevation scale: "
    f"{ELEVATION_PER_PIXEL:.6f} ft/pixel"
)

print()

print("First 5 engineering coordinates:")

for row in rows[:5]:

    print(
        f"  Station {row['Station']:.2f}, "
        f"Elevation {row['Elevation']:.2f}"
    )


print()

print("Last 5 engineering coordinates:")

for row in rows[-5:]:

    print(
        f"  Station {row['Station']:.2f}, "
        f"Elevation {row['Elevation']:.2f}"
    )


print()

if rows:

    print(
        "Extracted engineering range:"
    )

    print(
        f"  Station: "
        f"{rows[0]['Station']:.2f} "
        f"to "
        f"{rows[-1]['Station']:.2f}"
    )

    elevations = [
        row["Elevation"]
        for row in rows
    ]

    print(
        f"  Elevation: "
        f"{min(elevations):.2f} "
        f"to "
        f"{max(elevations):.2f}"
    )


print()

print(f"Saved:")
print(csv_path)