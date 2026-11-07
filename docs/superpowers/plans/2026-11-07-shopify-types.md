# Shopify Type alignment implementation plan

> For agentic workers: use superpowers:executing-plans for this small inline fix.

**Goal:** Make new Shopify products use the inventory app's current ProductType labels.

**Architecture:** ProductType.customer_label is the customer-facing type, with name and code fallbacks. Category continues to decide publishing scope. Existing products and collections are aligned through the connected Shopify plugin using the merchant's read-only 2026-11-07 backup, matched by exact SKU/barcode.

**Tech stack:** Existing Django models and Shopify productSet client; no new dependencies or migrations.

**Spec:** User request dated 2026-11-07: Shopify categories must follow inventory app Type; create missing brand collections. For the two brand disagreements, the merchant confirmed the app wins.

## Constraints

- Isolated branch based on 746bda6; do not touch the dirty main worktree.
- Do not change live prices, inventory, handles, or product publication status.
- Do not infer Type from titles or volume; support shop-defined ProductType rows.
- Existing source backup must remain byte-identical.
- Live brand corrections may replace the brand prefix in a title and its brand tag after merchant confirmation; retain handles.

## Steps

- [x] Read current inventory backup, audit all 249 Shopify products, and back up affected records.
- [x] Create six missing vendor-based brand collections and add menu links; fix Paris Corner membership.
- [x] Correct product types from exact barcode matches, create seven Type collections, and update navigation.
- [x] Add regression tests covering body products, gift sets, mini/oil and a custom shop-defined Type with fallback labels.
- [x] Observe those tests failing against the category-based creation code (nine assertions, no setup errors in the corrected RED run).
- [x] Replace the hard-coded creation productType with a focused ProductType label helper.
- [x] Run focused Shopify creation tests and check the diff (24 existing and six new tests passed; review found no Critical/Important issues).
- [x] Verify live Type membership, brand changes and navigation; audit variants and save result metadata. No price changed; one external stock change was retained without any inventory write by this task.

Final evidence and deployment notes: docs/SHOPIFY_TYPE_ALIGNMENT.md. All live product Type values match the source mapping; seven Type collections contain 249 products in total. GraphQL validation passed on revision 2.

## Review focus

- Dynamic Type renames must not use the cached type_info row.
- Empty customer_label must fall back to the type name, then code.
- Non-perfume categories and existing perfume creation must keep working.
- Mixed Shopify variants share a product-level Type: never infer one per capacity.
- The live API's legacy ruleSet TYPE migration produced ALL matching with zero membership; explicit productType conditions with ANY values were verified instead.

## Operational evidence

Live snapshots and results are in .tmp/geo/taxonomy-20261107 in the primary workspace. The new backup SHA-256 is b4173dbd228d3df4cfc9655228e62575837571e015bed6aa1d65d09e4681ac41. Two already-listed products have archived source rows; their Type was read from those rows, without changing their publication status.
