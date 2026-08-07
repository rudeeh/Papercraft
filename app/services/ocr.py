import fitz  # PyMuPDF
import os
import structlog

logger = structlog.get_logger()

# Doctr/torch are only needed for scanned pages with no extractable native
# text, so they're imported lazily inside get_predictor() -- most PDFs never
# touch them, which keeps worker startup and native-text extraction fast.
_predictor = None

# A page shorter than this (after stripping whitespace) is treated as
# "no usable native text" and falls back to OCR (e.g. scanned/image pages).
MIN_NATIVE_TEXT_CHARS = 20


def get_predictor():
    global _predictor
    if _predictor is None:
        import torch
        from doctr.models import ocr_predictor

        # Check for GPU
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        logger.info(f"Loading Doctr model on {device}")
        try:
            _predictor = ocr_predictor(det_arch='db_resnet50', reco_arch='crnn_vgg16_bn', pretrained=True).to(device)
        except Exception as e:
             logger.error(f"Failed to load Doctr model: {e}")
             raise e
    return _predictor


def _ocr_page(page) -> str:
    """Render a single PyMuPDF page to an image and run Doctr OCR on it."""
    import numpy as np
    import cv2

    # Render page to image (zoom = 2 for better OCR resolution)
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
    img_bytes = pix.tobytes("png")

    nparr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    # Doctr expects RGB, cv2 decodes to BGR.
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    predictor = get_predictor()
    result = predictor([img])

    page_text = ""
    for block in result.pages[0].blocks:
        for line in block.lines:
            for word in line.words:
                page_text += word.value + " "
            page_text += "\n"

    return page_text.strip()


def extract_text_from_pdf(file_path: str):
    """
    Extract text from a PDF, page by page.

    Native text extraction (PyMuPDF) is tried first for every page since it
    is fast and lossless for text-based PDFs. Doctr OCR -- which is much
    slower and requires loading a model -- is only invoked for pages where
    native extraction yields little or no text (e.g. scanned/image pages).
    """
    try:
        doc = fitz.open(file_path)
        results = []

        for page_num, page in enumerate(doc):
            native_text = page.get_text().strip()

            if len(native_text) >= MIN_NATIVE_TEXT_CHARS:
                results.append((page_num + 1, native_text))
                continue

            logger.info(
                "ocr_fallback_page",
                page=page_num + 1,
                native_chars=len(native_text),
            )
            page_text = _ocr_page(page)
            results.append((page_num + 1, page_text))

        doc.close()
        return results

    except Exception as e:
        logger.error(f"OCR failed: {e}")
        raise e
