import cv2
import numpy as np
from pathlib import Path

base = Path(__file__).parent

input_path = base / "output" / "page_4_profile_region.png"
output_path = base / "output" / "page_4_scale_calibration.png"

image = cv2.imread(str(input_path), cv2.IMREAD_GRAYSCALE)

if image is None:
    raise FileNotFoundError(f"Could not load {input_path}")

height, width = image.shape

# ------------------------------------------------------------
# FEMA PANEL 135P
#
# From the source panel:
#
# Major vertical grid spacing = 500 station feet
# Major horizontal grid spacing = 10 elevation feet
#
# The cropped image still contains the regular FEMA grid.
#
# Rather than assuming pixels-per-grid-box, detect the dominant
# repeating grid period directly from the image.
# ------------------------------------------------------------

darkness = 255 - image

vertical_signal = np.mean(darkness, axis=0)
horizontal_signal = np.mean(darkness, axis=1)


def autocorrelation_period(signal, min_period, max_period):
    """
    Find the strongest repeating period in a 1-D image projection.
    """

    signal = signal.astype(np.float64)
    signal -= np.mean(signal)

    correlation = np.correlate(
        signal,
        signal,
        mode="full"
    )

    correlation = correlation[len(correlation) // 2:]

    search = correlation[min_period:max_period + 1]

    best_offset = int(np.argmax(search))

    return min_period + best_offset


# Fine FEMA grid spacing should be somewhere in these broad ranges.
vertical_fine_period = autocorrelation_period(
    vertical_signal,
    min_period=10,
    max_period=100
)

horizontal_fine_period = autocorrelation_period(
    horizontal_signal,
    min_period=10,
    max_period=100
)

print(f"Image size: {width} x {height}")
print()

print(
    f"Detected vertical fine-grid period: "
    f"{vertical_fine_period} px"
)

print(
    f"Detected horizontal fine-grid period: "
    f"{horizontal_fine_period} px"
)

# ------------------------------------------------------------
# Major FEMA grid
#
# From the visible FEMA sheet:
# 5 fine divisions = 1 major division.
# ------------------------------------------------------------

vertical_major_period = vertical_fine_period * 5
horizontal_major_period = horizontal_fine_period * 5

print()
print(
    f"Estimated vertical major-grid period: "
    f"{vertical_major_period} px"
)

print(
    f"Estimated horizontal major-grid period: "
    f"{horizontal_major_period} px"
)

# ------------------------------------------------------------
# Convert detected pixel spacing to engineering scale.
# ------------------------------------------------------------

STATION_PER_MAJOR_GRID = 500.0
ELEVATION_PER_MAJOR_GRID = 10.0

station_per_pixel = (
    STATION_PER_MAJOR_GRID / vertical_major_period
)

elevation_per_pixel = (
    ELEVATION_PER_MAJOR_GRID / horizontal_major_period
)

print()
print(
    f"Calculated station scale: "
    f"{station_per_pixel:.6f} ft/pixel"
)

print(
    f"Calculated elevation scale: "
    f"{elevation_per_pixel:.6f} ft/pixel"
)

# ------------------------------------------------------------
# Diagnostic overlay
# ------------------------------------------------------------

overlay = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

# Draw vertical detected major spacing from image center.
center_x = width // 2

for direction in (-1, 1):

    x = center_x

    while 0 <= x < width:

        cv2.line(
            overlay,
            (int(x), 0),
            (int(x), height - 1),
            (0, 0, 255),
            2
        )

        x += direction * vertical_major_period


# Draw horizontal detected major spacing from image center.
center_y = height // 2

for direction in (-1, 1):

    y = center_y

    while 0 <= y < height:

        cv2.line(
            overlay,
            (0, int(y)),
            (width - 1, int(y)),
            (0, 255, 0),
            2
        )

        y += direction * horizontal_major_period


cv2.imwrite(str(output_path), overlay)

print()
print(f"Saved diagnostic image:")
print(output_path)