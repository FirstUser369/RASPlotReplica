import cv2
import numpy as np
from pathlib import Path
import math

base = Path(__file__).parent

processed_path = base / "output" / "page_4_profile_region.png"
original_path = base / "output" / "page_4_plot.png"

output_path = base / "output" / "page_4_reconstructed_profile.png"

image = cv2.imread(str(processed_path), cv2.IMREAD_GRAYSCALE)

if image is None:
    raise FileNotFoundError(f"Could not load {processed_path}")

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
    raise RuntimeError("No candidate segments found.")

# Estimate common profile slope.
xs = np.array([s["xm"] for s in segments])
ys = np.array([s["ym"] for s in segments])

slope, intercept = np.polyfit(xs, ys, 1)

for segment in segments:
    expected_y = slope * segment["xm"] + intercept
    segment["offset"] = segment["ym"] - expected_y

segments.sort(key=lambda s: s["offset"])

# Same clustering logic as previous experiment.
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
        f"Expected at least 2 clusters, found {len(clusters)}."
    )

# Cluster 2 was the lower green profile in our diagnostic.
target_cluster = clusters[1]

print(f"Using Cluster 2: {len(target_cluster)} segments")

# Collect both endpoints of every target segment.
points = []

for segment in target_cluster:

    x1, y1, x2, y2 = segment["coords"]

    points.append((x1, y1))
    points.append((x2, y2))

points = np.array(points, dtype=np.float64)

# Sort points left-to-right.
points = points[np.argsort(points[:, 0])]

# Bin nearby X positions and use median Y.
bin_width = 8

profile_points = []

min_x = int(points[:, 0].min())
max_x = int(points[:, 0].max())

for start_x in range(min_x, max_x + 1, bin_width):

    end_x = start_x + bin_width

    in_bin = (
        (points[:, 0] >= start_x)
        & (points[:, 0] < end_x)
    )

    bin_points = points[in_bin]

    if len(bin_points) == 0:
        continue

    x_value = np.median(bin_points[:, 0])
    y_value = np.median(bin_points[:, 1])

    profile_points.append((x_value, y_value))

profile_points = np.array(profile_points)

if len(profile_points) < 2:
    raise RuntimeError("Not enough reconstructed profile points.")

# Fit/interpolate a continuous Y value across the detected X range.
full_x = np.arange(
    int(profile_points[:, 0].min()),
    int(profile_points[:, 0].max()) + 1
)

full_y = np.interp(
    full_x,
    profile_points[:, 0],
    profile_points[:, 1]
)

# Draw on the processed profile region.
overlay = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

polyline = np.column_stack(
    (
        full_x.astype(np.int32),
        full_y.astype(np.int32)
    )
)

cv2.polylines(
    overlay,
    [polyline],
    False,
    (0, 0, 255),
    3
)

# Draw reconstructed control points too.
for x, y in profile_points:
    cv2.circle(
        overlay,
        (int(x), int(y)),
        4,
        (0, 255, 0),
        -1
    )

cv2.imwrite(str(output_path), overlay)

print(f"Reconstructed control points: {len(profile_points)}")
print(f"Profile X range: {full_x.min()} to {full_x.max()}")
print(f"Saved: {output_path}")