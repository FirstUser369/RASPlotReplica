import cv2
import numpy as np
from pathlib import Path

base = Path(__file__).parent

input_path = base / "output" / "page_4_plot.png"
output_dir = base / "output"

image = cv2.imread(str(input_path), cv2.IMREAD_GRAYSCALE)

if image is None:
    raise FileNotFoundError(f"Could not load {input_path}")

# Convert dark drawing content to white foreground.
binary = cv2.threshold(
    image,
    200,
    255,
    cv2.THRESH_BINARY_INV
)[1]

# Detect long horizontal structures.
horizontal_kernel = cv2.getStructuringElement(
    cv2.MORPH_RECT,
    (40, 1)
)

horizontal = cv2.morphologyEx(
    binary,
    cv2.MORPH_OPEN,
    horizontal_kernel
)

# Detect long vertical structures.
vertical_kernel = cv2.getStructuringElement(
    cv2.MORPH_RECT,
    (1, 40)
)

vertical = cv2.morphologyEx(
    binary,
    cv2.MORPH_OPEN,
    vertical_kernel
)

grid = cv2.bitwise_or(horizontal, vertical)

# Remove detected H/V structures from original binary drawing.
without_grid = cv2.bitwise_and(
    binary,
    cv2.bitwise_not(grid)
)

cv2.imwrite(str(output_dir / "page_4_binary.png"), binary)
cv2.imwrite(str(output_dir / "page_4_detected_grid.png"), grid)
cv2.imwrite(str(output_dir / "page_4_without_grid.png"), without_grid)

print("Saved:")
print("  page_4_binary.png")
print("  page_4_detected_grid.png")
print("  page_4_without_grid.png")