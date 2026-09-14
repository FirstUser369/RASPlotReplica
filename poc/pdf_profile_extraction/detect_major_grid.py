import cv2
import numpy as np
from pathlib import Path

base = Path(__file__).parent

input_path = base / "output" / "page_4_profile_region.png"
output_path = base / "output" / "page_4_grid_peaks.png"

image = cv2.imread(str(input_path), cv2.IMREAD_GRAYSCALE)

if image is None:
    raise FileNotFoundError(f"Could not load {input_path}")

height, width = image.shape

# Dark pixels get higher scores.
darkness = 255 - image

# Projection strength.
vertical_strength = np.mean(darkness, axis=0)
horizontal_strength = np.mean(darkness, axis=1)


def find_peaks(signal, minimum_distance, percentile=75):
    threshold = np.percentile(signal, percentile)

    candidates = []

    for i in range(1, len(signal) - 1):
        if (
            signal[i] >= threshold
            and signal[i] >= signal[i - 1]
            and signal[i] >= signal[i + 1]
        ):
            candidates.append(i)

    # Keep strongest peak when several occur close together.
    candidates = sorted(
        candidates,
        key=lambda i: signal[i],
        reverse=True
    )

    selected = []

    for candidate in candidates:
        if all(
            abs(candidate - existing) >= minimum_distance
            for existing in selected
        ):
            selected.append(candidate)

    return sorted(selected)


vertical_peaks = find_peaks(
    vertical_strength,
    minimum_distance=20
)

horizontal_peaks = find_peaks(
    horizontal_strength,
    minimum_distance=20
)

print("Vertical grid candidates:")
print(vertical_peaks)

print()
print("Vertical spacings:")
print(np.diff(vertical_peaks).tolist())

print()
print("Horizontal grid candidates:")
print(horizontal_peaks)

print()
print("Horizontal spacings:")
print(np.diff(horizontal_peaks).tolist())

overlay = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

for x in vertical_peaks:
    cv2.line(
        overlay,
        (x, 0),
        (x, height - 1),
        (0, 0, 255),
        1
    )

for y in horizontal_peaks:
    cv2.line(
        overlay,
        (0, y),
        (width - 1, y),
        (0, 255, 0),
        1
    )

cv2.imwrite(str(output_path), overlay)

print()
print(f"Saved: {output_path}")