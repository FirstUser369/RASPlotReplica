import cv2
from pathlib import Path

base = Path(__file__).parent

original_path = (
    base
    / "output"
    / "rendered_pages"
    / "page_4.png"
)

output_path = (
    base
    / "output"
    / "page_4_crop_on_original.png"
)

image = cv2.imread(str(original_path))

if image is None:
    raise FileNotFoundError(
        f"Could not load {original_path}"
    )

full_height, full_width = image.shape[:2]

# ------------------------------------------------------------
# Reproduce EXACTLY the crop chain we already used.
#
# crop_plot.py:
#   x = 10% to 86%
#   y = 5.5% to 90%
#
# isolate_profile.py:
#   x = 0% to 40% of plot crop
#   y = 48% to 82% of plot crop
# ------------------------------------------------------------

plot_x1 = int(full_width * 0.10)
plot_x2 = int(full_width * 0.86)

plot_y1 = int(full_height * 0.055)
plot_y2 = int(full_height * 0.90)

plot_width = plot_x2 - plot_x1
plot_height = plot_y2 - plot_y1


region_x1 = 0
region_x2 = int(plot_width * 0.40)

region_y1 = int(plot_height * 0.48)
region_y2 = int(plot_height * 0.82)


# Convert profile-region bounds back to original-image pixels.

full_region_x1 = plot_x1 + region_x1
full_region_x2 = plot_x1 + region_x2

full_region_y1 = plot_y1 + region_y1
full_region_y2 = plot_y1 + region_y2


# ------------------------------------------------------------
# Draw the profile-region rectangle.
# ------------------------------------------------------------

overlay = image.copy()

cv2.rectangle(
    overlay,
    (full_region_x1, full_region_y1),
    (full_region_x2, full_region_y2),
    (0, 0, 255),
    8
)

# Label it.

cv2.putText(
    overlay,
    "PROFILE REGION USED FOR EXTRACTION",
    (
        full_region_x1 + 20,
        max(40, full_region_y1 - 20)
    ),
    cv2.FONT_HERSHEY_SIMPLEX,
    1.2,
    (0, 0, 255),
    3,
    cv2.LINE_AA
)


cv2.imwrite(
    str(output_path),
    overlay
)


print(
    f"Original image size: "
    f"{full_width} x {full_height}"
)

print()

print("Plot crop on original image:")
print(
    f"  X: {plot_x1} to {plot_x2}"
)
print(
    f"  Y: {plot_y1} to {plot_y2}"
)

print()

print("Profile region on original image:")
print(
    f"  X: {full_region_x1} "
    f"to {full_region_x2}"
)
print(
    f"  Y: {full_region_y1} "
    f"to {full_region_y2}"
)

print()

print("Profile-region origin in original image:")
print(
    f"  ({full_region_x1}, "
    f"{full_region_y1})"
)

print()

print("Saved:")
print(output_path)