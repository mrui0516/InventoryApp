"""The shop's document look, shared by every PDF it hands out.

The quote (Pedido) set the style: the shop on the left and the document on
the right of a ruled header, a short block for the customer, one table with a
dark heading row and zebra lines, totals right-aligned under it. The receipt
and the sales report are built from the same pieces, so the three documents
read as one family instead of three different hands.

Everything here takes finished strings and Decimals. Wording and language
belong to the document that uses the piece - the receipt is Portuguese, the
internal report English.
"""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

INK = colors.HexColor('#10213a')
MUTED = colors.HexColor('#5b6675')
RULE = colors.HexColor('#cbd5e1')
HEAD_BG = colors.HexColor('#10213a')
ZEBRA = colors.HexColor('#f8fafc')
ACCENT = colors.HexColor('#2563eb')
ACCENT_SOFT = colors.HexColor('#eaf1ff')
TILE_BG = colors.HexColor('#f4f6fa')

PAGE_W, PAGE_H = A4
MARGIN = 17 * mm
# The page frame pads 6pt on each side. Tables sized to the full margin box
# hung 12pt past the right edge once they were left-aligned, so everything is
# measured against the inside of the frame.
FRAME_PAD = 6
CONTENT_W = PAGE_W - 2 * MARGIN - 2 * FRAME_PAD


def fill_first(*rest):
    """Column widths where the first column takes whatever the others leave."""
    return [CONTENT_W - sum(rest)] + list(rest)


def pdf_safe(text):
    """Drop what the standard PDF fonts cannot draw.

    Helvetica is WinAnsi: Portuguese accents and the euro sign are fine, emoji
    are not. reportlab does not complain about a missing glyph - it leaves a
    blank or a box on the paper - so anything outside the encoding is removed
    here rather than discovered by a customer.
    """
    out = []
    for char in (text or ''):
        if char in '\n\t':
            out.append(char)
            continue
        try:
            char.encode('cp1252')
        except UnicodeEncodeError:
            continue
        out.append(char)
    return '\n'.join(' '.join(line.split()) for line in ''.join(out).split('\n'))


def esc(text):
    """pdf_safe, then escaped for reportlab's mini-markup."""
    return (pdf_safe(text).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;'))


def money(value, suffix=' EUR'):
    """``1234.5`` -> ``1.234,50 EUR`` - the way it is written in Portugal."""
    if value is None:
        return ''
    whole = f'{value:,.2f}'
    return whole.replace(',', '\x00').replace('.', ',').replace('\x00', '.') + suffix


def styles():
    base = getSampleStyleSheet()
    return {
        'shop': ParagraphStyle('shop', parent=base['Heading1'], fontSize=16,
                               leading=19, spaceAfter=0, textColor=INK),
        'shopline': ParagraphStyle('shopline', parent=base['Normal'], fontSize=8,
                                   leading=11, textColor=MUTED),
        'doctitle': ParagraphStyle('doctitle', parent=base['Heading1'], fontSize=16,
                                   leading=19, alignment=TA_RIGHT, textColor=INK),
        'docline': ParagraphStyle('docline', parent=base['Normal'], fontSize=8.5,
                                  leading=12, alignment=TA_RIGHT, textColor=MUTED),
        'section': ParagraphStyle('section', parent=base['Heading2'], fontSize=10.5,
                                  leading=13, spaceBefore=9, spaceAfter=4, textColor=INK),
        'cell': ParagraphStyle('cell', parent=base['Normal'], fontSize=8.5,
                               leading=11, alignment=TA_LEFT, textColor=INK),
        'cellb': ParagraphStyle('cellb', parent=base['Normal'], fontSize=8.5,
                                leading=11, fontName='Helvetica-Bold', textColor=INK),
        'cellr': ParagraphStyle('cellr', parent=base['Normal'], fontSize=8.5,
                                leading=11, alignment=TA_RIGHT, textColor=INK),
        'cellrb': ParagraphStyle('cellrb', parent=base['Normal'], fontSize=8.5,
                                 leading=11, alignment=TA_RIGHT,
                                 fontName='Helvetica-Bold', textColor=INK),
        'sub': ParagraphStyle('sub', parent=base['Normal'], fontSize=7.5,
                              leading=10, textColor=MUTED),
        'head': ParagraphStyle('head', parent=base['Normal'], fontSize=7.5,
                               leading=10, textColor=colors.white),
        'headr': ParagraphStyle('headr', parent=base['Normal'], fontSize=7.5,
                                leading=10, textColor=colors.white, alignment=TA_RIGHT),
        'body': ParagraphStyle('body', parent=base['Normal'], fontSize=9, leading=13),
        'note': ParagraphStyle('note', parent=base['Normal'], fontSize=8, leading=11,
                               textColor=MUTED),
        'tile_label': ParagraphStyle('tile_label', parent=base['Normal'], fontSize=7,
                                     leading=9, textColor=MUTED, fontName='Helvetica-Bold'),
        'tile_value': ParagraphStyle('tile_value', parent=base['Normal'], fontSize=12.5,
                                     leading=15, textColor=INK, fontName='Helvetica-Bold'),
        'tile_value_hi': ParagraphStyle('tile_value_hi', parent=base['Normal'], fontSize=16,
                                        leading=19, textColor=colors.white,
                                        fontName='Helvetica-Bold'),
        'tile_label_hi': ParagraphStyle('tile_label_hi', parent=base['Normal'], fontSize=7,
                                        leading=9, textColor=colors.HexColor('#dbe7ff'),
                                        fontName='Helvetica-Bold'),
    }


def shop_header(profile, style, title, right_lines):
    """The shop left, the document right, a rule beneath both."""
    lines = [x for x in (getattr(profile, 'address', ''), getattr(profile, 'phone', ''),
                         getattr(profile, 'email', '')) if x]
    nif = getattr(profile, 'nif', '')
    left = [Paragraph(esc(getattr(profile, 'name', '') or ''), style['shop'])]
    for line in lines:
        left.append(Paragraph(esc(line), style['shopline']))
    if nif and nif not in lines:
        left.append(Paragraph(esc(f'NIF {nif}'), style['shopline']))

    right = [Paragraph(esc(title), style['doctitle'])]
    for line in right_lines:
        if line:
            right.append(Paragraph(esc(line), style['docline']))

    table = Table([[left, right]], colWidths=[CONTENT_W * 0.6, CONTENT_W * 0.4])
    table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('LINEBELOW', (0, 0), (-1, 0), 0.8, RULE),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
    ]))
    return table


def party_block(style, rows):
    """Label / value lines for whoever the document is about. Empty values
    are left off rather than printed as blanks."""
    body = []
    for label, value in rows:
        if not value:
            continue
        body.append([Paragraph(esc(label), style['note']),
                     Paragraph(esc(value).replace('\n', '<br/>'), style['cellb'])])
    if not body:
        return None
    table = Table(body, colWidths=[24 * mm, CONTENT_W - 24 * mm])
    table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
    ]))
    return table


def data_table(style, headers, rows, widths, *, right_cols=(), bold_last=False,
               repeat=1, extra=(), align='LEFT'):
    """The dark-headed zebra table. ``rows`` are lists of flowables or strings;
    strings are escaped and placed in the cell style for their column."""
    def cell(value, col, bold=False):
        if not isinstance(value, str):
            return value
        if col in right_cols:
            return Paragraph(esc(value), style['cellrb' if bold else 'cellr'])
        return Paragraph(esc(value), style['cellb' if bold else 'cell'])

    head = [Paragraph(esc(h), style['headr' if i in right_cols else 'head'])
            for i, h in enumerate(headers)]
    body = []
    for r, row in enumerate(rows):
        last = bold_last and r == len(rows) - 1
        body.append([cell(value, c, last) for c, value in enumerate(row)])
    table = Table([head] + body, colWidths=widths, repeatRows=repeat, hAlign=align)
    commands = [
        ('BACKGROUND', (0, 0), (-1, 0), HEAD_BG),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LINEBELOW', (0, 0), (-1, -1), 0.3, RULE),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, ZEBRA]),
    ]
    if bold_last and rows:
        commands += [('LINEABOVE', (0, -1), (-1, -1), 0.9, INK),
                     ('BACKGROUND', (0, -1), (-1, -1), TILE_BG)]
    table.setStyle(TableStyle(commands + list(extra)))
    return table


def totals_table(style, rows, grand):
    """Right-aligned money summary; ``grand`` is (label, value) in bold."""
    body = [[Paragraph(esc(label), style['cell']), Paragraph(esc(value), style['cellr'])]
            for label, value in rows]
    body.append([Paragraph(f'<b>{esc(grand[0])}</b>', style['cell']),
                 Paragraph(f'<b>{esc(grand[1])}</b>', style['cellr'])])
    table = Table(body, colWidths=[46 * mm, 34 * mm], hAlign='RIGHT')
    table.setStyle(TableStyle([
        ('LINEABOVE', (0, len(body) - 1), (-1, len(body) - 1), 0.9, INK),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
    ]))
    return table


def kpi_tiles(style, tiles):
    """A row of figure tiles; the first is the headline and is filled.

    ``tiles`` is a list of (label, value) pairs.
    """
    cells = []
    for i, (label, value) in enumerate(tiles):
        hi = i == 0
        cells.append([
            Paragraph(esc(label.upper()), style['tile_label_hi' if hi else 'tile_label']),
            Paragraph(esc(value), style['tile_value_hi' if hi else 'tile_value']),
        ])
    gap = 3 * mm
    # The headline tile is half as wide again: its figure is the largest and
    # the one people read first, and a four-digit sum must not wrap.
    unit = (CONTENT_W - gap * (len(tiles) - 1)) / (len(tiles) + 0.5)
    row, widths = [], []
    for i, cell in enumerate(cells):
        row.append(cell)
        widths.append(unit * 1.5 if i == 0 else unit)
        if i < len(cells) - 1:
            row.append('')
            widths.append(gap)
    table = Table([row], colWidths=widths)
    commands = [('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
                ('LEFTPADDING', (0, 0), (-1, -1), 7)]
    for i in range(len(cells)):
        col = i * 2
        commands.append(('BACKGROUND', (col, 0), (col, 0), HEAD_BG if i == 0 else TILE_BG))
    table.setStyle(TableStyle(commands))
    return table


def build(story, *, title, author, footer=None):
    """Render to PDF bytes. ``footer`` is a short string for every page foot."""
    buffer = BytesIO()

    def on_page(canvas, doc):
        if not footer:
            return
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN, 9 * mm, pdf_safe(footer))
        canvas.drawRightString(PAGE_W - MARGIN, 9 * mm, f'{doc.page}')
        canvas.restoreState()

    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=14 * mm, bottomMargin=16 * mm,
                            title=pdf_safe(title), author=pdf_safe(author or ''))
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buffer.getvalue()
