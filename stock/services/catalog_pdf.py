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
import os
from io import BytesIO
from xml.sax.saxutils import escape

from PIL import Image as PILImage

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (Image, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

from ..models import Product, ProductType
from .product_contents import pdf_links
from .wholesale_catalog import brand_sort_key, units_sold

# Same meaning as the spreadsheet's fills, so the two never disagree.
STATE_COLOURS = {
    'in-stock': (colors.HexColor('#dcfce7'), colors.HexColor('#166534')),
    'low-stock': (colors.HexColor('#fef3c7'), colors.HexColor('#92400e')),
    'incoming': (colors.HexColor('#dbeafe'), colors.HexColor('#1e40af')),
    'out-stock': (colors.HexColor('#fee2e2'), colors.HexColor('#991b1b')),
}

THUMB_MM = 18
# Printed at 18mm, so a little over 200px is already more than the page can
# show. Anything larger is weight in the file and nothing on the paper.
THUMB_PX = 220


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
        'detail': ParagraphStyle('d', parent=base['Normal'], fontSize=7.5, leading=9.5,
                                 textColor=colors.HexColor('#5b6675')),
        'ean': ParagraphStyle('e', parent=base['Normal'], fontSize=7, leading=8.5,
                              textColor=colors.HexColor('#5b6675')),
        'type': ParagraphStyle('ty', parent=base['Heading2'], fontSize=12, spaceBefore=8, spaceAfter=2,
                               textColor=colors.HexColor('#10213a')),
        'head': ParagraphStyle('h', parent=base['Normal'], fontSize=7.5,
                               leading=9, textColor=colors.white),
    }


def _thumb(product):
    """The product's first photo, shrunk to the size it is actually printed at.

    reportlab embeds whatever file it is handed and only scales the *box* it
    draws into, so passing the original shop photo put a multi-megabyte image
    into the page for every product - a catalogue of 238 came to 267 MB and
    took a minute and a half to build, which is a download that never
    finishes. Resizing first makes each one a few kilobytes.

    A missing or unreadable file must not take the whole catalogue down, so
    anything that goes wrong here simply leaves the cell empty.
    """
    try:
        source = product.images.all()[0].image.path
        with PILImage.open(source) as picture:
            picture = picture.convert('RGB')
            picture.thumbnail((THUMB_PX, THUMB_PX))
            buffer = BytesIO()
            picture.save(buffer, format='JPEG', quality=80, optimize=True)
        buffer.seek(0)
        return Image(buffer, width=THUMB_MM * mm, height=THUMB_MM * mm,
                     kind='proportional')
    except Exception:
        return None


def _brands_in_order(brand_groups):
    return sorted(brand_groups.items(), key=lambda item: brand_sort_key(item[0], len(item[1])))


def _sections(products, sold):
    """``(type or None, line, products)`` type by type, then each type's best-selling
    lines first and the products with no line last. The type comes only with its
    first line, which is where its heading goes."""
    by_type = {}
    for product in products:
        by_type.setdefault(product.product_type_id, {}).setdefault(product.export_model, []).append(product)
    for code in sorted(by_type, key=lambda c: ProductType.for_code(c).sort_order):
        kind = ProductType.for_code(code)
        lines = sorted(by_type[code].items(),
                       key=lambda item: (not any((p.model or '').strip() for p in item[1]),
                                         -sum(sold.get(p.pk, 0) for p in item[1])))
        for model_name, rows in lines:
            yield kind, model_name, rows
            kind = None


def _detail_line(prefix, items):
    """``Fragr\u00e2ncias: Khamrah \u00b7 Yara``, each name a jump when it has a target."""
    parts = []
    for label, target in items:
        text = escape(label)
        parts.append(f'<a href="#p{target}" color="#1d4ed8"><u>{text}</u></a>' if target else text)
    return f'{prefix} ' + ' \u00b7 '.join(parts)


def _title_cell(product, links, style):
    cell = [Paragraph(f'<a name="p{product.pk}"/>{product.barcode or "-"}', style['ean']),
            Paragraph(product.export_title, style['cell'])]
    if not links:
        return cell
    if links['contents']:
        prefix = 'Cont\u00e9m:' if product.type_info.is_gift else 'Fragr\u00e2ncias:'
        cell.append(Paragraph(_detail_line(prefix, links['contents']), style['detail']))
    if links['also_in']:
        cell.append(Paragraph(_detail_line('Tamb\u00e9m em:', links['also_in']), style['detail']))
    return cell


def _brand_logo(name):
    """The brand's logo as a flowable, or None: a missing file must not stop an export."""
    from ..models import Brand
    brand = Brand.objects.filter(name__iexact=name).exclude(logo='').first()
    if brand is None or not os.path.exists(brand.logo.path):
        return None
    return Image(brand.logo.path, width=12 * mm, height=12 * mm, kind='proportional')


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

    everything = [p for products in brand_groups.values() for p in products]
    sold = units_sold([p.pk for p in everything])
    headers = (['Foto'] if include_images else []) + ['Produto', 'Género']
    widths = ([THUMB_MM * mm + 4 * mm] if include_images else []) + [70 * mm, 18 * mm]
    if price_mode in {'retail', 'both'}:
        headers.append('PVP')
        widths.append(20 * mm)
    if price_mode in {'wholesale', 'both'}:
        headers.append('Preço grossista')
        widths.append(24 * mm)
    headers.append('Disponibilidade')
    widths.append(30 * mm)
    # Beside availability rather than under it: one product, one line.
    show_incoming = any(getattr(p, 'export_incoming_note', False)
                        for products in brand_groups.values() for p in products)
    if show_incoming:
        headers.append('A chegar')
        widths.append(28 * mm)

    links = pdf_links(everything)
    for index, (brand_name, products) in enumerate(_brands_in_order(brand_groups)):
        if index:
            story.append(PageBreak())
        title = Paragraph(f'{brand_name} | Lista de Produtos', style['title'])
        logo = _brand_logo(brand_name)
        if logo:
            head = Table([[logo, title]], colWidths=[14 * mm, None])
            head.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (-1, -1), 0)]))
            story.append(head)
        else:
            story.append(title)
        if legend:
            story.append(Paragraph(legend, style['legend']))

        for kind, model_name, model_products in _sections(products, sold):
            if kind is not None:
                story.append(Paragraph(kind.customer_label or kind.name, style['type']))
            story.append(Paragraph(model_name, style['section']))

            rows = [[Paragraph(h, style['head']) for h in headers]]
            highlights = []
            for product in model_products:
                row = []
                if include_images:
                    row.append(_thumb(product) or '')
                row.append(_title_cell(product, links.get(product.pk), style))
                row.append(Paragraph(Product.GENDER_SHOPIFY_TAGS.get(product.gender, '-'), style['cell']))
                # The same numbers the spreadsheet prints: PVP is the till
                # price plus the uplift, wholesale carries this export's
                # discount. Both are worked out before we get here.
                if price_mode in {'retail', 'both'}:
                    row.append(_money(getattr(product, 'export_pvp', None)))
                if price_mode in {'wholesale', 'both'}:
                    row.append(_money(getattr(product, 'export_wholesale', None)))
                row.append(Paragraph(product.export_availability, style['cell']))
                incoming = getattr(product, 'export_incoming_note', False)
                if show_incoming:
                    row.append(Paragraph('Brevemente em stock', style['cell'])
                               if incoming else '')
                rows.append(row)
                highlights.append((len(rows) - 1,
                                   getattr(product, 'export_state', ''), incoming))

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
            # Availability is the last column unless the incoming one is
            # present, in which case it is the one before it.
            state_col = -2 if show_incoming else -1
            for row_number, state, incoming in highlights:
                fill, ink = STATE_COLOURS.get(state, (None, None))
                if fill is not None:
                    commands.append(('BACKGROUND', (state_col, row_number),
                                     (state_col, row_number), fill))
                    commands.append(('TEXTCOLOR', (state_col, row_number),
                                     (state_col, row_number), ink))
                if incoming and show_incoming:
                    fill, ink = STATE_COLOURS['incoming']
                    commands.append(('BACKGROUND', (-1, row_number), (-1, row_number), fill))
                    commands.append(('TEXTCOLOR', (-1, row_number), (-1, row_number), ink))
            table.setStyle(TableStyle(commands))
            story.append(table)
            story.append(Spacer(1, 3))

    if not story:
        story.append(Paragraph('Nenhum produto encontrado.', style['cell']))

    doc.build(story)
    buffer.seek(0)
    return buffer


def _money(value):
    return '' if value is None else f'{value:,.2f} EUR'
