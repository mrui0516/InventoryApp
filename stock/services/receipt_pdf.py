"""A sale's A4 receipt as a PDF, in the same look as the quote.

Portuguese, because it is handed to the customer - like the quote and the
customer price list. The 48 mm till slip is a different thing and stays HTML.
"""
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Spacer

from . import doc_pdf
from .doc_pdf import money

PT_PAYMENT = {'cash': 'Numerário', 'card': 'Cartão', 'mbway': 'MB WAY'}


def valid_pt_nif(nif):
    """True for a Portuguese NIF whose check digit adds up.

    Many customers were saved with a stand-in (000000001, 123123123) because
    the till wants nine digits. Printing those on a receipt helps nobody; a
    real one is printed because the customer can use it for tax deductions.
    """
    digits = (nif or '').strip()
    if len(digits) != 9 or not digits.isdigit():
        return False
    total = sum(int(d) * (9 - i) for i, d in enumerate(digits[:8]))
    check = 11 - (total % 11)
    if check >= 10:
        check = 0
    return check == int(digits[8])


def build_receipt_pdf(*, profile, order_id, created_label, store_name, customer_name,
                      customer_phone, customer_nif, lines, total, payments, footer_note=''):
    """``lines``: dicts with title, barcode, quantity, unit_price, line_total.
    ``payments``: (method code, amount) pairs from the order's payment split."""
    style = doc_pdf.styles()
    right = [f'N.º {order_id}', f'Data: {created_label}']
    if store_name:
        right.append(f'Loja: {store_name}')
    story = [doc_pdf.shop_header(profile, style, 'RECIBO', right), Spacer(1, 7)]

    customer = doc_pdf.party_block(style, [
        ('Cliente', customer_name),
        ('Telefone', customer_phone),
        ('NIF', customer_nif if valid_pt_nif(customer_nif) else ''),
    ])
    if customer is not None:
        story += [customer, Spacer(1, 8)]

    rows = [[line['title'], line.get('barcode') or '', str(line['quantity']),
             money(line['unit_price']), money(line['line_total'])] for line in lines]
    story.append(doc_pdf.data_table(
        style, ['Produto', 'EAN', 'Qtd.', 'Preço unit.', 'Total'], rows,
        doc_pdf.fill_first(30 * mm, 14 * mm, 22 * mm, 24 * mm), right_cols=(2, 3, 4)))
    story.append(Spacer(1, 7))

    summary = [('Artigos', str(sum(int(line['quantity']) for line in lines)))]
    for method, amount in payments:
        summary.append((f"Pago em {PT_PAYMENT.get(method, (method or 'outro').title())}"
                        if method != 'mbway' else 'Pago por MB WAY', money(amount)))
    story.append(doc_pdf.totals_table(style, summary, ('TOTAL', money(total))))

    if footer_note:
        story += [Spacer(1, 14), Paragraph(doc_pdf.esc(footer_note), style['note'])]
    return doc_pdf.build(story, title=f'Recibo {order_id}',
                         author=getattr(profile, 'name', '') or 'Recibo',
                         footer=f'Recibo N.º {order_id}')
