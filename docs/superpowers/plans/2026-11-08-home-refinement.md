# Homepage refinement implementation plan

> For agentic workers: use executing-plans for the frontend; one isolated parallel worker owns only the backend files listed below.

**Goal:** Keep Novidades at the latest 20 available App products, refine the infinite product marquee and cards, add Restock, order existing category pages by buyability, and prohibit decants for Coffret/Oil/Mini.

**Architecture:** App Product.created_at governs Novidades; require positive App stock, verified Online Store publication and a buyable permitted variant before deduplicating/limiting to 20. Background reconciliation orders complete existing collection memberships by whole-item/decant-only/sold-out tiers without changing conditions. Do not create /collections/all. One shared backend and one shared Liquid type guard prohibit decants for Coffret, Oil and Mini. The current MAIN is copied to a draft; compact equal-height cards run continuously inside page gutters with drag/resume and seamless wrapping. No large controls or product CTA. Restock remains a separate 30-day shelf.

**Tech stack:** Existing Django, Admin GraphQL 2026-10, Dawn Liquid/CSS/JS. No dependencies or migrations expected.

- [x] Backend: available App-newest 20, replacement on receipt, unpublished exclusion and failure preservation; complete collection-tier ordering with a fresh MANUAL readback.
- [x] Frontend: compact cards, shared title slots, soft sold-out style, rounded list cards, added Restock shelf and main-theme-derived homepage.
- [x] Replace old controls with a content-width continuous marquee; prove end wrap, touch drag, tap and immediate autoresume on mobile/desktop.
- [x] Diagnose Type visibility; publish only the explicitly approved Type records and user-confirmed recent App batch. Do not create other historical products.
- [x] Enforce whole-item-only Coffret/Oil/Mini stock and pricing rules; retain legitimate Mini fragrance choices and normal perfume decants.
- [x] Validate theme and GraphQL; 67 related backend tests pass; 20 existing collection orders verified. Stage/push isolated deliverable and provide direct deployment commands.

**Verification:** 390px/1440px browser checks confirm twenty buyable products, no page overflow, equal 240px product cards/36px title slots, ~328px section height, no large buttons, seamless end wrap, drag and tap autoresume, seven Restock cards and rounded collection cards. Full Liquid validation covers 12 changed files; only unused-snippet warnings arise from the partial local theme dependency tree. Main theme remains unchanged until merchant publishing. Product-page whole-item option/price check is recorded separately.
