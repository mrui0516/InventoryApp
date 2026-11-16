# STOQ notifications and incoming preorders

The merchant selected full payment at checkout and dispatch after arrival.
Preorders are restricted to **new products on confirmed, positive-quantity
pending inbound orders**, matched to the exact whole-bottle Shopify SKU.
Existing purchases or sales exclude restocks. The 5ml/10ml decants are excluded.

## Inventory and recurring checks

The existing `process_shopify_catalog` worker already refreshes
`scentory.incoming_products`. Each refresh now also checks STOQ policies:

- Enabled, full-payment STOQ membership and incoming eligibility are both
  required to retain `CONTINUE`.
- Receipt, cancellation, previous sales/purchases, and exhausted incoming
  capacity close `CONTINUE`. The guard never reopens a closed offer.
- Capacity deducts the existing two-bottle sample reserve when applicable.
- The incoming feed carries exact public product/whole-bottle variant IDs and
  capacity. Unpublished listings still receive no storefront link.
- `ShopifyCatalogState.preorder_variant_gid` remembers an observed whole-bottle
  offer before stock/policy writes, including disabled offers and `DENY`
  variants. The marker survives offer deletion, receipt, and cancellation.
- Stock synchronization subtracts the marked variant's outstanding Shopify
  `committed` quantity. It reads location `available` and `committed` in one
  snapshot and uses compare-and-swap for the available write. A concurrent
  change raises the normal Shopify error for retry/requeue instead of
  overwriting the new order.

The periodic guard is a secondary check. It does not replace STOQ's checkout
quantity limits and does not create or attach private STOQ offers.

## Theme

Preview theme: `209070162268`, **Scentory — STOQ integrado 16-11-2026**.
Keep it unpublished until the merchant explicitly approves publication.

Custom product shelves and incoming cards open STOQ's documented native modal
with exact product and variant IDs, preserving a real product-page fallback.
Only eligible zero-stock offers receive `PreOrder` structured data; real stock
remains `InStock`. Cart notices require an actual STOQ selling-plan allocation.
No customer subscriptions, emails, orders, or payments are created by setup.

## PythonAnywhere deployment

Use the existing virtual environment and worker; no additional scheduled task
or dependency is required. Migration 0094 adds only the reservation marker.

```bash
cd /home/scentory/InventoryApp
workon inventory
git pull --ff-only origin master
python manage.py migrate --noinput
python manage.py check
python manage.py sync_shopify_incoming --apply
```

Then reload the web app and restart the existing Always-on task to load the
updated code:

```bash
/home/scentory/.virtualenvs/inventory/bin/python -u /home/scentory/InventoryApp/manage.py process_shopify_catalog --apply --watch --interval 60
```

Do not start a second copy of that task. Check that the shop incoming metafield
now includes `variant_id`, `variant_sku`, and `preorder_capacity`.

## Remaining private STOQ configuration

OAuth authorization succeeded, and merchant setup created a full-payment offer
for Asad Polar. Native MCP currently fails while processing the response to
`notifications/initialized`; no private STOQ read/write tool becomes available.
The URL is `https://app.stoqapp.com/api/v2/external/mcp`.

The Asad Polar 5ml/10ml Shopify inventory policies were directly changed to
`DENY`, preserving quantities (including the existing 5ml quantity of -1).
Private offer membership still needs those two variants removed. Other incoming
whole-bottle variants must be attached through STOQ's supported private API/UI,
with finite per-variant limits based on current pending quantities, full-payment
terms, and Portuguese shipping/notification copy. These private changes are not
claimed complete. Missing `preorder_max_count` on the current public variant
metafields means no cap has been verified.

Use normal MCP/OAuth or a merchant-supplied `STOQ_API_KEY` in the local `.env`
(never in chat). Do not probe OS credential stores. Do not simulate STOQ-owned
configuration by writing its internal metafields through another app.

Official references:
- https://docs.stoqapp.com/v2/docs/api-v2/mcp/
- https://docs.stoqapp.com/frontend/docs/javascript-api/
- https://docs.stoqapp.com/frontend/docs/preorder-metafields/product-variant-level-metafields/
- https://shopify.dev/docs/api/admin-graphql/latest/mutations/inventorySetQuantities
