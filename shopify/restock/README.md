# Restock theme integration

- `snippets/scentory-restock-dialog.liquid` is the complete shared storefront dialog.
- `locales/*.json` are **translation fragments**. Merge the `scentory_restock` object into the matching existing locale; do not replace an entire locale with a fragment. The unpublished Shopify theme was updated by merging these keys into its current files.
- `messaging-email.liquid` is a Messaging **Custom Liquid section**, not a complete email. Keep the editor's default footer and unsubscribe link.

Deployment, the validated Flow receipt expression, remaining native verification and activation steps: `docs/SHOPIFY_MESSAGING_DEPLOY.md`.
