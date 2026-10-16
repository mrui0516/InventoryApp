"""The sales report behind Orders > Export PDF.

Internal, so in English like the app. It prints exactly what the Orders page
shows for the same choices - built from the same rows, after the same store
scope, search and payment filter - so the paper and the screen never disagree.

Three blocks, in the order somebody reads them: the figures for the range,
one line per day, then the orders themselves - one row each with the goods
in small type beneath. The old report broke the page at every day and put a
gridded table inside every order, so nothing stood out.
"""
from decimal import Decimal

from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Spacer

from . import doc_pdf

PAYMENT_EN = {'cash': 'Cash', 'card': 'Card', 'mbway': 'MB WAY'}
ZERO = Decimal('0.00')


def eur(value):
    return f'EUR {value or ZERO:,.2f}'


def _pay_columns(days):
    """Cash, Card, MB WAY always; any other method only if it was used."""
    seen = set()
    for day in days:
        seen.update(m for m, v in day['pay'].items() if v)
    extra = sorted(m for m in seen if m not in PAYMENT_EN)
    return ['cash', 'card', 'mbway'] + extra


def build_sales_report_pdf(*, profile, range_label, store_label, generated_label,
                           filter_label, summary, days, orders, show_profit):
    """``days``: [{'day', 'orders', 'qty', 'amount', 'pay'}] newest first.
    ``orders``: the Orders page rows, newest first."""
    style = doc_pdf.styles()
    story = [doc_pdf.shop_header(profile, style, 'SALES REPORT',
                                 [range_label, store_label, f'Generated {generated_label}']),
             Spacer(1, 8)]
    if filter_label:
        story += [Paragraph(doc_pdf.esc(filter_label), style['note']), Spacer(1, 4)]

    tiles = [('Sales', eur(summary['amount'])), ('Orders', str(summary['orders'])),
             ('Items', str(summary['qty'])), ('Per order', eur(summary['avg']))]
    if show_profit:
        tiles.append(('Gross profit', eur(summary['profit'])))
    story += [doc_pdf.kpi_tiles(style, tiles), Spacer(1, 6)]

    if not orders:
        story.append(Paragraph('No sales in this range.', style['body']))
        return doc_pdf.build(story, title='Sales report', author=getattr(profile, 'name', ''),
                             footer=f'Sales report - {range_label} - {store_label}')

    # payments, as a share of the range
    pays = summary['pay_display']
    total = summary['amount'] or Decimal('1')
    story.append(Paragraph('Payments', style['section']))
    story.append(doc_pdf.data_table(
        style, ['Method', 'Amount', 'Share'],
        [[PAYMENT_EN.get(p['code'], p['label']), eur(p['amount']),
          f"{(p['amount'] / total * 100):.0f}%"] for p in pays],
        [60 * mm, 40 * mm, 25 * mm], right_cols=(1, 2)))

    # one line per day
    columns = _pay_columns(days)
    story.append(Paragraph('By day', style['section']))
    headers = ['Date', 'Orders', 'Items', 'Sales'] + [PAYMENT_EN.get(m, m.title()) for m in columns]
    rows = []
    for day in days:
        rows.append([day['day'].strftime('%a %d/%m/%Y'), str(day['orders']), str(day['qty']),
                     eur(day['amount'])] + [eur(day['pay'].get(m, ZERO)) for m in columns])
    rows.append(['Total', str(summary['orders']), str(summary['qty']), eur(summary['amount'])]
                + [eur(summary['pay'].get(m, ZERO)) for m in columns])
    fixed = 28 * mm + 14 * mm + 12 * mm + 24 * mm
    pay_w = (doc_pdf.CONTENT_W - fixed) / max(len(columns), 1)
    story.append(doc_pdf.data_table(
        style, headers, rows, [28 * mm, 14 * mm, 12 * mm, 24 * mm] + [pay_w] * len(columns),
        right_cols=tuple(range(1, len(headers))), bold_last=True))

    # the orders: one row each, the goods in small type under the customer -
    # in the same cell, so a page break can never part an order from its goods
    story.append(Paragraph('Orders', style['section']))
    head = ['Date', 'Time', 'Order', 'Customer and goods', 'Items', 'Amount', 'Payment']
    widths = [20 * mm, 13 * mm, 15 * mm, 66 * mm, 11 * mm, 24 * mm]
    widths.append(doc_pdf.CONTENT_W - sum(widths))
    rows = []
    for row in orders:
        goods = ', '.join(f"{item.quantity}x {item.display_name}" for item in row['items'])
        rows.append([
            row['day'].strftime('%d/%m/%Y'),
            row['created_at'].strftime('%H:%M'),
            Paragraph(f"#{row['order_id']}" if row['order_id'] else 'Legacy', style['cellb']),
            [Paragraph(doc_pdf.esc(row['customer_name']), style['cellb']),
             Paragraph(doc_pdf.esc(goods), style['sub'])],
            str(row['qty']),
            Paragraph(eur(row['amount']), style['cellrb']),
            ' + '.join(PAYMENT_EN.get(p['code'], p['label']) for p in row['pay_display']) or '-',
        ])
    story.append(doc_pdf.data_table(style, head, rows, widths, right_cols=(4, 5)))

    return doc_pdf.build(story, title='Sales report', author=getattr(profile, 'name', ''),
                         footer=f'Sales report - {range_label} - {store_label}')
