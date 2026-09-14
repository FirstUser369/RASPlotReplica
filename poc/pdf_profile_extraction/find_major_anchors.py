import cv2
import numpy as np
from pathlib import Path

base = Path(__file__).parent

input_path = base / "output" / "page_4_profile_region.png"
output_path = base / "output" / "page_4_major_anchors.png"

image = cv2.imread(str(input_path), cv2.IMREAD_GRAYSCALE)

if image is None:
    raise FileNotFoundError(f"Could not load {input_path}")

height, width = image.shape

# ------------------------------------------------------------
# Detect the strongest vertical/horizontal grid lines.
#
# We know from the previous test:
# fine grid ≈ 30 px
# major grid ≈ 150 px
#
# Instead of detecting every grid line, score each possible
# 150-pixel phase and find the phase that repeatedly passes
# through the darkest grid lines.
# ------------------------------------------------------------

darkness = 255.0 - image.astype(np.float64)

vertical_strength = np.mean(darkness, axis=0)
horizontal_strength = np.mean(darkness, axis=1)

MAJOR_PERIOD_X = 150
MAJOR_PERIOD_Y = 150


def best_phase(signal, period):
    scores = []

    for phase in range(period):
        positions = np.arange(phase, len(signal), period)

        if len(positions) == 0:
            scores.append(0)
            continue

        score = np.mean(signal[positions])
        scores.append(score)

    return int(np.argmax(scores))


phase_x = best_phase(
    vertical_strength,
    MAJOR_PERIOD_X
)

phase_y = best_phase(
    horizontal_strength,
    MAJOR_PERIOD_Y
)

vertical_major_lines = list(
    range(phase_x, width, MAJOR_PERIOD_X)
)

horizontal_major_lines = list(
    range(phase_y, height, MAJOR_PERIOD_Y)
)

print(f"Image size: {width} x {height}")
print()

print(f"Best vertical major-grid phase: {phase_x}")
print("Vertical major-grid pixels:")
print(vertical_major_lines)

print()

print(f"Best horizontal major-grid phase: {phase_y}")
print("Horizontal major-grid pixels:")
print(horizontal_major_lines)

# ------------------------------------------------------------
# Diagnostic overlay
# ------------------------------------------------------------

overlay = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

for x in vertical_major_lines:

    cv2.line(
        overlay,
        (x, 0),
        (x, height - 1),
        (0, 0, 255),
        2
    )

    cv2.putText(
        overlay,
        f"x={x}",
        (x + 4, 25),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (0, 0, 255),
        1,
        cv2.LINE_AA
    )


for y in horizontal_major_lines:

    cv2.line(
        overlay,
        (0, y),
        (width - 1, y),
        (0, 255, 0),
        2
    )

    cv2.putText(
        overlay,
        f"y={y}",
        (5, max(18, y - 5)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (0, 255, 0),
        1,
        cv2.LINE_AA
    )


cv2.imwrite(str(output_path), overlay)

print()
print(f"Saved:")
print(output_path)