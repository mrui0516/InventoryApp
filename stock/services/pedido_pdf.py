"""Render a quote to PDF.

PDF rather than a spreadsheet because that argument is already settled: a
customer opening an .xlsx in WhatsApp saw prices turn into dates, and the PDF
export is what fixed it. Same reasoning here - a quote is read on a phone.

The builder is deliberately dumb. It is handed finished strings and finished
Decimals: availability wording, the payment text, the totals. Deciding what a
product's availability says belongs with the rest of the catalogue's wording in
the view, not in two places.
"""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

INK = colors.HexColor('#10213a')
MUTED = colors.HexColor('#5b6675')
RULE = colors.HexColor('#cbd5e1')
HEAD_BG = colors.HexColor('#10213a')
ZEBRA = colors.HexColor('#f8fafc')


def pdf_safe(text):
    """Drop what the standard PDF fonts cannot draw.

    Helvetica is WinAnsi: Portuguese accents and the euro sign are fine, emoji
    are not. reportlab does not complain about a missing glyph - it just leaves
    a blank or a box on the paper - so anything outside the encoding is removed
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
    # Emoji usually sit next to a space; collapse what removing them left.
    return '\n'.join(' '.join(line.split()) for line in ''.join(out).split('\n'))


def money(value):
    """``1234.5`` -> ``1.234,50 EUR`` - the way it is written in Portugal."""
    if value is None:
        return ''
    whole = f'{value:,.2f}'
    return whole.replace(',', '\x00').replace('.', ',').replace('\x00', '.') + ' EUR'


def _styles():
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
        'section': ParagraphStyle('section', parent=base['Heading2'], fontSize=10,
                                  leading=13, spaceBefore=8, spaceAfter=3,
                                  textColor=INK),
        'cell': ParagraphStyle('cell', parent=base['Normal'], fontSize=8.5,
                               leading=11, alignment=TA_LEFT),
        'cellr': ParagraphStyle('cellr', parent=base['Normal'], fontSize=8.5,
                                leading=11, alignment=TA_RIGHT),
        'head': ParagraphStyle('head', parent=base['Normal'], fontSize=7.5,
                               leading=10, textColor=colors.white),
        'body': ParagraphStyle('body', parent=base['Normal'], fontSize=9,
                               leading=13),
        'note': ParagraphStyle('note', parent=base['Normal'], fontSize=8,
                               leading=11, textColor=MUTED),
    }


def _shop_header(profile, style, date_label, valid_until_label):
    lines = [x for x in (getattr(profile, 'address', ''),
                         getattr(profile, 'phone', ''),
                         getattr(profile, 'email', '')) if x]
    left = [Paragraph(pdf_safe(getattr(profile, 'name', '') or ''), style['shop'])]
    for line in lines:
        left.append(Paragraph(pdf_safe(line), style['shopline']))

    right = [Paragraph('PEDIDO', style['doctitle']),
             Paragraph(f'Data: {date_label}', style['docline'])]
    if valid_until_label:
        right.append(Paragraph(f'Válido até: {valid_until_label}', style['docline']))

    table = Table([[left, right]], colWidths=[108 * mm, 68 * mm])
    table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('LINEBELOW', (0, 0), (-1, 0), 0.8, RULE),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
    ]))
    return table


def _customer_block(style, name, phone, address):
    """Name, phone, address - never the NIF.

    Most customers here never gave a usable NIF, so printing the field would
    leave a blank line on the paperwork more often than not; the phone is what
    the shop actually reaches them on.
    """
    rows = []
    for label, value in (('Cliente', name), ('Telefone', phone),
                         ('Endereço', address)):
        if not value:
            continue
        rows.append([Paragraph(label, style['note']),
                     Paragraph(pdf_safe(value).replace('\n', '<br/>'), style['cell'])])
    if not rows:
        return None
    table = Table(rows, colWidths=[22 * mm, 154 * mm])
    table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
    ]))
    return table


def _items_table(style, lines):
    headers = ['Produto', 'EAN', 'Disponibilidade', 'Qtd.', 'Preço unit.', 'Total']
    widths = [66 * mm, 25 * mm, 32 * mm, 12 * mm, 20 * mm, 21 * mm]
    rows = [[Paragraph(h, style['head']) for h in headers]]
    for line in lines:
        rows.append([
            Paragraph(pdf_safe(line['title']), style['cell']),
            Paragraph(pdf_safe(line.get('barcode') or ''), style['cell']),
            Paragraph(pdf_safe(line.get('availability') or ''), style['cell']),
            Paragraph(str(line['quantity']), style['cellr']),
            Paragraph(money(line['unit_price']), style['cellr']),
            Paragraph(money(line['line_total']), style['cellr']),
        ])

    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HEAD_BG),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.4, RULE),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, ZEBRA]),
    ]))
    return table


def _totals_table(style, totals):
    rows = [('Subtotal', totals['subtotal'])]
    if totals['discount']:
        rows.append(('Desconto', -totals['discount']))
    if totals['shipping']:
        rows.append(('Transporte', totals['shipping']))
    for extra in totals['extras']:
        rows.append((pdf_safe(extra['label']), extra['amount']))

    body = [[Paragraph(label, style['cell']), Paragraph(money(value), style['cellr'])]
            for label, value in rows]
    body.append([Paragraph('<b>TOTAL</b>', style['cell']),
                 Paragraph('<b>' + money(totals['total']) + '</b>', style['cellr'])])

    table = Table(body, colWidths=[44 * mm, 32 * mm], hAlign='RIGHT')
    table.setStyle(TableStyle([
        ('LINEABOVE', (0, len(body) - 1), (-1, len(body) - 1), 0.8, INK),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
    ]))
    return table


def _payment_block(style, payment_info):
    safe = pdf_safe(payment_info).strip()
    if not safe:
        return []
    flow = [Paragraph('Pagamento', style['section'])]
    # Blank lines separate the options, so each block becomes its own paragraph
    # and the three methods stay apart on the page.
    for chunk in [c for c in safe.split('\n\n') if c.strip()]:
        flow.append(Paragraph(chunk.strip().replace('\n', '<br/>'), style['body']))
        flow.append(Spacer(1, 3))
    return [KeepTogether(flow)]


def build_pedido_pdf(*, profile, lines, totals, date_label, valid_until_label='',
                     customer_name='', customer_phone='', customer_address='',
                     note='', payment_info=''):
    """Render the quote to PDF bytes."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=17 * mm, rightMargin=17 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
        title='Pedido', author=pdf_safe(getattr(profile, 'name', '') or 'Pedido'))
    style = _styles()

    story = [_shop_header(profile, style, date_label, valid_until_label),
             Spacer(1, 7)]

    customer = _customer_block(style, customer_name, customer_phone, customer_address)
    if customer is not None:
        story += [customer, Spacer(1, 8)]

    story += [_items_table(style, lines), Spacer(1, 7),
              _totals_table(style, totals)]

    if note:
        story += [Spacer(1, 8), Paragraph('Observações', style['section']),
                  Paragraph(pdf_safe(note).replace('\n', '<br/>'), style['body'])]

    story += _payment_block(style, payment_info)

    doc.build(story)
    return buffer.getvalue()
