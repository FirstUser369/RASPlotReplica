import cv2
from pathlib import Path

base = Path(__file__).parent

input_path = base / "output" / "page_4_profile_region.png"
output_path = base / "output" / "page_4_anchor_reference.png"

image = cv2.imread(str(input_path))

if image is None:
    raise FileNotFoundError(f"Could not load {input_path}")

height, width = image.shape[:2]

overlay = image.copy()

# ------------------------------------------------------------
# Diagnostic coordinate ruler
#
# Vertical red lines every 50 px.
# Horizontal green lines every 50 px.
#
# Labels are added so we can match visible FEMA grid locations
# to exact pixel coordinates.
# ------------------------------------------------------------

for x in range(0, width, 50):

    cv2.line(
        overlay,
        (x, 0),
        (x, height - 1),
        (0, 0, 255),
        1
    )

    cv2.putText(
        overlay,
        f"x={x}",
        (x + 3, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.38,
        (0, 0, 255),
        1,
        cv2.LINE_AA
    )


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
        f"y={y}",
        (5, y + 15),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.38,
        (0, 255, 0),
        1,
        cv2.LINE_AA
    )


cv2.imwrite(str(output_path), overlay)

print(f"Image size: {width} x {height}")
print()
print(f"Saved:")
print(output_path)