"""Reject DRM wrapper pages before OCR or either library generator runs."""
from pathlib import Path
from pypdf import PdfReader


def require_readable_document(path: str | Path) -> None:
    reader = PdfReader(path)
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("PDF_ACCESS_REQUIRED: PDF 已加密，请提供获授权的可读副本；无法生成零件库。")
    # A protection notice is not a scanned datasheet. Do not OCR it or derive
    # package dimensions from the document name. This is document-format based,
    # never an IC/vendor dispatch rule.
    for page in list(reader.pages)[:2]:
        text = (page.extract_text() or "").casefold()
        if "trustview udp protected" in text or (
            "trustview for office" in text and ("保護" in text or "保护" in text)
        ):
            raise ValueError("PDF_ACCESS_REQUIRED: 读取到 TrustView 保护提示页而非技术内容。请提供获授权的可读 PDF；OCR 无法解密，不生成猜测零件。")
