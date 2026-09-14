import cv2
import numpy as np
from pathlib import Path

base = Path(__file__).parent

input_path = base / "output" / "page_4_without_grid.png"
output_path = base / "output" / "page_4_profile_region.png"

image = cv2.imread(str(input_path), cv2.IMREAD_GRAYSCALE)

if image is None:
    raise FileNotFoundError(f"Could not load {input_path}")

height, width = image.shape

# For the first controlled POC, isolate the portion of the FEMA
# plotting area where the actual profile geometry exists.
#
# We are intentionally using image proportions here rather than
# station/elevation values. Automatic profile-region discovery
# can be developed after feasibility is proven.

x1 = 0
x2 = int(width * 0.40)

y1 = int(height * 0.48)
y2 = int(height * 0.82)

profile_region = image[y1:y2, x1:x2]

cv2.imwrite(str(output_path), profile_region)

print(f"Source size: {width} x {height}")
print(f"Profile crop: x={x1}:{x2}, y={y1}:{y2}")
print(
    f"Profile region size: "
    f"{profile_region.shape[1]} x {profile_region.shape[0]}"
)
print(f"Saved: {output_path}")