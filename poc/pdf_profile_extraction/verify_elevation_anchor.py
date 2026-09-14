import cv2
from pathlib import Path

base = Path(__file__).parent

input_path = base / "output" / "page_4_profile_region.png"
output_path = base / "output" / "page_4_elevation_anchor_check.png"

image = cv2.imread(str(input_path))

if image is None:
    raise FileNotFoundError(f"Could not load {input_path}")

height, width = image.shape[:2]

overlay = image.copy()

# ------------------------------------------------------------
# FEMA panel 135P
#
# Confirmed engineering grid spacing:
# 300 px = 10 ft
#
# Current TEST assumption:
# y = 613 corresponds to Elevation 6940.
#
# Draw neighboring major lines and label the elevations
# implied by that assumption.
# ------------------------------------------------------------

ANCHOR_Y = 613
ANCHOR_ELEVATION = 6940
MAJOR_GRID_PIXELS = 300
MAJOR_GRID_ELEVATION = 10

major_lines = []

# Work upward from anchor
y = ANCHOR_Y
elevation = ANCHOR_ELEVATION

while y >= 0:
    major_lines.append((y, elevation))
    y -= MAJOR_GRID_PIXELS
    elevation += MAJOR_GRID_ELEVATION

# Work downward from anchor
y = ANCHOR_Y + MAJOR_GRID_PIXELS
elevation = ANCHOR_ELEVATION - MAJOR_GRID_ELEVATION

while y < height:
    major_lines.append((y, elevation))
    y += MAJOR_GRID_PIXELS
    elevation -= MAJOR_GRID_ELEVATION

major_lines.sort()

for y, elevation in major_lines:

    cv2.line(
        overlay,
        (0, y),
        (width - 1, y),
        (0, 255, 0),
        3
    )

    label = f"{elevation} ft   y={y}"

    cv2.putText(
        overlay,
        label,
        (20, max(30, y - 10)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (0, 255, 0),
        2,
        cv2.LINE_AA
    )

cv2.imwrite(str(output_path), overlay)

print("Elevation anchor being tested:")
print(f"  y={ANCHOR_Y} -> Elevation {ANCHOR_ELEVATION}")
print()

print("Major elevation lines:")
for y, elevation in major_lines:
    print(f"  y={y} -> {elevation} ft")

print()
print(f"Saved:")
print(output_path)