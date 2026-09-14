import cv2
import numpy as np
from pathlib import Path
import math

base = Path(__file__).parent

input_path = base / "output" / "page_4_profile_region.png"
output_path = base / "output" / "page_4_diagonal_segments.png"

image = cv2.imread(str(input_path), cv2.IMREAD_GRAYSCALE)

if image is None:
    raise FileNotFoundError(f"Could not load {input_path}")

# Detect edges
edges = cv2.Canny(image, 50, 150)

# Probabilistic Hough transform
lines = cv2.HoughLinesP(
    edges,
    rho=1,
    theta=np.pi / 180,
    threshold=30,
    minLineLength=30,
    maxLineGap=12
)

overlay = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

accepted = 0

if lines is not None:
    for line in lines:
        x1, y1, x2, y2 = np.asarray(line).reshape(-1)[:4]

        dx = x2 - x1
        dy = y2 - y1

        if dx == 0:
            continue

        angle = math.degrees(math.atan2(dy, dx))

        # Image Y increases downward.
        # The FEMA profile rises toward the right,
        # therefore its image-space angle is negative.
        if -35 <= angle <= -5:
            cv2.line(
                overlay,
                (x1, y1),
                (x2, y2),
                (0, 0, 255),
                2
            )
            accepted += 1

cv2.imwrite(str(output_path), overlay)

print(f"Accepted diagonal segments: {accepted}")
print(f"Saved: {output_path}")