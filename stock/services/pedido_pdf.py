"""Render a quote to PDF.

PDF rather than a spreadsheet because that argument is already settled: a
customer opening an .xlsx in WhatsApp saw prices turn into dates, and the PDF
export is what fixed it. Same reasoning here - a quote is read on a phone.

The look lives in doc_pdf, shared with the receipt and the sales report. This
module only decides what a quote says. It is handed finished strings and
Decimals - availability wording, the payment text, the totals.
"""
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, Spacer

from . import doc_pdf
from .doc_pdf import money, pdf_safe  # noqa: F401  (part of this module's API)


def _payment_block(style, payment_info):
    safe = pdf_safe(payment_info).strip()
    if not safe:
        return []
    flow = [Paragraph('Pagamento', style['section'])]
    # Blank lines separate the options, so each becomes its own paragraph and
    # the three methods stay apart on the page.
    for chunk in [c for c in safe.split('\n\n') if c.strip()]:
        flow.append(Paragraph(doc_pdf.esc(chunk.strip()).replace('\n', '<br/>'), style['body']))
        flow.append(Spacer(1, 3))
    return [KeepTogether(flow)]


def build_pedido_pdf(*, profile, lines, totals, date_label, valid_until_label='',
                     customer_name='', customer_phone='', customer_address='',
                     note='', payment_info=''):
    """Render the quote to PDF bytes.

    The customer block carries name, phone and address - never the NIF. Most
    customers here never gave a usable one, so the field would be a blank line
    on the paper more often than not; the phone is what reaches them.
    """
    style = doc_pdf.styles()
    right = [f'Data: {date_label}']
    if valid_until_label:
        right.append(f'Válido até: {valid_until_label}')
    story = [doc_pdf.shop_header(profile, style, 'PEDIDO', right), Spacer(1, 7)]

    customer = doc_pdf.party_block(style, [('Cliente', customer_name),
                                           ('Telefone', customer_phone),
                                           ('Endereço', customer_address)])
    if customer is not None:
        story += [customer, Spacer(1, 8)]

    rows = [[line['title'], line.get('barcode') or '', line.get('availability') or '',
             str(line['quantity']), money(line['unit_price']), money(line['line_total'])]
            for line in lines]
    story.append(doc_pdf.data_table(
        style, ['Produto', 'EAN', 'Disponibilidade', 'Qtd.', 'Preço unit.', 'Total'], rows,
        doc_pdf.fill_first(30 * mm, 30 * mm, 12 * mm, 20 * mm, 21 * mm),
        right_cols=(3, 4, 5)))
    story.append(Spacer(1, 7))

    summary = [('Subtotal', money(totals['subtotal']))]
    if totals['discount']:
        summary.append(('Desconto', money(-totals['discount'])))
    if totals['shipping']:
        summary.append(('Transporte', money(totals['shipping'])))
    for extra in totals['extras']:
        summary.append((extra['label'], money(extra['amount'])))
    story.append(doc_pdf.totals_table(style, summary, ('TOTAL', money(totals['total']))))

    if note:
        story += [Spacer(1, 8), Paragraph('Observações', style['section']),
                  Paragraph(doc_pdf.esc(note).replace('\n', '<br/>'), style['body'])]

    story += _payment_block(style, payment_info)
    return doc_pdf.build(story, title='Pedido',
                         author=getattr(profile, 'name', '') or 'Pedido')
