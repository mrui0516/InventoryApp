"""Customer product list as a PDF.

Why this exists: the Excel export embeds photos as *floating drawings*
anchored over cells. That is valid xlsx and Excel on a desktop renders it
perfectly - which is why the file looks right when you download it. But
WhatsApp's document preview, Google Sheets and most phone spreadsheet apps
drop floating pictures on import, so the customer opens the same file and
sees a list with an empty Image column.

Nothing is wrong with the file, and no change to it fixes this: the pictures
are simply not something those viewers draw. A PDF has no such problem. The
photos are part of the page, so every phone, every preview and every printer
shows them, and the layout cannot be reflowed away by whichever app opens it.

So: Excel for anyone who wants to sort and filter, PDF for anyone sending a
list to a customer.
"""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (Image, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

# Same meaning as the spreadsheet's fills, so the two never disagree.
STATE_COLOURS = {
    'in-stock': (colors.HexColor('#dcfce7'), colors.HexColor('#166534')),
    'low-stock': (colors.HexColor('#fef3c7'), colors.HexColor('#92400e')),
    'incoming': (colors.HexColor('#dbeafe'), colors.HexColor('#1e40af')),
    'out-stock': (colors.HexColor('#fee2e2'), colors.HexColor('#991b1b')),
}

THUMB_MM = 18


def _styles():
    base = getSampleStyleSheet()
    return {
        'title': ParagraphStyle('t', parent=base['Heading1'], fontSize=15,
                                spaceAfter=2, textColor=colors.HexColor('#10213a')),
        'legend': ParagraphStyle('l', parent=base['Normal'], fontSize=7.5,
                                 textColor=colors.HexColor('#5b6675'), spaceAfter=6),
        'section': ParagraphStyle('s', parent=base['Heading2'], fontSize=10,
                                  spaceBefore=6, spaceAfter=2,
                                  textColor=colors.HexColor('#10213a')),
        'cell': ParagraphStyle('c', parent=base['Normal'], fontSize=8,
                               leading=10, alignment=TA_LEFT),
        'head': ParagraphStyle('h', parent=base['Normal'], fontSize=7.5,
                               leading=9, textColor=colors.white),
    }


def _thumb(product):
    """The product's first photo, sized for the table, or None.

    A missing or unreadable file must not take the whole catalogue down, so
    anything that goes wrong here simply leaves the cell empty.
    """
    try:
        image = product.images.all()[0]
        return Image(image.image.path, width=THUMB_MM * mm, height=THUMB_MM * mm,
                     kind='proportional')
    except Exception:
        return None


def build_catalog_pdf(brand_groups, *, price_mode='retail', include_images=True,
                      legend='', shop_name='KHAN PERFUME'):
    """Render the grouped products to PDF bytes.

    ``brand_groups`` is ``{brand: [products]}`` where each product already
    carries the export_* attributes the spreadsheet uses, so both exports
    describe stock the same way.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title='Product list', author=shop_name)
    style = _styles()
    story = []

    headers = (['Photo'] if include_images else []) + ['Product', 'EAN']
    widths = ([THUMB_MM * mm + 4 * mm] if include_images else []) + [62 * mm, 26 * mm]
    if price_mode in {'retail', 'both'}:
        headers.append('Retail')
        widths.append(20 * mm)
    if price_mode in {'wholesale', 'both'}:
        headers.append('Wholesale')
        widths.append(22 * mm)
    headers.append('Availability')
    widths.append(28 * mm)

    for index, (brand_name, products) in enumerate(brand_groups.items()):
        if index:
            story.append(PageBreak())
        story.append(Paragraph(f'{brand_name} | Product list', style['title']))
        if legend:
            story.append(Paragraph(legend, style['legend']))

        by_model = {}
        for product in products:
            by_model.setdefault(product.export_model, []).append(product)

        for model_name, model_products in by_model.items():
            story.append(Paragraph(model_name, style['section']))

            rows = [[Paragraph(h, style['head']) for h in headers]]
            highlights = []
            for position, product in enumerate(model_products, start=1):
                row = []
                if include_images:
                    row.append(_thumb(product) or '')
                row.append(Paragraph(product.export_title, style['cell']))
                row.append(Paragraph(product.barcode or '-', style['cell']))
                if price_mode in {'retail', 'both'}:
                    row.append(_money(product.default_price))
                if price_mode in {'wholesale', 'both'}:
                    row.append(_money(product.wholesale_price))
                row.append(Paragraph(product.export_availability, style['cell']))
                rows.append(row)
                highlights.append((position, getattr(product, 'export_state', '')))

            table = Table(rows, colWidths=widths, repeatRows=1)
            commands = [
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#334155')),
                ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#cbd5e1')),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('ALIGN', (-1, 0), (-1, -1), 'CENTER'),
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                ('TOPPADDING', (0, 0), (-1, -1), 2),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ]
            for row_number, state in highlights:
                fill, ink = STATE_COLOURS.get(state, (None, None))
                if fill is not None:
                    commands.append(('BACKGROUND', (-1, row_number), (-1, row_number), fill))
                    commands.append(('TEXTCOLOR', (-1, row_number), (-1, row_number), ink))
            table.setStyle(TableStyle(commands))
            story.append(table)
            story.append(Spacer(1, 3))

    if not story:
        story.append(Paragraph('No products matched.', style['cell']))

    doc.build(story)
    buffer.seek(0)
    return buffer


def _money(value):
    return '' if value is None else f'EUR {value:,.2f}'
