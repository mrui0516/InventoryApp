# Shopify image sync

Push local product photos to the matching products in the Shopify store
(**Scentory**, `www.scentory.pt`). Products are matched by **barcode = Shopify
variant SKU**.

**哪些产品会上 Shopify，由分类开关决定**：`Category.sync_to_shopify`（admin 里勾选）。
香水在线上卖，手机配件/电子是**纯门店商品**，迁移 0043 已把非香水分类关掉。
这个开关同时管住：四个覆盖全部产品的 sync 命令、产品页的单品同步按钮、
以及**上传照片时的自动同步 signal**（否则给配件拍张照就把它挂到网店上了）。

## 新建产品的格式（app → Shopify）

产品页的「Sync to Shopify」创建新产品时，必须和店里**已有的产品长得一样**，
否则集合筛选不到它、也没法事后补分装。格式取自线上现有产品：

| 项 | 格式 | 例子 |
|---|---|---|
| 标题 | 品牌 + 系列 + 名称 + 浓度 + 容量 | `Lattafa Khamrah Waha EDP 100ml` |
| 选项 | **`Tamanho`**（不是 Default Title） | 100ml / 10ml / 5ml |
| SKU | `<条码>` / `<条码>-10ML` / `<条码>-5ML` | |
| 标签 | 容量、系列、品牌、性别短语、`Perfume Árabe`、`Scentory`、性别、名称 | |
| SEO 标题 | 标题 + ` \| Perfume Árabe` | |
| SEO 描述 | 描述开头 + ` \| Scentory — envio para Portugal` | |

**只有 100ml 香水建 3 个变体**（`DECANTABLE_VOLUME_ML`）。
30ml 本身就便宜，再抽 2 瓶做样品会把库存吃光。

**库存按样品规则**（和更新路径同一个 `_inventory_targets`）：
100ml = 库存 − 2（留 2 瓶做样品/分装源），10ml/5ml = 只要还有 1 瓶就是 10。

**分装价格**：10ml = 正装 × 17.5%，5ml = × 11.5%，四舍五入到 €0.05
（`DECANT_PRICE_RATIO`）。这个比例是**照着线上现有产品反推的**，误差 ≤ €0.30；
创建后在 Shopify 改了就是改了，**app 只写正装价格，不会覆盖分装价**。
**成本只写在正装上**——分装是一瓶的一部分，套用整瓶成本会让每笔分装看起来都在亏。

### 之前为什么不对

创建路径只建**一个 `Default Title` 变体**、用**全部库存**、
标题只读旧的 `spec` 自由文本字段。所以新产品：没有 5ml/10ml、
库存没减 2、容量丢失（容量早就搬到 `volume_ml` 了，113 个香水的 `spec` 是空的）、
系列和名称相同时会写成 `Rayhaan Pharaoh Pharaoh`。
分装和留样规则一直只存在于**更新**路径里。

## 库存自动同步（app → Shopify，单向）

**开关**：`.env` 里 `SHOPIFY_INVENTORY_SYNC=1`。关着的时候什么都不推。

打开后，**app 里任何一次库存变动都会自动推到 Shopify**，不需要手动调：

| 动作 | 触发点 |
|---|---|
| 卖出（收银台） | `Sale` post_save |
| 进货收货 | `Purchase` post_save |
| 删除销售/采购记录 | 对应的 post_delete |
| **手动调整库存（减少）** | `consume_stock_fifo` |
| 订单更正、退回库存 | `restore_stock_fifo` |
| 改售价 | `Product` post_save（只推价格） |

**为什么减少库存要单独挂**：`consume_stock_fifo` / `restore_stock_fifo` 用
`QuerySet.update()` 改 `Purchase.remaining`（SQLite 上 `select_for_update` 是空操作，
只能用条件 UPDATE），而 **`.update()` 不触发任何 model signal**。
所以「手动减少库存」以前**永远不会推到 Shopify**，线上数量会一直停在旧值。
现在挂在这两个函数里——变动真正发生的那一个地方。

**收银台不等 Shopify**：`transaction.on_commit` 的回调是**在请求线程里同步跑的**，
直接在 signal 里推，一张 5 行的单子就要等 15 个 Shopify API 调用才返回。
现在改成：事务里先收集产品（**按产品去重**，同一产品 3 行只推 1 次），
提交后交给**后台线程**。`SHOPIFY_PUSH_BACKGROUND=0` 可以改回内联（更慢但确定）。

**推送是绝对值**，重复推没有副作用；worker 被回收导致某次推送丢失时，
`sync_shopify_inventory --apply` 可以把全部产品对齐一次（**建议每晚跑一次兜底**）。

**只推不拉**：Shopify 上卖出的单子由店里**手动录入 app**，
app 不会从 Shopify 读库存。双向同步必须决定「两边都改了听谁的」，
判断错了就是悄悄丢库存——所以刻意不做。

不上网店的分类（`Category.sync_to_shopify=False`，比如配件）和没有条码的产品**不推**。

Entry points, all driven by the same service:

- **`python manage.py sync_shopify_images`** — attach photos to products that
  already exist in Shopify (bulk backfill).
- **`python manage.py sync_shopify_products`** — **create** missing products in
  Shopify (variant / price / SKU / barcode / cost / inventory / SEO / image),
  and attach images to ones that already exist.
- **Auto-sync signal** — when a product photo is uploaded in the app, it is
  synced to Shopify automatically (opt-in via `SHOPIFY_AUTO_SYNC`; with
  `SHOPIFY_AUTO_CREATE` it also *creates* the product if it isn't there yet).

Only the authenticated GraphQL calls use the Admin token; image bytes go to
Shopify's own pre-signed upload URL. No third-party image host (Cloudinary etc.).

---

## 1. One-time setup — create a custom app token

1. Shopify admin → **Settings → Apps and sales channels → Develop apps → Create an app**.
2. Name it e.g. `Inventory Image Sync`.
3. **Configuration → Admin API integration → Configure**, grant scopes:
   - `read_products`, `write_products`
   - `read_inventory`, `write_inventory`  (needed to create products with stock)
   - `read_locations`
4. **Install app**, then **API credentials → Admin API access token → Reveal** and copy it (`shpat_…`). You only see it once.

## 2. Configure the environment (never commit the token)

Set these before running the app / command:

| Variable | Value | Required |
|---|---|---|
| `SHOPIFY_STORE_DOMAIN` | `66tcd5-su.myshopify.com` | default already set |
| `SHOPIFY_ADMIN_TOKEN` | `shpat_…` (from step 1) | **yes** |
| `SHOPIFY_API_VERSION` | e.g. `2025-01` | optional (default `2025-01`) |
| `SHOPIFY_AUTO_SYNC` | `1` to auto-sync on upload | optional (default off) |
| `SHOPIFY_AUTO_CREATE` | `1` to also *create* missing products on upload | optional (default off) |
| `SHOPIFY_NEW_PRODUCT_STATUS` | `DRAFT` or `ACTIVE` for created products | optional (default `DRAFT`) |

PowerShell (current session):
```powershell
$env:SHOPIFY_ADMIN_TOKEN = "shpat_xxxxxxxxxxxxxxxx"
```

## 3. Bulk backfill

Always preview first (dry run — writes nothing):
```powershell
python manage.py sync_shopify_images --brand Lattafa
```
Then apply:
```powershell
python manage.py sync_shopify_images --brand Lattafa --apply
```

Options:

| Flag | Effect |
|---|---|
| `--apply` | Actually upload (default is a dry run) |
| `--overwrite` | Replace images even if the Shopify product already has one |
| `--brand <text>` | Only products whose brand contains `<text>` |
| `--barcode <code>` | Only the one product with this exact barcode |
| `--in-stock` | Only products with stock > 0 |
| `--limit <n>` | Process at most `n` products |

The command prints a per-product line for uploads/errors and a summary. Result
categories: `uploaded`, `already has image` (skipped), `not in Shopify`
(skipped), `no local image`, `no barcode`, `ERROR`.

## 4. Create missing products

`sync_shopify_products` creates products that aren't in Shopify yet (and images
existing ones). **Always test one first**, then a brand, then all:

```powershell
python manage.py sync_shopify_products --barcode 6290362349730          # preview one
python manage.py sync_shopify_products --barcode 6290362349730 --apply  # create one
python manage.py sync_shopify_products --brand Lattafa --apply          # then the brand
```

Each created product gets: clean title (from brand/model/name/size), vendor =
brand, product type = category, tags, description, **SEO title + meta
description**, a single variant with **price / SKU / barcode / unit cost /
tracked inventory** set to current stock at your location, and the **product
photo**. New products are created as **DRAFT** by default (review before
publishing) — pass `--status active` to publish immediately.

Extra flags on top of the shared ones: `--status active|draft`,
`--overwrite-image`.

## 5. Auto-sync on upload

Set `SHOPIFY_AUTO_SYNC=1`. Then whenever a product photo is saved in the app, the
product is synced to Shopify **after the DB commit**, in the background of the
request (failures logged, never block the save):

- product already in Shopify → its image is attached;
- product not in Shopify **and** `SHOPIFY_AUTO_CREATE=1` → it is created
  (as `SHOPIFY_NEW_PRODUCT_STATUS`, default DRAFT) with variant/inventory/SEO/image;
- otherwise → logged and skipped.

---

## Re-align barcodes after fixing EANs (`sync_shopify_barcodes`)

If you corrected barcodes **in the app**, Shopify's SKU/barcode no longer match
(the match key changed). This command re-aligns them by matching each product to
its Shopify product by **title** (the stable key — titles didn't change) and
pushing the app's current barcode into the Shopify variant's SKU + barcode.

```
python manage.py sync_shopify_barcodes                  # preview every change (dry run)
python manage.py sync_shopify_barcodes --brand Lattafa  # scope by brand
python manage.py sync_shopify_barcodes --apply          # write to Shopify
```

All Shopify products are fetched once and matched locally, so the dry run is a
few API calls; only real changes write. **Always dry-run and review first.**
Titles that occur on more than one Shopify product are treated as ambiguous and
skipped. Run the barcode re-align **before** the image/product sync so those keep
matching by SKU. After this, re-run `sync_cloudinary_images --apply` so Cloudinary
assets live under the corrected barcodes too.

## Price + inventory (`sync_shopify_inventory`) + real-time push

Push the app's **price** and **on-hand** to Shopify — the app is the source of
truth — matched by barcode = SKU:

```
python manage.py sync_shopify_inventory                    # preview (dry run)
python manage.py sync_shopify_inventory --apply            # write price + inventory
python manage.py sync_shopify_inventory --inventory-only --apply
python manage.py sync_shopify_inventory --price-only --brand Lattafa --apply
```

All Shopify variants are fetched once; only real changes write. Price sets the
100ml variant to `Product.default_price`.

**Decant-aware inventory.** Shopify variant SKUs are `<barcode>` (100ml),
`<barcode>-10ML`, `<barcode>-5ML`. Given `N = Σ purchase.remaining` full bottles:

| Variant | Available set to |
|---|---|
| 100ml | `max(N − 2, 0)` — the last 2 bottles are shop samples, so ≤2 → out of stock |
| 10ml / 5ml | `10` while any stock exists (N ≥ 1 — a sample can be decanted), else `0` |

Products without decant variants just get 100ml = `N`. The reserve (2) and decant
availability (10) are `DECANT_RESERVE` / `DECANT_AVAILABLE` in `shopify_sync.py`.

**Real-time:** set `SHOPIFY_INVENTORY_SYNC=1` in the environment. Then, after the
DB commit:

- a **sale / purchase / stock adjustment** pushes that product's **on-hand** to Shopify;
- changing a product's **price** in the app pushes the new **price** to Shopify.

Idempotent (absolute set), never breaks the local save, ~1–2 Shopify API calls per
event, off by default. Run the bulk `sync_shopify_inventory --apply` once first to
align everything, then turn the flag on so it stays in sync.

## Daily storefront automation (`sync_shopify_storefront`)

Meant for a daily PythonAnywhere **Scheduled task**. Dry-run by default.

```
python manage.py sync_shopify_storefront            # preview
python manage.py sync_shopify_storefront --apply    # write to Shopify
```

1. **Hide sold-out / show restocked** — a perfume with on-hand `N == 0` is set to
   `DRAFT` (off the storefront); `N >= 1` is set back to `ACTIVE`. (At `N <= 2` the
   100ml is out of stock but decants remain, so the product stays visible.)
2. **"Novidades"** — the existing manual collection is set to the **20 newest
   perfumes** (by `created_at`, newest first).
3. **"O Mais Vendido do Mês"** — created if missing, set to the **top 5 perfumes by
   units sold this calendar month** (from app sales).

Collections are matched by exact title; you wire the theme's homepage sections to
them in Shopify (theme edits are manual). *Back-in-stock (a collection of perfumes
whose 100ml recently returned to stock) is a separate follow-up — it needs to
track the out→in transition.*

## Bulk button (product list)

The product list has a manager-only **"Sync all perfumes to Shopify"** button. It
launches `manage.py sync_shopify_perfumes --apply --create` in a detached
background process (200+ products would time out a web request), writing progress
to `logs/shopify_perfumes_sync.log`. It updates descriptions (formatted) and
pushes price + decant-aware inventory for every perfume, and **creates the ones
that are not on Shopify yet**, published ACTIVE, in the full listing shape (see
below).

`--create` is what makes the button able to re-list a product deleted in Shopify.
Without it those perfumes were counted as "missing" and skipped, so the button
appeared to do nothing at all. The queryset is `shopify_syncable(...)` filtered on
"perfum", so a category switched off for Shopify is never published by it.

**Descriptions** are uploaded as HTML that preserves the saved formatting (blank
lines → paragraphs, single newlines → `<br>`) instead of collapsing into one run
of text — applied on create and on every sync of an existing product.

## Per-product button (product page)

Perfume product pages show a manager-only **"Sync to Shopify"** button
(`sync_product_to_shopify`). One click: creates the product on Shopify if it's
missing (ACTIVE, with variants/price/inventory/image/SEO), then pushes its price +
decant-aware inventory. Only shown for products whose category contains "perfum".
A reliable manual alternative to the real-time signals — good for listing a new
perfume or a one-off re-sync.

## What a created perfume looks like

Both buttons create through `create_product_in_shopify`, which builds the listing
the way the storefront's existing ones are built — a product created with a single
"Default Title" variant cannot be given decants later without rebuilding it:

- **Three sizes under the `Tamanho` option**: `100ml`, `10ml`, `5ml`, with SKUs
  `<barcode>`, `<barcode>-10ML`, `<barcode>-5ML`.
- **Quantities follow the same reserve rule as the update path**: the 100ml gets
  `on-hand − 2` (the last two bottles are the shop's samples), and the decants show
  `10` available while any bottle remains, `0` when the product is fully out. So a
  perfume with 9 on hand lists 7 full bottles and both decants.
- **Prices**: the app's price on the 100ml; the decants at 17.5% and 11.5% of it,
  rounded to 5 cents, matching what is already on the store. Cost is set on the
  full bottle only.
- **Title**: brand + series + name + EDP/EDT + volume, prettified (`Lattafa Khamrah
  Qahwa 100ml`), never the app's shouty upper case.
- **Vendor** = the prettified brand (`Lattafa`) — vendor drives the store's brand
  collections, so `LATTAFA` would split them — and **product type** `Perfume`,
  both matching the existing listings.
- **SEO** title `<title> | Perfume Árabe` and a description built from the
  product's own text, closing with the shop and where it ships; tag `Perfume
  Árabe`; the product photo uploaded if there is one.
- *Inspired by* is deliberately **never** sent to Shopify (trademark decision);
  it stays an internal field.

## Behaviour & limitations

- **Match key is barcode = SKU** (except `sync_shopify_barcodes`, which matches by
  title to *repair* the barcode). A product whose barcode doesn't match any
  Shopify variant SKU is skipped (`not in Shopify`). Placeholder/fake barcodes
  (e.g. `7777777777777`) won't match — fix the SKU in Shopify or the barcode in
  the app, then re-run.
- **Only fills missing images** by default (products with no image on Shopify).
  Use `--overwrite` / `--overwrite-image` to replace.
- **Variants.** A perfume is created with its three `Tamanho` sizes (above);
  everything else gets one default variant (one barcode = one variant).
- **Created as DRAFT** by default (`sync_shopify_products`) so a mistake never
  goes live instantly — publish from Shopify or use `--status active`. The two
  perfume buttons create **ACTIVE**: a perfume the shop is selling belongs on the
  storefront the moment it is listed. **Test with one product first**
  (`--barcode … --apply`) before a bulk run.
- **Idempotent:** re-running skips products that already exist with an image, so
  it's safe to run repeatedly.
