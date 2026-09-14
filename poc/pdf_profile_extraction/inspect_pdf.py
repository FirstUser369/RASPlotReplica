import fitz
from pathlib import Path

pdf_path = Path(__file__).parent / "input" / "effective_profile.pdf"

doc = fitz.open(pdf_path)

print(f"PDF: {pdf_path.name}")
print(f"Total pages: {len(doc)}")
print("-" * 50)

for page_number, page in enumerate(doc, start=1):
    drawings = page.get_drawings()
    images = page.get_images(full=True)
    text = page.get_text("text").strip()

    print(f"Page {page_number}")
    print(f"  Vector drawing objects: {len(drawings)}")
    print(f"  Embedded images: {len(images)}")
    print(f"  Extracted text characters: {len(text)}")
    print("-" * 50)

doc.close()