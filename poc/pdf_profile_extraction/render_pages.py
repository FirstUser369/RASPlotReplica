import fitz
from pathlib import Path

base = Path(__file__).parent
pdf_path = base / "input" / "effective_profile.pdf"
output_dir = base / "output" / "rendered_pages"

output_dir.mkdir(parents=True, exist_ok=True)

doc = fitz.open(pdf_path)

for page_number, page in enumerate(doc, start=1):
    # 300 DPI gives us enough resolution for line-analysis testing.
    pix = page.get_pixmap(dpi=300, alpha=False)

    output_path = output_dir / f"page_{page_number}.png"
    pix.save(output_path)

    print(
        f"Page {page_number}: "
        f"{pix.width} x {pix.height} pixels -> {output_path}"
    )

doc.close()

print("Done.")