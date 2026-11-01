# Welcome campaign and decant pricing

User scope: pause the unfinished restock-email work; replace general EUR5 automatic discount with registered/subscribed first-order EUR5; reprice 10ml at 10% of the same product's full-bottle price, 5ml at 5% plus EUR0.50; add a Portuguese Decant guide.

Decisions: retain the existing EUR25 merchandise minimum. Use a native customer segment `number_of_orders = 0 AND (email_subscription_status = 'SUBSCRIBED' OR customer_account_status = 'ENABLED')` and update the existing automatic discount. No welcome-email automation is needed. Money rounds half-up to EUR0.01. Keep all full-bottle prices and inventory unchanged. Theme changes target UNPUBLISHED 207200584028 only; merchant publishes. Existing restock code/config is retained; its unfinished UI is hidden in the draft. User confirmed WELCOME10 deactivation; readback is EXPIRED.

- [x] Audit and back up catalogue, old offer, menus and theme files under .tmp/geo/campaign-20261101.
- [x] Create eligibility segment and update native automatic EUR5 rule, retaining EUR25 minimum.
- [x] Apply 420 decant price updates for 210 products with per-response checks.
- [x] Update App creation and ongoing price-sync policy with focused tests.
- [x] Publish the Decant guide and welcome terms page; add navigation entries.
- [x] Update draft welcome banner, signup/account messaging and hide paused restock UI; validate Liquid.
- [x] Read back prices, rules, pages and theme; commit and push the isolated branch with direct deployment commands.

No root working-tree files are edited. The branch starts from the existing restock branch and incorporates committed origin/master so the prior feature is preserved.

Validation: 26 targeted tests and Django check passed; read-only independent review found no Critical/Important issues. Native API readback verified all 420 price values, unchanged full-bottle prices, the ACTIVE segmented EUR5 offer, EXPIRED WELCOME10, two published pages and all 10 draft file changes. Public HTML fetch returned 429, so browser visual verification remains limited; no order or marketing email was sent.
