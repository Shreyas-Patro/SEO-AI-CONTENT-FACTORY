"""
Text extraction from PDF, DOCX, and Markdown files.
"""

import os


def extract_text(filepath):
    """Extract text from a file based on its extension."""
    ext = os.path.splitext(filepath)[1].lower()

    if ext == ".md" or ext == ".txt":
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()

    elif ext == ".pdf":
        import fitz  # PyMuPDF
        doc = fitz.open(filepath)
        text = ""
        for page in doc:
            text += page.get_text() + "\n\n"
        doc.close()
        return text

    elif ext == ".docx":
        from docx import Document
        doc = Document(filepath)
        return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())

    else:
        raise ValueError(f"Unsupported file type: {ext}. Use .md, .txt, .pdf, or .docx")