import cv2
from pathlib import Path

base = Path(__file__).parent

input_path = base / "output" / "rendered_pages" / "page_4.png"
output_path = base / "output" / "page_4_edges.png"

image = cv2.imread(str(input_path))

if image is None:
    raise FileNotFoundError(f"Could not load {input_path}")

gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

# Light blur reduces tiny scan artifacts without intentionally
# removing the FEMA grid or profile geometry.
blurred = cv2.GaussianBlur(gray, (3, 3), 0)

edges = cv2.Canny(
    blurred,
    threshold1=50,
    threshold2=150,
)

cv2.imwrite(str(output_path), edges)

print(f"Input size: {image.shape[1]} x {image.shape[0]}")
print(f"Saved edge image to: {output_path}")