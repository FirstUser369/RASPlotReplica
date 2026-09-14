import cv2
import numpy as np
from pathlib import Path

base = Path(__file__).parent

image_path = base / "output" / "page_4_profile_region.png"
output_path = base / "output" / "page_4_calibration_points.png"

image = cv2.imread(str(image_path))

if image is None:
    raise FileNotFoundError(f"Could not load {image_path}")

height, width = image.shape[:2]

print(f"Profile region size: {width} x {height}")

# For now we are only creating a diagnostic image that gives
# us exact pixel coordinates. We will use this to anchor known
# FEMA grid coordinates before converting the reconstructed line.

overlay = image.copy()

# Draw vertical reference lines every 100 pixels.
for x in range(0, width, 100):
    cv2.line(
        overlay,
        (x, 0),
        (x, height - 1),
        (0, 0, 255),
        1
    )

    cv2.putText(
        overlay,
        str(x),
        (x + 3, 18),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.4,
        (0, 0, 255),
        1,
        cv2.LINE_AA
    )

# Draw horizontal reference lines every 50 pixels.
for y in range(0, height, 50):
    cv2.line(
        overlay,
        (0, y),
        (width - 1, y),
        (0, 255, 0),
        1
    )

    cv2.putText(
        overlay,
        str(y),
        (5, y + 15),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.4,
        (0, 255, 0),
        1,
        cv2.LINE_AA
    )

cv2.imwrite(str(output_path), overlay)

print(f"Saved: {output_path}")