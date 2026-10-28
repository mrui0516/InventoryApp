# Restock Messaging implementation plan

Goal: replace SMTP for new, explicitly consented restock subscriptions with the verified Shopify Messaging sender, without changing master or existing subscriptions.

Architecture: the inventory App owns variant demand and the outbox. A per-email database slot serializes dispatches. Each dispatch adds one product-ready tag and one unique job tag. Flow sends the email, then removes the newly added job tag as a completion receipt; the App polls that receipt. No Flow HTTP action, customer bulk query, or new paid service is required.

Decisions: native marketing email requires an unchecked, explicit marketing-consent checkbox. Only a foreground customer request may record SINGLE_OPT_IN; workers never re-subscribe customers. Existing SMTP subscriptions are not converted. A Flow receipt means its send action completed, not inbox delivery. Ambiguous dispatch failures block for review rather than automatically sending again.

## Tasks

- [x] Add failing integration tests for explicit consent, exact capacity, same-email serialization, receipt acknowledgement, opt-out, cancellation and ambiguous mutation outcomes.
- [x] Add provider settings, subscription consent metadata, provider-specific jobs, per-email dispatch slots and migration.
- [x] Implement schema-validated customer lookup/consent and tag bridge; route the existing worker to the selected provider and poll receipts fairly.
- [x] Add a read-only deployment preflight command, manager queue visibility and consent-aware theme dialog. Keep Messaging disabled until the merchant configures Flow.
- [x] Document direct Git/PythonAnywhere commands and the exact Flow filters, send and receipt steps. Validate Liquid and all new GraphQL.
- [x] Run focused tests, Django checks and migration check; review concurrency and failure states; commit and push only the isolated branch.

## Review focus

- Submitting without consent must not create or subscribe a Shopify customer.
- A second capacity/product for the same email cannot replace the first email's template data.
- A missing receipt must not be labelled sent or automatically retriggered.
- A timeout after tag submission cannot lead to a second automatic email.
- A provider change cannot silently enrol pre-existing SMTP requests in marketing.

## Ledger

- Base 52687b2 pushed to origin/codex/restock-app-20261026; master untouched.
- Shopify 2026-10 schema inspected for CustomerInput, consent mutation, Customer.defaultEmailAddress and tag mutations. Official Flow docs confirm Remove customer tags; Add customer tags is intentionally avoided in this workflow.
- 36 restock tests passed; focused stock push checks also passed. GraphQL toolkit validation passed for all seven bridge operations against 2026-10. Liquid validation passed for dialog, translations and production email; dialog JavaScript syntax checked separately.
- Independent read-only review found stale pre-slot customer snapshots and opt-out releasing unresolved context. Fixed and covered with regressions. Receipt uses only the immutable original event's job UUID; delayed old receipts cannot complete later jobs. No remaining concrete Important issue reported in those paths.
- Merchant confirmed production Flow prefix/single-token removal is validated and remains off. Messaging email editor save and native two-product receipt/render trial remain external activation requirements; no production sending or server deployment performed by the agent.
- Consent dialog and both locale updates were applied only to UNPUBLISHED theme 207200584028 and read back successfully.
- Full Django suite: 877 tests passed in 689.796 seconds; no test failures. Expected outage-test logging and missing unbuilt staticfiles warning do not indicate a failed test. Migration drift check and Django system check passed.
- During merchant draft testing, diagnosed missing DNS for gestao.scentory.pt (Google public resolver NXDOMAIN) and HTTP 404 for the not-yet-deployed restock route at the actual scentory.eu.pythonanywhere.com host. Corrected draft endpoint, public link default and production email cancellation link to the actual host. After the URL-only correction, all 36 restock tests passed again; four changed theme files passed Liquid checks. The customer-facing message now distinguishes unavailable alert service from failed subscription submission.
