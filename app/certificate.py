"""Certificate rendering (single predefined template, PDF output via ReportLab)."""
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = landscape(A4)
NAVY = HexColor("#1F3A5F")
GOLD = HexColor("#B8923A")
GREY = HexColor("#555555")


@dataclass(frozen=True)
class CertificateData:
    certificate_id: str
    recipient_name: str
    event_name: str
    issuer: str
    issue_date: date


def _fit_font_size(text: str, font: str, max_size: float, max_width: float,
                   min_size: float = 12) -> float:
    size = max_size
    while size > min_size and stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def _draw(c: canvas.Canvas, data: CertificateData) -> None:
    cx = PAGE_W / 2

    # Double border
    c.setStrokeColor(NAVY)
    c.setLineWidth(4)
    c.rect(24, 24, PAGE_W - 48, PAGE_H - 48)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.5)
    c.rect(34, 34, PAGE_W - 68, PAGE_H - 68)

    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 46)
    c.drawCentredString(cx, PAGE_H - 130, "CERTIFICATE")
    c.setFillColor(GOLD)
    c.setFont("Helvetica", 18)
    c.drawCentredString(cx, PAGE_H - 160, "OF PARTICIPATION")

    c.setFillColor(GREY)
    c.setFont("Helvetica", 14)
    c.drawCentredString(cx, PAGE_H - 215, "This is to certify that")

    name_font = "Times-BoldItalic"
    name_size = _fit_font_size(data.recipient_name, name_font, 40, PAGE_W - 160)
    c.setFillColor(NAVY)
    c.setFont(name_font, name_size)
    c.drawCentredString(cx, PAGE_H - 270, data.recipient_name)
    name_w = stringWidth(data.recipient_name, name_font, name_size)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.line(cx - name_w / 2 - 20, PAGE_H - 280, cx + name_w / 2 + 20, PAGE_H - 280)

    c.setFillColor(GREY)
    c.setFont("Helvetica", 14)
    c.drawCentredString(cx, PAGE_H - 318, "has successfully participated in")

    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 22)
    y = PAGE_H - 355
    for line in simpleSplit(data.event_name, "Helvetica-Bold", 22, PAGE_W - 180):
        c.drawCentredString(cx, y, line)
        y -= 28

    # Footer: date (left), issuer / signature line (right)
    c.setFillColor(GREY)
    c.setFont("Helvetica", 12)
    c.drawString(80, 100, f"Issued on {data.issue_date.strftime('%d %B %Y')}")
    c.setStrokeColor(GREY)
    c.line(PAGE_W - 280, 105, PAGE_W - 80, 105)
    c.drawCentredString(PAGE_W - 180, 88, data.issuer)

    c.setFont("Helvetica", 8)
    c.drawString(80, 56, f"Certificate ID: {data.certificate_id}")


def generate_certificate_pdf(path: Path, data: CertificateData) -> None:
    """Render one certificate to ``path``.

    Written to a temp file and renamed so a crash never leaves a truncated PDF
    under the final name.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".pdf.tmp")
    try:
        c = canvas.Canvas(
            str(tmp),
            pagesize=(PAGE_W, PAGE_H),
            pageCompression=0,  # keeps text greppable/testable; files are only a few KB
            invariant=1,  # deterministic output (no timestamps/random ids)
        )
        c.setTitle(f"Certificate - {data.recipient_name}")
        c.setAuthor(data.issuer)
        _draw(c, data)
        c.showPage()
        c.save()
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
