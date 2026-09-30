import os
from pathlib import Path
from typing import List, Dict, Any
import logging
from pypdf import PdfReader
from docx import Document as DocxDocument

logger = logging.getLogger(__name__)


def parse_pdf(file_path: str) -> List[Dict[str, Any]]:
    """
    Extracts text from a PDF file page by page.
    Returns:
        List of dicts: [{"page_number": int, "text": str}]
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"PDF file not found at {file_path}")

    results = []
    reader = PdfReader(file_path)

    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            raise ValueError("PDF is encrypted and password-protected.")

    for idx, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        text = text.strip()
        if text:
            results.append({
                "page_number": idx + 1,
                "text": text
            })

    if not results:
        logger.warning(f"No extractable text found in PDF: {file_path}")

    return results


def parse_docx(file_path: str) -> List[Dict[str, Any]]:
    """
    Extracts text from a DOCX file, parsing paragraphs and tables.
    Returns:
        List of dicts: [{"page_number": int, "text": str}]
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"DOCX file not found at {file_path}")

    doc = DocxDocument(file_path)
    text_blocks = []

    # Extract paragraphs
    for p in doc.paragraphs:
        p_text = p.text.strip()
        if p_text:
            text_blocks.append(p_text)

    # Extract tables
    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
            if row_text:
                text_blocks.append(row_text)

    if not text_blocks:
        logger.warning(f"No extractable text found in DOCX: {file_path}")
        return []

    # DOCX does not define static physical pages, so divide into pseudo-pages
    # of ~3000 characters (approx 1 page of text) for logical grouping
    results = []
    current_page_text = []
    current_char_count = 0
    page_number = 1

    for block in text_blocks:
        current_page_text.append(block)
        current_char_count += len(block)

        if current_char_count >= 3000:
            results.append({
                "page_number": page_number,
                "text": "\n\n".join(current_page_text)
            })
            page_number += 1
            current_page_text = []
            current_char_count = 0

    if current_page_text:
        results.append({
            "page_number": page_number,
            "text": "\n\n".join(current_page_text)
        })

    return results


def parse_document(file_path: str) -> List[Dict[str, Any]]:
    """
    Dispatcher based on file extension.
    Supports .pdf and .docx.
    """
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        return parse_pdf(file_path)
    elif ext in [".docx", ".doc"]:
        return parse_docx(file_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}. Only PDF and DOCX files are supported.")
