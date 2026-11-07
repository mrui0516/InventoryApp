"""The purchasing side of Orders on paper. Internal, so in English like the app.

Two documents. The report is what Orders > Purchases shows for the same choices:
what came in, from whom, what each shop received, then every delivery. The other
is one delivery on its own, to check against an invoice or hand to the supplier.
"""
from collections import OrderedDict
from decimal import Decimal

from reportlab.lib.units import mm
from reportlab.platypus import CondPageBreak, KeepTogether, Paragraph, Spacer, Table, TableStyle

from . import doc_pdf

ZERO = Decimal('0.00')


def eur(value):
    return f'EUR {value or ZERO:,.2f}'


def _suppliers(groups):
    rows = OrderedDict()
    for group in sorted(groups, key=lambda g: -g['cost']):
        row = rows.setdefault(group['supplier_name'], {'receipts': 0, 'qty': 0, 'cost': ZERO, 'owing': ZERO})
        row['receipts'] += 1
        row['qty'] += group['qty']
        row['cost'] += group['cost']
        row['owing'] += group.get('order_owing') or ZERO
    return sorted(rows.items(), key=lambda kv: -kv[1]['cost'])


def _shops_text(group):
    return ' / '.join(f"{shop['label']} {eur(shop['cost'])}" for shop in group['shops'] if shop['qty'])


def _due_text(group):
    if not group.get('order_owing'):
        return 'Paid' if group.get('order_owing') is not None else '-'
    day = group.get('next_due')
    when = f" {'overdue' if group.get('overdue') else 'due'} {day.strftime('%d/%m/%Y')}" if day else ''
    return f"{eur(group['order_owing'])}{when}"


def _lines_table(style, items, shops):
    """A delivery's goods with their prices: each shop's units in a column of its
    own when every shop is shown; one shop's part when one shop is chosen."""
    head = ['EAN', 'Product', 'Qty'] + [label for _id, label in shops] + ['Unit cost', 'Total']
    fixed = [26 * mm, 11 * mm] + [17 * mm] * len(shops) + [21 * mm, 23 * mm]
    widths = [fixed[0], doc_pdf.CONTENT_W - sum(fixed)] + fixed[1:]
    rows = []
    for item in items:
        product = item.product
        rows.append([(product.barcode or '') if product else '', item.display_name, str(item.quantity)]
                    + [str(item.by_shop.get(shop_id, 0) or '') for shop_id, _label in shops]
                    + [eur(item.cost_price), eur(item.line_total)])
    rows.append(['', 'Total', str(sum(item.quantity for item in items))]
                + [str(sum(item.by_shop.get(shop_id, 0) for item in items) or '') for shop_id, _label in shops]
                + ['', eur(sum((item.line_total for item in items), ZERO))])
    return doc_pdf.data_table(style, head, rows, widths, right_cols=tuple(range(2, len(head))), bold_last=True)


def _delivery_head(style, group, with_payments, one_shop):
    """The delivery's line above its goods: what, from whom, and the facts."""
    title = f"{group['day'].strftime('%d/%m/%Y')}  {group['short_ref']}  {group['supplier_name']}"
    if group['invoice_no']:
        title += f"  -  invoice {group['invoice_no']}"
    facts = ['On the way' if group['pending'] else
             f"Received {group['received_on'].strftime('%d/%m/%Y')}" if group.get('received_on') else '']
    if group.get('iva_rate'):
        facts.append(f"IVA {group['iva_rate']:.0f}% included")
    if group.get('shipping'):
        facts.append('its share of shipping included' if one_shop else
                     f"shipping EUR {group['shipping']:,.2f} included")
    if group['shops']:
        facts.append(_shops_text(group))
    if with_payments and group.get('order_owing') is not None:
        facts.append(f'to pay {_due_text(group)}')
    left = [Paragraph(doc_pdf.esc(title), style['cellb']),
            Paragraph(doc_pdf.esc(' - '.join(f for f in facts if f)), style['sub'])]
    table = Table([[left, Paragraph(eur(group['cost']), style['cellrb'])]],
                  colWidths=[doc_pdf.CONTENT_W - 32 * mm, 32 * mm])
    table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), doc_pdf.TILE_BG), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                               ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
    return table


def build_inbound_report_pdf(*, profile, range_label, store_label, generated_label, filter_label, groups,
                             shop_totals, date_label, with_payments, shop_columns=(), one_shop=False):
    """``groups``: the Purchases page's rows, newest first, already cut to one
    shop's part when one is chosen. ``shop_columns``: ``[(id, label)]`` when
    every shop is shown."""
    style = doc_pdf.styles()
    footer = f'Inbound report - {range_label} - {store_label}'
    story = [doc_pdf.shop_header(profile, style, 'INBOUND REPORT',
                                 [range_label, store_label, date_label, f'Generated {generated_label}']),
             Spacer(1, 8)]
    if filter_label:
        story += [Paragraph(doc_pdf.esc(filter_label), style['note']), Spacer(1, 4)]

    total = sum((group['cost'] for group in groups), ZERO)
    tiles = [('Purchased', eur(total)), ('Receipts', str(len(groups))),
             ('Items', str(sum(group['qty'] for group in groups)))]
    if with_payments:
        tiles.append(('Owing', eur(sum((group.get('order_owing') or ZERO for group in groups), ZERO))))
    story += [doc_pdf.kpi_tiles(style, tiles), Spacer(1, 6)]

    if not groups:
        story.append(Paragraph('Nothing came in during this range.', style['body']))
        return doc_pdf.build(story, title='Inbound report', author=getattr(profile, 'name', ''), footer=footer)

    if shop_totals:
        story.append(Paragraph('By shop', style['section']))
        rows = [[shop['label'], str(shop['qty']), eur(shop['cost']),
                 f"{(shop['cost'] / total * 100) if total else 0:.0f}%"] for shop in shop_totals]
        rows.append(['Total', str(sum(shop['qty'] for shop in shop_totals)),
                     eur(sum((shop['cost'] for shop in shop_totals), ZERO)), ''])
        story.append(doc_pdf.data_table(style, ['Shop', 'Units', 'Cost', 'Share'], rows,
                                        [doc_pdf.CONTENT_W - 85 * mm, 20 * mm, 40 * mm, 25 * mm], right_cols=(1, 2, 3), bold_last=True))

    story.append(Paragraph('By supplier', style['section']))
    head = ['Supplier', 'Receipts', 'Units', 'Cost'] + (['Owing'] if with_payments else [])
    rows = [[name, str(row['receipts']), str(row['qty']), eur(row['cost'])]
            + ([eur(row['owing'])] if with_payments else []) for name, row in _suppliers(groups)]
    widths = [20 * mm, 16 * mm, 30 * mm] + ([30 * mm] if with_payments else [])
    widths.insert(0, doc_pdf.CONTENT_W - sum(widths))
    story.append(doc_pdf.data_table(style, head, rows, widths, right_cols=tuple(range(1, len(head)))))

    story.append(Paragraph('Deliveries', style['section']))
    for group in groups:
        # The heading never ends a page alone; a long delivery runs on, its
        # column heads repeated.
        story.append(CondPageBreak(30 * mm))
        story.append(_delivery_head(style, group, with_payments, one_shop))
        story.append(_lines_table(style, group['items'], list(shop_columns)))
        story.append(Spacer(1, 7))
    return doc_pdf.build(story, title='Inbound report', author=getattr(profile, 'name', ''), footer=footer)


def build_inbound_doc_pdf(*, profile, title, right_lines, party, items, shops, summary, payment=None):
    """One delivery. ``shops``: ``[(id, label)]`` columns for each shop's units,
    ``summary``: ``[(label, value)]`` lines above the total, ``payment``:
    ``{paid, owing, schedule}`` for those who may see it."""
    style = doc_pdf.styles()
    story = [doc_pdf.shop_header(profile, style, title, right_lines), Spacer(1, 8)]
    block = doc_pdf.party_block(style, party)
    if block is not None:
        story += [block, Spacer(1, 6)]

    story.append(_lines_table(style, items, shops))
    if summary:
        story += [Spacer(1, 3), Paragraph(doc_pdf.esc(' - '.join(f'{label} {value}' for label, value in summary)),
                                          style['note'])]

    if payment:
        parts = [Paragraph('Payment', style['section'])]
        rows = [[part['on'].strftime('%d/%m/%Y'), eur(part['amount']), eur(part['left']) if part['left'] else 'Paid']
                for part in payment['schedule']]
        parts.append(doc_pdf.data_table(style, ['Due', 'Amount', 'Left to pay'], rows,
                                        [40 * mm, 35 * mm, 35 * mm], right_cols=(1, 2)))
        parts.append(Paragraph(f"Paid {eur(payment['paid'])} - owing {eur(payment['owing'])}", style['note']))
        story.append(KeepTogether(parts))

    name = title.title()
    return doc_pdf.build(story, title=name, author=getattr(profile, 'name', ''), footer=name)
