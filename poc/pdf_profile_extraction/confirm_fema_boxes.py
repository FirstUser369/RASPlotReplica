import cv2
from pathlib import Path

base = Path(__file__).parent

input_path = base / "output" / "page_4_profile_region.png"
output_path = base / "output" / "page_4_fema_boxes.png"

image = cv2.imread(str(input_path))

if image is None:
    raise FileNotFoundError(f"Could not load {input_path}")

height, width = image.shape[:2]

overlay = image.copy()

# ------------------------------------------------------------
# FEMA 135P GRID
#
# Previous detection:
# fine grid ≈ 30 px
#
# The FEMA major engineering box contains 10 fine divisions.
#
# Therefore:
#
# 300 px = 500 station feet
# 300 px = 10 elevation feet
# ------------------------------------------------------------

MAJOR_X = 300
MAJOR_Y = 300

# The detected repeating grid phase was approximately:
X_PHASE = 2
Y_PHASE = 13

# ------------------------------------------------------------
# Draw FEMA major vertical boxes
# ------------------------------------------------------------

x = X_PHASE

while x < width:

    cv2.line(
        overlay,
        (x, 0),
        (x, height - 1),
        (0, 0, 255),
        3
    )

    cv2.putText(
        overlay,
        f"x={x}",
        (x + 5, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (0, 0, 255),
        2,
        cv2.LINE_AA
    )

    x += MAJOR_X


# ------------------------------------------------------------
# Draw FEMA major horizontal boxes
# ------------------------------------------------------------

y = Y_PHASE

while y < height:

    cv2.line(
        overlay,
        (0, y),
        (width - 1, y),
        (0, 255, 0),
        3
    )

    cv2.putText(
        overlay,
        f"y={y}",
        (8, max(25, y - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (0, 255, 0),
        2,
        cv2.LINE_AA
    )

    y += MAJOR_Y


cv2.imwrite(str(output_path), overlay)

print(f"Image size: {width} x {height}")
print()

print("FEMA major vertical grid:")
print([
    x for x in range(X_PHASE, width, MAJOR_X)
])

print()

print("FEMA major horizontal grid:")
print([
    y for y in range(Y_PHASE, height, MAJOR_Y)
])

print()

print("Engineering scale:")
print(f"  Station:   {500 / MAJOR_X:.6f} ft/pixel")
print(f"  Elevation: {10 / MAJOR_Y:.6f} ft/pixel")

print()
print(f"Saved:")
print(output_path)