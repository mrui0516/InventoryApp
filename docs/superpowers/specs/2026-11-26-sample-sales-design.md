# 样品销售：不再拦截，只做标记（设计）

日期：2026-11-26。状态：待店主确认。

## 为什么

每家店每款留 1 瓶开封样品（Settings → Rules 的 `samples_per_shop`），按原价能卖的件数 = 库存 − 样品。现在只有 Sell 页能卖样品（价格类型选 Sample、数量固定 1、价格手填、只能卖本店那瓶），于是：

- **Pedido 转销售**：报价数量超过"样品以外的库存"时，Sell 购物车那一行是红的，整单交不了；
- **补录历史销售**（Orders → manage → new，勾扣库存）：没有 Sample 选项，保存时报 "Only the shop samples are left of …: sell it as a sample."，存不进去，可那笔销售早已发生。

店主要的是：卖样品永远直接卖，只在记录里标出来。

## 目标

1. 任何卖货路径（Sell、Pedido 转 Sell、补录 / 修改订单）都不因"只剩样品"被拦；只有两家店合起来**一件都不够**时才拦。
2. 卖出的样品瓶一定标记（`Sale.is_sample`），而且只标那几瓶。
3. Pedido 每输入一次就是一行，同一款可以分两行（比如一行普通、一行 Tester）。
4. Pedido 每行可以标 Tester；Pedido PDF、B2B 客户订单、销售记录、收据都显示这个标记。

## 不做

- 不改 Shopify 库存规则（两店合计，有分装的 100ml 款减 2）。
- 不改样品数量规则本身（每店几瓶仍在 Settings → Rules）。
- 不改利润和成本算法：样品照样按 FIFO 成本计入。

## 选定方案：自动拆行（店主 2026-11-26 选 A）

考虑过的三种：

| 方案 | 做法 | 取舍 |
|---|---|---|
| **A 自动拆行（选定）** | 普通行先用样品以外的库存，不够的件数从样品瓶出，自动拆成单独一行标 Sample，单价、付款方式同原行 | 标记精确，三条路一个规则；订单里可能多出一行 |
| B 整行标记 | 用到样品就整行标 Sample | 简单；3 瓶里 1 瓶是样品也整行算样品，批发优惠和报表会偏 |
| C 只标手动勾的 | 没勾的行用到样品也按普通记 | 最简单；记录里看不出哪几瓶是样品 |

## 规则（店主已定）

- **原价能卖的件数**（这家店能拿到的）= 本店库存 − 本店样品，加上其它店各自的"库存 − 样品"，再加上没分店的批次。和现在一样。
- **普通行**：数量 ≤ 原价能卖的件数 → 照旧（缺的从另一家店自动调货）。超出的部分 → 从样品瓶出（先本店，本店没有再从另一家店调，对方的样品也可以用），并**拆成一行 `is_sample=True`**。
- **Sample 行**（手动勾的）：整行都是样品。先用本店的货（含样品），本店不够再从别家调（含对方样品）。数量不限于 1，价格照常选、可改，不再必须手填。
- **只在两家店合计不够时拦**："Not enough stock for X. Needed n, both shops have m."
- **补录 / 修改订单**：同样按上面拆行；不勾"扣库存"时不扣、不拆，只保留手动勾的标记。
- **标记的文字**：员工界面英文 "Sample"；客户看到的单据（Pedido 页面和 PDF、B2B 订单、收据）葡语 **"Tester"**。不用 "Amostra"，因为它是分装小样那个产品类型的名字。

## 要改的地方

**库存（`services/store_stock.py`、`services/stock_ops.py`）**
- 新增"这家店能拿到的原价件数"（现在 `bring_in_for_sale` 里算过一遍，抽出来）。
- 新增"让本店手上有 n 件（含样品）"：本店不够时，先拿别家的原价件数，再拿别家的样品，照样记调货欠款。
- `consume_stock_fifo(sample=True)` 改用这个，不再要求本店自己有。
- 一个共用函数把一行拆成 `[(数量, 是否样品)]`，Sell 和补录都用它，在扣库存的事务里按当时的库存计算（同一单里同一款两行，第二行看到的是第一行扣完后的库存）。

**Sell（`outbound.html`、`outbound_view`）**
- 弹窗：Sample 从价格类型里拿掉，改成一个独立的勾选；价格类型只剩 Retail / Wholesale / Last。
- 校验：同一款所有行（普通 + Sample）的数量合计 ≤ 两家店合计件数（含样品）才能提交；普通行超过原价件数时，行上显示提醒 "N from the shop samples"（会被拆成 Sample 行），不再显示红色的 No stock / Exceeds。
- 购物车需要知道合计件数：`_sell_line_ctx` / `check_barcode` 返回 `total`。
- 服务端每行按拆行规则写一或两条 `Sale`。

**Pedido（`pedido.html`、`pedido_views.py`、`pedido_drafts.py`、`pedido_pdf.py`）**
- `addRow` 不再合并同款：每次加入都新开一行。
- 每行一个 "Tester" 勾选（隐藏字段 `sample` 与 `product_id` 平行）；`_read_lines` 读它；草稿行 JSON 加 `sample`。
- Disponibilidade 列：Tester 行显示 "Tester"。
- PDF：Tester 行在产品名下方小字写 "Tester"，和选款的写法一样。
- `sell_payload` / `_pedido_cart` 把 `sample` 带进 Sell 购物车，那一行就是 Sample 行，价格是报价。

**B2B 客户订单（`models/wholesale.py`、`services/wholesale_orders.py`）**
- `OrderRequestLine.is_sample`（迁移，默认 False）。
- `_follows_quote`：比较和复制都带上 `sample`；选款改用每一行自己的 `picks`，不再按 product_id 查（同一款两行时会串）。
- `save_to_pedido` 把订单行的 `is_sample` 写进草稿行。
- 客户的订单页（B2B）在 Tester 行显示 "Tester"。

**补录 / 修改订单（`sale_order_correction_form.html`、`_parse_correction_cart`、`order_corrections.py`）**
- 行编辑器加 "Sample" 勾选；`items_json` 带 `sample`；载入已有订单时带上每行的 `is_sample`。
- `save_sale_order_correction` 逐行扣库存和建 `Sale`（不再按产品汇总），按拆行规则；`sample_products` 集合去掉。归还库存仍按产品汇总，不变。

**显示 "Sample" / "Tester" 的地方**
- 员工：Orders 列表的详情层、订单详情页（表格和小票样式两处）、员工的销售记录、客户页的订单行、员工的客户订单页、Today 的订单行、产品页的销售列表。
- 客户：收据 PDF、Pedido PDF、B2B 订单页。
- 统一做法：产品名旁一个小标签；条件出现的标签不能让行变形或错位。

## 测试要覆盖

- 拆行：普通行刚好够 / 超出 1 件 / 只剩样品 / 两家店合计不够才拦；Sample 行用本店样品、本店没有时用别家样品并记调货；同一单同一款两行。
- Sell：Pedido 草稿转销售、带 Tester 行；超出原价件数时整单能交，记录里出现拆出的 Sample 行。
- 补录：只剩样品时能保存并拆行；修改订单保留每行的标记；不扣库存时不拆。
- Pedido：同款加两次是两行；Tester 勾选存进草稿、出现在 PDF；B2B 订单跟着报价带上 Tester，同款两行的选款不串。
- 各显示页面出现标签。
- 现有测试里依赖"样品数量固定 1 / 价格必须手填 / 只剩样品时拒绝"的要按新规则改。

## 界面实测

Sell 弹窗、Pedido 行、补录表单的行、带标签的列表行：360 / 390 / 820 / 1366 四种宽度，用最长的产品名量对齐、溢出、重叠。

## 交给实现时决定的

- 拆出的 Sample 行在购物车 / 记录里的先后（建议紧跟原行）。
- 标签的具体样式（沿用 Sell 购物车现有的 `.line-flag.sample`）。
- 迁移编号（现在最新是 0096）；部署前确认没和 codex 撞号。
