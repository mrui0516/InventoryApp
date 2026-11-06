"""Orders > Analysis on paper. Internal, so in English like the app.

It reads as the page does: the range's figures, the year month by month, then
what sold and how it was paid. Profit only for those who see it on screen.
"""
from decimal import Decimal

from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, Spacer

from . import doc_pdf

ZERO = Decimal('0.00')
CHART_H = 42 * mm


def eur(value):
    return f'EUR {value or 0:,.2f}'


def _year_chart(rows):
    """Twelve bars, the amount over each so the page needs no axis."""
    drawing = Drawing(doc_pdf.CONTENT_W, CHART_H)
    peak = max((row['amount'] for row in rows), default=ZERO)
    slot = doc_pdf.CONTENT_W / len(rows)
    base, room = 5 * mm, CHART_H - 11 * mm
    for index, row in enumerate(rows):
        x = index * slot
        height = float(row['amount'] / peak) * room if peak else 0
        if height:
            drawing.add(Rect(x + slot * 0.18, base, slot * 0.64, height, fillColor=doc_pdf.ACCENT, strokeColor=None))
            drawing.add(String(x + slot / 2, base + height + 1.2 * mm, f"{row['amount']:,.0f}", fontName='Helvetica',
                               fontSize=6.5, fillColor=doc_pdf.INK, textAnchor='middle'))
        drawing.add(String(x + slot / 2, 1.2 * mm, row['label'], fontName='Helvetica', fontSize=7,
                           fillColor=doc_pdf.MUTED, textAnchor='middle'))
    return drawing


def build_analysis_report_pdf(*, profile, range_label, store_label, generated_label, summary, purchases_total,
                              trend, payments, top_products, overview, show_profit):
    style = doc_pdf.styles()
    footer = f'Analysis - {range_label} - {store_label}'
    story = [doc_pdf.shop_header(profile, style, 'ANALYSIS', [range_label, store_label, f'Generated {generated_label}']),
             Spacer(1, 8)]

    tiles = [('Sales', eur(summary['amount']))]
    if show_profit:
        tiles.append(('Gross profit', eur(summary['profit'])))
    tiles += [('Purchases', eur(purchases_total)), ('Orders', str(summary['orders'])),
              ('Per order', eur(summary['avg']))]
    story += [doc_pdf.kpi_tiles(style, tiles), Spacer(1, 6)]

    rows = overview['monthly_rows']
    head = ['Month', 'Orders', 'Items', 'Sales'] + (['Profit'] if show_profit else [])
    table = [[row['label'], str(row['order_count']), str(row['qty']), eur(row['amount'])]
             + ([eur(row['profit'])] if show_profit else []) for row in rows]
    table.append(['Total', str(overview['year_order_count']), str(overview['year_sales_qty']),
                  eur(overview['year_sales_amount'])] + ([eur(overview['year_sales_profit'])] if show_profit else []))
    widths = [22 * mm, 22 * mm, 38 * mm] + ([38 * mm] if show_profit else [])
    widths.insert(0, doc_pdf.CONTENT_W - sum(widths))
    story.append(KeepTogether([
        Paragraph(f"Year {overview['year']}", style['section']), _year_chart(rows), Spacer(1, 4),
        doc_pdf.data_table(style, head, table, widths, right_cols=tuple(range(1, len(head))), bold_last=True)]))

    if top_products:
        head = ['Product', 'Units', 'Sales'] + (['Profit'] if show_profit else [])
        table = [[row['name'], str(row['qty']), eur(row['revenue'])] + ([eur(row['profit'])] if show_profit else [])
                 for row in top_products]
        fixed = [14 * mm, 28 * mm] + ([28 * mm] if show_profit else [])
        story.append(KeepTogether([
            Paragraph('Best sellers in the range', style['section']),
            doc_pdf.data_table(style, head, table, [doc_pdf.CONTENT_W - sum(fixed)] + fixed,
                               right_cols=tuple(range(1, len(head))))]))

    if payments:
        total = sum(row['amount'] for row in payments) or 1
        table = [[row['label'], eur(row['amount']), f"{row['amount'] / total * 100:.0f}%"] for row in payments]
        story.append(KeepTogether([
            Paragraph('Payments in the range', style['section']),
            doc_pdf.data_table(style, ['Method', 'Amount', 'Share'], table,
                               [doc_pdf.CONTENT_W - 65 * mm, 40 * mm, 25 * mm], right_cols=(1, 2))]))

    if trend:
        head = ['Day', 'Sales', 'Purchases'] + (['Profit'] if show_profit else [])
        table = [[row['label'], eur(row['sales']), eur(row['purchases'])]
                 + ([eur(row['profit'])] if show_profit else []) for row in trend]
        widths = [40 * mm, 40 * mm] + ([40 * mm] if show_profit else [])
        widths.insert(0, doc_pdf.CONTENT_W - sum(widths))
        story.append(Paragraph('Day by day', style['section']))
        story.append(doc_pdf.data_table(style, head, table, widths, right_cols=tuple(range(1, len(head)))))

    return doc_pdf.build(story, title='Analysis', author=getattr(profile, 'name', ''), footer=footer)
