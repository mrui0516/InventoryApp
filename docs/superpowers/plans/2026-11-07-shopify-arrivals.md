# Shopify arrivals automation implementation plan

> For agentic workers: use superpowers:executing-plans inline; one focused final code review.

**Goal:** Publish complete new app products and refresh Novidades automatically; maintain a Restock collection for genuine returns to stock in the last 30 days.

**Architecture:** A durable per-product generation queue and one database lease serialize background API work. Product/image saves and stock changes enqueue locally; real receipt batches record zero-to-positive restocks within the receipt transaction. A watcher retries failures and periodically reconciles collections even without user requests.

**Tech stack:** Existing Django/SQLite, Shopify Admin 2026-10, existing one-way price/inventory helpers. No new dependencies. Feature off until explicitly enabled in deployment.

**Spec:** User request 2026-11-07 and confirmation of a 30-day Restock window. Novidades retains the newest 20 listed app products, choosing newest first then showing available products first within that set. Restock excludes initial receipts, transfers, refunds, manual corrections, expired events and current zero stock.

## Constraints and design decisions

- Isolated branch includes the prior Type fix; preserve all uncommitted work in the main checkout.
- Category.sync_to_shopify controls scope; do not auto-publish incomplete products or merchant-owned drafts.
- New listings require barcode, positive price and a usable photo; use current Type logic.
- Automatic creates use a persisted deterministic handle and productSet identifier for retry idempotency. Existing handles are unchanged.
- Never hold an app stock transaction open during network work. Queue and receipt timestamps roll back with the transaction.
- Shopify stock remains one-way from the app. This feature does not resume the paused restock email workflow.
- Keep sold-out products visible rather than running the old command that makes them DRAFT.
- Use physical on-hand consistently with the existing Shopify inventory helper; do not count on-the-way purchases.
- Restock page is the Shopify manual collection /collections/restock, published and linked in navigation.
- A source-backed historical backfill is allowed only for reconciled, nonnegative ledgers without manual adjustments; otherwise start tracking from deployment.

## Tasks

- [ ] Write failing tests for receipt transitions, queue rollback/revisions, retry/idempotency and collection selection.
- [ ] Add queue/lease models and one migration; implement enqueue and receipt hooks.
- [ ] Implement background runner, readiness checks, deterministic creation and collection refresh.
- [ ] Replace the old storefront command; add watcher/bootstrap command and settings.
- [ ] Configure the live Restock collection/menu and refresh existing Novidades from the supplied backup.
- [ ] Verify focused tests, migration consistency and GraphQL; one code review; commit/push isolated branch.
- [ ] Provide exact PythonAnywhere deployment flags, bootstrap command and worker command.

## Review focus

Concurrent workers and ambiguous creation retries must not create duplicates. A newer queued generation must survive an older worker's acknowledgment/failure. Initial split receipts and shop transfers must not create Restock entries. Missing images/prices must retain a retryable job. Manual drafts, retail monthly rankings and source publishing scope must remain intact.
