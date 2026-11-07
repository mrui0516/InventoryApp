# Shopify Type and brand alignment — 2026-11-07

## Source and scope

The merchant supplied scentory-db-20261107-182017.sqlite3. It was opened read-only;
SHA-256 before and after: b4173dbd228d3df4cfc9655228e62575837571e015bed6aa1d65d09e4681ac41.
No customer data, costs or credentials were extracted. Exact variant SKU = app
barcode was used to match all 249 existing Shopify products; mixed variants had
no conflicting Type or vendor among matched source rows. Two products have
archived source rows: their existing listing status was retained.

## Live changes completed through Shopify

- Updated 113 productType values, including normalization of Perfumes to Perfume.
- All 249 product types read back correctly against the source mapping.
- Created and published these automatic collections:

| App Type code | Shopify productType | Collection | Products |
|---|---|---|---:|
| perfume | Perfume | /collections/perfumes | 232 |
| mini | Mini | /collections/mini-perfumes | 1 |
| oil_perfume | Óleo de perfume | /collections/oleos-de-perfume | 2 |
| body_mist | Body mist | /collections/body-mist | 2 |
| body_spray | Body spray | /collections/body-spray | 6 |
| air_freshener | Ambientador | /collections/ambientadores | 1 |
| coffret | Coffret | /collections/coffrets | 5 |

Amostra has no corresponding Shopify product in this source, so no empty
collection was created. Existing 5ml/10ml variants are not separate Mini products.

- Main menu now uses Produtos -> Type. Perfumes contains Homem/Mulher/Unissexo;
  those gender collections also require productType Perfume. Existing collection
  URLs and the Decants, Revendedor, contact and footer links were retained.
- Created Vendor-based collections for Al Absar, Arabiyat Prestige, Asdaaf,
  Khadlaj, Mamkakat Al Oud and Nusuk; added them to Marcas.
- Added one missing Paris Corner product to its existing manual collection.
- With explicit merchant confirmation, corrected Andaleeb from Lattafa to Asdaaf
  and Hand & Body Lotion from Ard Al Zaafaran to Lattafa. Brand prefixes in titles
  and matching brand tags were reconciled; product handles were retained.

The legacy smart rule conversion produced productType conditions with ALL values
and empty membership. Replacing them with explicit singleton productType ANY
conditions produced the expected counts. Gender sources combine Type and tag
using source-level ALL. The final counts sum to 249.

## Verification and limits

- Final readback covered all 249 products and 741 variants; no price differences.
- One variant's stock changed from 15 to 13 during the operation. This task sent
  no inventory mutations and did not restore that external change; its origin
  was not established here.
- This task did not write variant prices, inventory, handles or product status,
  and did not publish or modify theme files.
- Admin GraphQL artifacts passed Shopify's 2026-10 CLI validator, revision 2.
  Different relation enum fields in query fragments use aliases.
- Source snapshots, affected records, operation results and the complete price
  audit are retained under .tmp/geo/taxonomy-20261107 in the primary workspace.

## App correction to deploy

New product creation now reads current ProductType.customer_label, falling back
to name/code. Root Category still controls publishing scope. This prevents new
body products, minis, oils and coffrets from all being created as Perfume.
This is a creation fix, not a new automatic updater for subsequent edits to
existing Shopify products. Prices, variants, inventory and standard Shopify
taxonomy logic are outside this small code change.

Validation: six new Type tests passed, and the existing 24 creation/scope tests
passed. Tests cover shop-defined types, blank labels, a non-perfume Category and
a label changed in another worker. Read-only code review found no Critical or
Important issues; its test-coverage suggestions were incorporated.

Deploy the isolated codex/shopify-taxonomy-20261107 branch using the normal
PythonAnywhere fetch, fast-forward merge, migrate/check and Web Reload steps.
No new migrations are introduced by this fix. If fast-forward is refused,
inspect the server branch; do not reset or overwrite another AI's changes.

References: [Shopify productUpdate](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/productUpdate),
[collection source product types](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/CollectionSourceInclusionConditionProductTypeInput).
