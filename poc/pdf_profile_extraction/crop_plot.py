import cv2
from pathlib import Path

base = Path(__file__).parent

input_path = base / "output" / "rendered_pages" / "page_4.png"
output_path = base / "output" / "page_4_plot.png"

image = cv2.imread(str(input_path))

if image is None:
    raise FileNotFoundError(f"Could not load {input_path}")

height, width = image.shape[:2]

print(f"Original image: {width} x {height}")

# Approximate plotting-area crop for this FEMA panel.
# These percentages deliberately exclude the title block,
# margins and most axis labeling.
x1 = int(width * 0.10)
x2 = int(width * 0.86)
y1 = int(height * 0.055)
y2 = int(height * 0.90)

plot = image[y1:y2, x1:x2]

cv2.imwrite(str(output_path), plot)

print(f"Crop: x={x1}:{x2}, y={y1}:{y2}")
print(f"Cropped size: {plot.shape[1]} x {plot.shape[0]}")
print(f"Saved: {output_path}")