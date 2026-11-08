# Shopify theme updates and SEO release — 2026-11-08

The published theme changed from the customized Dawn 16.1.0 to Dawn 16.1.1 (theme 208330096988). The update retained custom carousel sections but omitted modifications in five native files: component-card.css, card-product.liquid, price.liquid, product-variant-picker.liquid and product-variant-options.liquid. This removed equal card title slots, rounded cards, sold-out image fading and parts of whole-item option/price filtering.

The recovery draft **Scentory 16.1.1 - SEO e Design 08-11** (208349528412) duplicates the current 16.1.1 theme. A three-way merge restored the custom changes and preserved all four upstream escaping changes in the variant picker. This is not a rollback to 16.1.0.

The draft also includes retail homepage SEO and a visible H1, accurate original-versus-decant copy, clear price capacity labels, author bylines, and official Instagram/map/contact links. Novidades retains its existing infinite marquee and the latest 20 available App arrivals; homepage Restock remains unchanged. Coffret, oil and mini products retain whole-item-only filtering; ordinary perfume decants remain.

Global Shopify content has been updated separately: five brand and three gender collections, five Lattafa product descriptions and metadata, four new articles, and the existing revenda guide. The News blog display name is now Guias e Inspiração; its news handle and existing article URL are retained. The footer menu now links to the blog. No collection membership, product prices, stock or discount conditions were changed by this SEO release. No restock email or Flow was activated.

## Publishing this release

Preview https://www.scentory.pt/?preview_theme_id=208349528412 and publish that draft in Shopify → Online Store → Themes. The connector cannot publish themes or edit MAIN files. The patch is a backup/review artifact; its changes are already applied to the draft. No Inventory App deployment is required for this release.

## Future theme updates

Add the updated theme to drafts first. Review Shopify's code-edits-included result and compare the files in shopify/seo-20261108/manifest.json before publishing. Preserve the current published theme as a backup. Merge individual custom changes against the new official version; do not overwrite new native files wholesale with old ones.

Check mobile and desktop product title heights, rounded corners, fully sold-out image fading, selected-capacity prices, Coffret/Oil/Mini without decants, normal perfume decants, Novidades dragging/wrapping/resume, homepage Restock, canonical/title/description/H1, and footer/contact links. Keep the reviewed custom diff in Git with its base version after each upgrade.

Shopify explains when custom code can or cannot be carried forward: https://help.shopify.com/en/manual/online-store/themes/managing-themes/updating-themes

## Verification

All 15 changed files passed the Liquid/theme validator (three warnings from the partial local theme tree or existing Dawn code, no errors). Current Admin readback matches all 18 updated collection/product/article records after normalizing harmless HTML formatting. All 17 distinct links in those content records resolve to known store resources or the verified official Lattafa sources. Mobile and desktop preview checks verified the retail H1/metadata/canonical, 20 in-stock equal-height Novidades cards, bounded infinite dragging/resume and Restock. Browser artifacts and rollback snapshots are retained locally under .tmp/geo/seo-20261108/.
