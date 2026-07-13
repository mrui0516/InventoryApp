# Product

## Register

product

## Users

**KHAN PERFUME** — a small perfume retail business in Amadora, Portugal (now multi-store, e.g. a second store "Scentory"). Three roles, all authenticated staff working the tool daily:

- **Employees (cashiers/floor staff)** — run the counter: POS checkout, receiving stock, clocking attendance, looking up products and customers. Often on their feet, sometimes on a phone/tablet at the counter, moving fast between customers. Cost/profit data is hidden from them.
- **Managers** — the above, plus the money view: cost/FIFO/profit, product CRUD, supplier and AR management, team attendance summaries, stock adjustments. They live in the dashboard and sales records.
- **Admins (owner/superuser)** — everything, plus employee accounts, the order-correction/audit center, store management, and force-deletes.

Context of use: a live shop. The primary job on any given screen is a transactional one done under mild time pressure (ring up a sale, receive an order, check a balance, read the day's numbers) — not exploration. Employees are locked to their home store; managers/admins switch stores or view "All stores."

## Product Purpose

An all-in-one **inventory + POS + CRM** system for a small perfume retailer. It exists to replace spreadsheets and guesswork with one trustworthy source of truth for:

- Stock (FIFO batch costing), inbound receiving, and supplier records — shared across all stores.
- Point-of-sale checkout with per-line, splittable payment methods.
- Customers, accounts-receivable (credit) tracking, and history.
- Operating dashboards: sales, gross profit, MoM comparison, targets, slow movers, low-stock alerts, yearly trend — all store-scoped.
- Staff accounts, attendance, order-correction auditing, receipt printing, and exports.

Success = staff trust the numbers and finish the transactional task fast; managers can read the state of the business at a glance and never see a figure they have to second-guess.

## Brand Personality

**Sharp · precise · data-confident.** The interface should carry itself like a well-built financial/analytics instrument: numbers stated plainly and confidently, information dense but never cramped, built for people who use it every day and want fewer clicks, not hand-holding. Voice is professional and terse — labels and data, not explanations. It respects the user's expertise and the seriousness of handling money and stock.

Emotional goal: **quiet confidence.** The staff should feel the tool is exact and dependable; a manager reading the dashboard should feel informed, not sold to.

## Anti-references

- **Trendy consumer SaaS.** No oversized hero-metric templates, no gradient-drenched cards, no marketing-y motion or decorative animation. This is a working tool, not a landing page. Motion is functional (state feedback, transitions) only.
- **Toy-like / childish.** No playful rounded blobs, cartoonish bright colors, or emoji-as-icons. It handles money — it must read as serious and trustworthy.
- (Implicit, from the build so far) not a generic flat Bootstrap admin either: the density and typographic discipline are the identity.

## Design Principles

1. **Numbers you can trust at a glance.** Money and quantities are the product. Use tabular figures, consistent precision, and unambiguous alignment so a total never has to be re-read. Never surface a figure a role isn't allowed to see — gating is a correctness requirement, not a nicety.
2. **Speed over decoration.** The common paths (checkout, receive, look up, read the day) are repeated hundreds of times. Optimize the fastest route to task-done; every bit of chrome or motion must earn its place against that.
3. **Dense but legible.** Pack information for expert power-users without tipping into clutter — hierarchy through spacing, weight, and alignment, not color or boxes-in-boxes.
4. **The interface adapts to who's looking.** Employee / manager / admin and the active store change what's shown. Sensitive data (cost, profit) is absent from the DOM for unauthorized roles, not merely visually hidden.
5. **Restraint is the brand.** A working financial tool earns trust by being calm and consistent, not flashy. When in doubt, remove the flourish.

## Accessibility & Inclusion

- Target **WCAG AA**: body text ≥ 4.5:1 contrast (large text ≥ 3:1), visible focus states, keyboard-navigable flows, semantic labels on inputs and icon-only buttons.
- **Readable defaults**: 16px+ body text (avoids mobile input zoom), comfortable line-height, tabular numerals for all data columns.
- **Touch-aware**: staff use phones/tablets at the counter — keep tap targets ≥ 44px and primary POS actions reachable and un-cramped on small screens.
- Respect `prefers-reduced-motion` (already honored in the design system); never convey status by color alone (pair with icon/text — matters for payment-method and stock-availability states).
- Locale is **Europe/Lisbon**, euro currency, Portuguese tax (NIF) conventions.
