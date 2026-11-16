"""Executive Markdown -> PDF generator using ReportLab.

Produces high-end, beautifully formatted executive research briefs with:
- Modern typography, color palettes, and visual hierarchy
- Cover/header metadata block (date, scope, citation verification badge)
- Executive Summary highlight card with accent border
- Clean section dividers and interactive clickable source references
- Running headers, footers, and dynamic page numbering (Page X of Y)
"""
import logging
import os
import re
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

from app.errors import ReportError

log = logging.getLogger(__name__)

# Colors
C_DARK = "#0F172A"       # Slate 900: Primary headings
C_ACCENT = "#1D4ED8"     # Blue 700: Brand accent, subheadings, badges
C_TEXT = "#334155"       # Slate 700: High-legibility body text
C_MUTED = "#64748B"      # Slate 500: Metadata & footers
C_BORDER = "#E2E8F0"     # Slate 200: Dividers & subtle borders
C_CARD_BG = "#F8FAFC"    # Slate 50: Summary & callout card background
C_CARD_BORDER = "#2563EB"# Blue 600: Accent card border

_FONT_CANDIDATES = [
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]
_font_name: str | None = None
_SMART = {
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2026": "...", "\u2022": "•", "\u00a0": " "
}

# Regex to strip 4-byte UTF-8 emojis that break in standard PDF TrueType fonts
_EMOJI_RE = re.compile(
    r"[\U00010000-\U0010ffff]"  # Surrogate/astral plane emojis
    r"|[\u2600-\u27bf]"          # Misc symbols & dingbats
    r"|[\u2300-\u23ff]"          # Misc technical
    r"|[\u2b50-\u2b55]"          # Star & shapes
)


def _font() -> str:
    """Use a Unicode TrueType font when one exists on this machine, else built-in Helvetica."""
    global _font_name
    if _font_name:
        return _font_name
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for regular, bold in _FONT_CANDIDATES:
        if os.path.exists(regular) and os.path.exists(bold):
            try:
                pdfmetrics.registerFont(TTFont("ReportFont", regular))
                pdfmetrics.registerFont(TTFont("ReportFont-Bold", bold))
                pdfmetrics.registerFontFamily(
                    "ReportFont",
                    normal="ReportFont",
                    bold="ReportFont-Bold",
                    italic="ReportFont",
                    boldItalic="ReportFont-Bold",
                )
                _font_name = "ReportFont"
                return _font_name
            except Exception as e:  # noqa: BLE001
                log.warning("Could not load font %s: %s", regular, e)
    _font_name = "Helvetica"
    return _font_name


def _clean_text(s: str) -> str:
    """Clean typography and strip emojis so PDF fonts don't produce null bytes or broken squares."""
    s = _EMOJI_RE.sub("", s)
    for k, v in _SMART.items():
        s = s.replace(k, v)
    return s.strip()


def _format_inline(s: str) -> str:
    """Convert markdown bold/italic/links to ReportLab XML tags."""
    clean = _clean_text(s)
    escaped = escape(clean)
    # Bold: **text** or __text__
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"__(.+?)__", r"<b>\1</b>", escaped)
    # Italic: *text* or _text_
    escaped = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", escaped)
    # Code spans: `text`
    escaped = re.sub(r"`(.+?)`", rf'<font color="{C_ACCENT}"><b>\1</b></font>', escaped)
    # Citations: [1], [2][3] -> styled badges
    escaped = re.sub(
        r"\[(\d{1,2})\]",
        rf'<font color="{C_ACCENT}"><b>[\1]</b></font>',
        escaped,
    )
    return escaped


def _build_canvas_class(title: str, font: str):
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas

    clean_title = _clean_text(title)
    if len(clean_title) > 65:
        clean_title = clean_title[:62] + "..."

    class NumberedCanvas(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            num_pages = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self.draw_decorations(num_pages)
                super().showPage()
            super().save()

        def draw_decorations(self, page_count):
            self.saveState()
            self.setFont(f"{font}-Bold" if font != "Helvetica" else "Helvetica-Bold", 8)

            page_w, page_h = self._pagesize
            margin = 1.8 * cm

            # Running Header (pages > 1)
            if self._pageNumber > 1:
                self.setFillColor(colors.HexColor(C_MUTED))
                self.drawString(margin, page_h - 1.2 * cm, clean_title.upper())
                self.setFont(font, 8)
                self.drawRightString(page_w - margin, page_h - 1.2 * cm, "DEEP RESEARCH REPORT")
                self.setStrokeColor(colors.HexColor(C_BORDER))
                self.setLineWidth(0.6)
                self.line(margin, page_h - 1.35 * cm, page_w - margin, page_h - 1.35 * cm)

            # Running Footer (all pages)
            self.setStrokeColor(colors.HexColor(C_BORDER))
            self.setLineWidth(0.6)
            self.line(margin, 1.4 * cm, page_w - margin, 1.4 * cm)

            self.setFont(font, 8)
            self.setFillColor(colors.HexColor(C_MUTED))
            self.drawString(margin, 1.0 * cm, "Verified Deep Research • Autonomous Intelligence")
            page_text = f"Page {self._pageNumber} of {page_count}"
            self.drawRightString(page_w - margin, 1.0 * cm, page_text)

            self.restoreState()

    return NumberedCanvas


def markdown_to_pdf_bytes(title: str, md: str) -> bytes:
    """Render structured research markdown into an executive PDF with ReportLab. Raises ReportError."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        font = _font()
        bold = f"{font}-Bold" if font != "Helvetica" else "Helvetica-Bold"

        # Styles definition
        base = ParagraphStyle("base", fontName=font, fontSize=9.5, leading=14.5, textColor=colors.HexColor(C_TEXT))
        styles = {
            "badge": ParagraphStyle("badge", fontName=bold, fontSize=8, leading=10, textColor=colors.HexColor(C_ACCENT), spaceAfter=4),
            "title": ParagraphStyle("title", parent=base, fontName=bold, fontSize=20, leading=24, textColor=colors.HexColor(C_DARK), spaceAfter=8),
            "meta": ParagraphStyle("meta", parent=base, fontSize=8.5, leading=12, textColor=colors.HexColor(C_MUTED), spaceAfter=14),
            "h2": ParagraphStyle("h2", parent=base, fontName=bold, fontSize=13, leading=17, textColor=colors.HexColor(C_DARK), spaceBefore=14, spaceAfter=4),
            "h3": ParagraphStyle("h3", parent=base, fontName=bold, fontSize=10.5, leading=14, textColor=colors.HexColor(C_ACCENT), spaceBefore=9, spaceAfter=3),
            "body": ParagraphStyle("body", parent=base, spaceAfter=8),
            "bullet": ParagraphStyle("bullet", parent=base, leftIndent=14, bulletIndent=4, spaceAfter=4),
            "card_body": ParagraphStyle("card_body", parent=base, fontSize=9.5, leading=14.5, textColor=colors.HexColor(C_TEXT)),
            "card_title": ParagraphStyle("card_title", fontName=bold, fontSize=9.5, leading=12, textColor=colors.HexColor(C_ACCENT), spaceAfter=4),
            "ref_item": ParagraphStyle("ref_item", parent=base, fontSize=8.5, leading=12.5, textColor=colors.HexColor(C_TEXT), spaceAfter=4),
        }

        buf = BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            leftMargin=1.8 * cm,
            rightMargin=1.8 * cm,
            topMargin=1.8 * cm,
            bottomMargin=1.8 * cm,
            title=_clean_text(title),
        )

        story = []

        # 1. Header Metadata Block
        now_str = datetime.now().strftime("%B %d, %Y")
        story.append(Paragraph("EXECUTIVE RESEARCH DOSSIER • CITATION GROUNDED", styles["badge"]))
        story.append(Paragraph(_format_inline(title), styles["title"]))
        meta_line = f"Generated: {now_str} &nbsp;|&nbsp; Synthesis: Autonomous Deep Search &nbsp;|&nbsp; Status: Verified & Fact-Checked"
        story.append(Paragraph(meta_line, styles["meta"]))
        story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor(C_ACCENT), spaceAfter=14))

        # 2. Process Markdown Content
        lines = md.splitlines()
        first_h1_skipped = False
        in_exec_summary = False
        exec_summary_paragraphs = []
        in_references = False

        i = 0
        while i < len(lines):
            raw = lines[i].strip()
            i += 1

            if not raw:
                continue

            # Skip duplicate H1 if it matches the title
            if raw.startswith("# "):
                if not first_h1_skipped:
                    first_h1_skipped = True
                    continue
                story.append(Paragraph(_format_inline(raw[2:]), styles["h2"]))
                continue

            # Heading 2 (Sections)
            if raw.startswith("## "):
                section_title = raw[3:].strip()
                lower_title = section_title.lower()

                # Flush executive summary card if we were in one
                if in_exec_summary:
                    card_content = [Paragraph("<b>EXECUTIVE SUMMARY</b>", styles["card_title"]), Spacer(1, 4)]
                    for p in exec_summary_paragraphs:
                        card_content.append(Paragraph(_format_inline(p), styles["card_body"]))
                        card_content.append(Spacer(1, 4))
                    card_table = Table([[card_content]], colWidths=[doc.width])
                    card_table.setStyle(TableStyle([
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(C_CARD_BG)),
                        ("LINELEFT", (0, 0), (0, -1), 3.5, colors.HexColor(C_CARD_BORDER)),
                        ("TOPPADDING", (0, 0), (-1, -1), 8),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                        ("LEFTPADDING", (0, 0), (-1, -1), 12),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(C_BORDER)),
                    ]))
                    story.append(card_table)
                    story.append(Spacer(1, 10))
                    in_exec_summary = False
                    exec_summary_paragraphs = []

                if "executive summary" in lower_title:
                    in_exec_summary = True
                    continue

                if "references" in lower_title or "sources" in lower_title:
                    in_references = True
                    story.append(Spacer(1, 8))
                    story.append(Paragraph("References & Verified Sources", styles["h2"]))
                    story.append(HRFlowable(width="100%", thickness=0.75, color=colors.HexColor(C_BORDER), spaceAfter=8))
                    continue

                # Standard Section Heading
                story.append(Paragraph(_format_inline(section_title), styles["h2"]))
                story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor(C_BORDER), spaceAfter=6))
                continue

            # Heading 3 (Sub-sections)
            if raw.startswith("### "):
                story.append(Paragraph(_format_inline(raw[4:]), styles["h3"]))
                continue

            # Executive Summary accumulator
            if in_exec_summary:
                exec_summary_paragraphs.append(raw)
                continue

            # References formatter
            if in_references:
                # Format references with clean clickable links
                # e.g. [1] Title - https://url
                ref_match = re.match(r"^\[(\d{1,2})\]\s*(.+?)(?:\s*[-–—:]\s*(https?://\S+))?$", raw)
                if ref_match:
                    num = ref_match.group(1)
                    ref_title = escape(ref_match.group(2).strip())
                    ref_url = ref_match.group(3)
                    if ref_url:
                        clean_url = escape(ref_url.strip())
                        item_html = f'<b>[{num}]</b> {ref_title} &nbsp;—&nbsp; <a href="{clean_url}" color="{C_ACCENT}"><u>{clean_url}</u></a>'
                    else:
                        item_html = f"<b>[{num}]</b> {ref_title}"
                    story.append(Paragraph(item_html, styles["ref_item"]))
                else:
                    story.append(Paragraph(_format_inline(raw), styles["ref_item"]))
                continue

            # Bullets
            if re.match(r"^[-*•]\s+", raw):
                clean_bullet = re.sub(r"^[-*•]\s+", "", raw)
                story.append(Paragraph(_format_inline(clean_bullet), styles["bullet"], bulletText="•"))
                continue

            # Standard Body Paragraph
            story.append(Paragraph(_format_inline(raw), styles["body"]))

        # If executive summary was at the end or not closed
        if in_exec_summary and exec_summary_paragraphs:
            card_content = [Paragraph("<b>EXECUTIVE SUMMARY</b>", styles["card_title"]), Spacer(1, 4)]
            for p in exec_summary_paragraphs:
                card_content.append(Paragraph(_format_inline(p), styles["card_body"]))
                card_content.append(Spacer(1, 4))
            card_table = Table([[card_content]], colWidths=[doc.width])
            card_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(C_CARD_BG)),
                ("LINELEFT", (0, 0), (0, -1), 3.5, colors.HexColor(C_CARD_BORDER)),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(C_BORDER)),
            ]))
            story.append(card_table)
            story.append(Spacer(1, 10))

        canvas_maker = _build_canvas_class(title, font)
        doc.build(story, canvasmaker=canvas_maker)
        return buf.getvalue()

    except Exception as e:  # noqa: BLE001
        raise ReportError(f"PDF build failed: {type(e).__name__}: {str(e)[:200]}") from e
