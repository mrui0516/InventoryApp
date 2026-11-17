# STOQ notifications and incoming preorders

The merchant selected full payment at checkout and dispatch after arrival.
Preorders are restricted to **new products on confirmed, positive-quantity
pending inbound orders**, matched to the exact whole-bottle Shopify SKU.
Existing purchases or sales exclude restocks. The 5ml/10ml decants are excluded.

## Inventory and recurring checks

The existing catalogue/incoming task refreshes `scentory.incoming_products`.
The merchant confirmed that the current schedule runs once an hour; keep that
schedule. Each refresh also checks STOQ policies:

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
Incoming buttons read the exact current Shopify variant, rather than waiting
for the hourly feed's cached inventory policy. The feed remains authoritative
for incoming eligibility. The lookup uses at most 20 handles, matching the
incoming feed's existing limit; the rest of this theme makes no `all_products`
lookups.
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

Then reload the web app. If the existing schedule is a long-running worker,
restart it to load the updated code. Keep the current hourly schedule; an
equivalent watch command is:

```bash
/home/scentory/.virtualenvs/inventory/bin/python -u /home/scentory/InventoryApp/manage.py process_shopify_catalog --apply --watch --interval 3600
```

Do not start a second copy of that task. Check that the shop incoming metafield
now includes `variant_id`, `variant_sku`, and `preorder_capacity`.

## Saved STOQ configuration and MCP connection

The current custom-selection offer is `fcc8a4c1-e058-49eb-9047-e03751e7f4f6`,
named **Pré-encomenda**. It is live, offers full payment only (100% at checkout),
and uses delivery timing `unknown` (On hold until arrival; no invented ship
date). Existing orders were not rescheduled. Payment/disclaimer/shipping copy
and the back-in-stock popup are Portuguese. Email remains enabled, SMS remains
disabled, and alerts require stock of the exact subscribed variant.

The Asad Polar 5ml/10ml variants were removed from the offer and remain `DENY`,
preserving quantities, including the existing 5ml quantity of -1. The 11 public
incoming whole-bottle variants were added through normal STOQ preflight and
given individual limits from the live App feed. No blanket default limit was
applied. The unpublished Black Exposed listing remains outside the offer.

| Product | Current batch limit |
| --- | ---: |
| Riiffs Taif | 4 |
| Armaf Club de Nuit Sillage | 2 |
| Armaf Club de Nuit Intense Overdose | 6 |
| Lattafa Asad Polar | 22 |
| Maison Asrar Milky Way | 1 |
| Maison Asrar Legacy | 2 |
| Rayhaan Crimson | 4 |
| Rayhaan Aquatica | 6 |
| French Avenue Spectre | 4 |
| Maison Asrar Muharib | 1 |
| Volaré Esta Pura Body Mist | 12 |

The limits total 64 units, after the App's existing sample reserve. These are
this batch's settings, not permanent product defaults. Future incoming products
appear in the feed automatically; they need an explicit STOQ offer assignment
and limit before preorder opens. The App guard only retains/closes eligibility;
it deliberately never reopens an exhausted offer.

The original native HTTP MCP transport failed on STOQ's HTTP 200 / JSON `null`
response to `notifications/initialized`. The official-documentation-recommended
`mcp-remote@0.16.6` OAuth bridge works. The existing global Codex `stoq` connector
now uses that bridge and the same URL:
`https://app.stoqapp.com/api/v2/external/mcp`. Private reads, writes and a fresh
readback succeeded without an API key. The bridge handles its own normal OAuth
storage; never probe the OS credential store or commit credentials.

The current Windows workspace keeps the client under
`.tmp/stoq-mcp-bridge/node_modules/mcp-remote/dist/proxy.js`; its dedicated OAuth
directory has its own ignore file. Preserve these while using this workspace's
connector. The local `outputs/stoq-integration-2026-11-16/stoq_mcp.py` uses Codex's
native tool-call protocol (no model turn) for this connection. Never write STOQ's
internal metafields through another app to simulate its settings.

Official references:
- https://docs.stoqapp.com/v2/docs/api-v2/mcp/
- https://docs.stoqapp.com/frontend/docs/javascript-api/
- https://docs.stoqapp.com/frontend/docs/preorder-metafields/product-variant-level-metafields/
- https://shopify.dev/docs/api/admin-graphql/latest/mutations/inventorySetQuantities
