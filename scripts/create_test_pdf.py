import fitz

def create_dummy_pdf(path="tests/data/small.pdf"):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "This is a small test PDF document.\nIt contains some text for OCR testing.\nSentence transformers will embed this.", fontsize=12)
    doc.save(path)

if __name__ == "__main__":
    create_dummy_pdf()
