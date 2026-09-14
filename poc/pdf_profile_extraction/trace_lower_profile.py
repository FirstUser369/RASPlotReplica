import cv2
import numpy as np
from pathlib import Path

base = Path(__file__).parent

input_path = base / "output" / "page_4_profile_region.png"
output_path = base / "output" / "page_4_lower_profile_trace.png"

image = cv2.imread(str(input_path), cv2.IMREAD_GRAYSCALE)

if image is None:
    raise FileNotFoundError(f"Could not load {input_path}")

height, width = image.shape

# Binary foreground mask
mask = image > 180

# Store traced y position for each x.
trace = np.full(width, np.nan)

# Start from the lower portion on the left side.
# Find the first column that actually contains foreground pixels.
start_x = None
current_y = None

for x in range(width):
    candidates = np.where(mask[:, x])[0]

    if len(candidates) > 0:
        start_x = x
        current_y = int(np.max(candidates))
        break

if start_x is None or current_y is None:
    raise RuntimeError("No starting pixels found.")

print(f"Trace starting at x={start_x}, y={current_y}")

max_jump = 8
search_radius = 12

for x in range(start_x, width):

    low = max(0, current_y - search_radius)
    high = min(height, current_y + search_radius + 1)

    candidates = np.where(mask[low:high, x])[0] + low

    if len(candidates) == 0:
        continue

    # Prefer candidate nearest previous position.
    distances = np.abs(candidates - current_y)

    valid = candidates[distances <= max_jump]

    if len(valid) == 0:
        continue

    # Among nearby candidates, slightly favor the lower pixel.
    best = valid[np.argmax(valid)]

    trace[x] = best
    current_y = int(best)

# Interpolate small missing sections.
valid = ~np.isnan(trace)

if np.count_nonzero(valid) < 2:
    raise RuntimeError("Not enough profile points were traced.")

x_values = np.arange(width)

trace = np.interp(
    x_values,
    x_values[valid],
    trace[valid]
)

# Draw trace over the source image.
overlay = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

points = np.column_stack(
    (
        x_values.astype(np.int32),
        trace.astype(np.int32)
    )
)

cv2.polylines(
    overlay,
    [points],
    False,
    (0, 0, 255),
    2
)

cv2.imwrite(str(output_path), overlay)

print(f"Image size: {width} x {height}")
print(f"Traced points: {len(points)}")
print(f"Saved: {output_path}")