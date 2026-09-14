import cv2
import numpy as np
from pathlib import Path
import math

base = Path(__file__).parent

input_path = base / "output" / "page_4_profile_region.png"
output_path = base / "output" / "page_4_profile_clusters.png"

image = cv2.imread(str(input_path), cv2.IMREAD_GRAYSCALE)

if image is None:
    raise FileNotFoundError(f"Could not load {input_path}")

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

            segments.append(
                {
                    "coords": (x1, y1, x2, y2),
                    "xm": xm,
                    "ym": ym,
                }
            )

print(f"Candidate segments: {len(segments)}")

if len(segments) == 0:
    raise RuntimeError("No diagonal profile segments found.")

# Estimate the common overall slope using segment midpoints.
xs = np.array([s["xm"] for s in segments])
ys = np.array([s["ym"] for s in segments])

slope, intercept = np.polyfit(xs, ys, 1)

print(f"Estimated image-space slope: {slope:.4f}")

# Convert each segment into a vertical offset from the common trend.
for segment in segments:
    expected_y = slope * segment["xm"] + intercept
    segment["offset"] = segment["ym"] - expected_y

# Sort by vertical offset.
segments.sort(key=lambda s: s["offset"])

# Simple offset-based clustering.
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

# Keep only clusters containing several segments.
clusters = [
    cluster
    for cluster in clusters
    if len(cluster) >= 3
]

print(f"Profile clusters found: {len(clusters)}")

overlay = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

# Distinct diagnostic colors.
colors = [
    (0, 0, 255),
    (0, 255, 0),
    (255, 0, 0),
    (0, 255, 255),
    (255, 0, 255),
    (255, 255, 0),
]

for cluster_index, cluster in enumerate(clusters):

    color = colors[cluster_index % len(colors)]

    print(
        f"Cluster {cluster_index + 1}: "
        f"{len(cluster)} segments"
    )

    for segment in cluster:

        x1, y1, x2, y2 = segment["coords"]

        cv2.line(
            overlay,
            (int(x1), int(y1)),
            (int(x2), int(y2)),
            color,
            2
        )

cv2.imwrite(str(output_path), overlay)

print(f"Saved: {output_path}")