# 项目状态文档（Status）

> 快照时间：2026-07-16。记录当前代码、数据、测试、环境与版本控制的实际状态，便于评估"现在能不能上线/部署/迭代"。配合 [PRD.md](./PRD.md)（功能与验收标准）与 [ARCHITECTURE.md](./ARCHITECTURE.md)（技术架构）一起阅读。
>
> 第 7 节只记"发生了什么"，一条一行；具体改了哪些函数、跑了多少测试请查 `git log`。

---

## 1. 总体结论

| 维度 | 状态 | 说明 |
|---|---|---|
| 功能完整度 | ✅ 已覆盖 PRD 全部 20 个模块 | 正在生产使用中（真实业务数据） |
| 自动化测试 | ✅ 178/178 通过 | `python manage.py test stock` |
| 数据库迁移 | ✅ 无待生成迁移 | `makemigrations --check --dry-run` → "No changes detected" |
| 版本控制 | ✅ 已推送 GitHub，与 origin/master 同步 | 见第 4 节 |
| 本地虚拟环境 | ⚠ `.venv` 不完整/未使用 | 见第 3 节 |
| 生产可用性 | ⚠ 仍是开发配置 | `DEBUG=True`、`SECRET_KEY` 硬编码（见第 6 节） |
| 数据备份 | ❌ 无 | `db.sqlite3` + `media/`（79MB）无任何备份机制 |

---

## 2. 当前数据规模

（来自 `db.sqlite3` 实时查询，2026-07-15）

| 实体 | 数量 |
|---|---|
| Product（产品） | 392 |
| Purchase（采购批次） | 582 |
| InboundOrder（进货订单） | 201 |
| SaleOrder（销售订单） | 1,695 |
| Sale（销售行项目） | 4,166 |
| Customer（客户） | 35 |
| Supplier（供应商） | 16 |
| ARInvoice（应收发票） | 4 |
| DailySalesSummary（每日汇总） | 332 |
| Store（店铺） | 2 |
| 用户账号 | 4（其中 2 个超级管理员、2 个 staff） |

数据量处于"小型门店"规模，当前架构（含 `sale_profit_map_for_sale_ids` 的全量 FIFO 重放）尚未出现性能问题，但应作为未来扩容前的基线参考。

---

## 3. 运行环境状态 ⚠

项目实际有**三个** Python 环境，容易混淆：

| 环境 | 路径 | 状态 | 用途 |
|---|---|---|---|
| 便携版 Python | `F:\APP\python-3.13.5\` | ✅ 依赖齐全 | **服务器实际用的就是它**（`start.bat` → `runserver.py`） |
| 系统 Python | `C:\Users\maoru\AppData\Local\Programs\Python\Python313\` | ✅ 依赖齐全 | 跑测试 / management command |
| 项目内 `.venv/` | `.\.venv\` | ❌ **只有 Pillow/openpyxl，没有 Django** | 无法运行项目，别用 |

- `.venv` 处于"半初始化"状态，`activate` 后跑 `manage.py` 会直接 `ModuleNotFoundError: No module named 'django'`。
- 建议二选一：① `.venv\Scripts\pip install -r requirements.txt` 补齐；② 直接删掉 `.venv` 以免误导（已在 `.gitignore`，不影响仓库）。
- 便携版 Python 装依赖用 `F:\APP\install_deps.bat`（读 `InventoryApp\requirements.txt`，单一来源，不会漂移）。

---

## 4. 版本控制状态

- 远程：`origin = https://github.com/mrui0516/InventoryApp.git`，本地分支 `master` 跟踪 `origin/master`。
- **已推送到 GitHub**（2026-07-16，密钥扫描确认 `.env`/Cloudinary 密钥从未入库后推送），`master` 与 `origin/master` 同步 → 代码已有异地副本。
- `.gitignore` 已正确排除 `db.sqlite3`、`media/`、`.venv/`、`.env`、临时文件等。
- `.env`（Cloudinary 密钥）不入库，仅存在于本地/U 盘。

---

## 5. 测试与质量状态

- `python manage.py test stock` → **178 个测试全部通过**（耗时约 132s）。
- `python manage.py check` → 无系统检查问题。
- `python manage.py makemigrations --check --dry-run` → 无待生成迁移，模型与迁移文件一致。
- 迁移历史：33 个迁移（`0001_initial` ~ `0033_saleorder_affects_stock`），体现了从早期"扁平 Sale 表"到"SaleOrder + Sale 行项目"、品牌/系列结构化、AR 模块、考勤、打印配置、库存调整审计、销售目标、多店铺等逐步演进的过程。
- 外部集成（Shopify / Cloudinary）的测试**全部 mock**，不发真实网络请求。

---

## 6. 已知风险与技术债

| 项 | 现状 | 风险等级 |
|---|---|---|
| `SECRET_KEY` 已轮换、改从 `.env` 读；旧 key 仍在 git 历史 | 新 key 不入库、旧 key 已弃用失效；**仓库已转 private**（2026-07-16）。旧 key 仍在历史但已无价值 | 低 |
| `db.sqlite3` + `media/`（79MB）本地已有每日备份，但无异地副本 | 本机失火/被偷则本地备份同亡（代码已推 GitHub，数据未上云） | 中 |
| Cloudinary API Secret 明文存于 `.env`（U 盘上） | U 盘丢失/被盗即泄露；建议轮换 | 中 |
| `.venv` 不完整（见第 3 节） | 新环境/新协作者按 `.venv` 操作会直接失败 | 中 |
| `sale_profit_map_for_sale_ids` 全量 FIFO 重放 | 当前 4,166 条销售记录下可接受，规模增长后需关注 | 低（中长期） |

**已审查并确认维持现状的事项**（不应在后续代码审查中重复提出）：

- `ar_list_view`/`ar_detail_view` 仅使用 `@login_required`（不要求经理权限）：页面内全部 € 金额字段已通过模板 `{% if show_sensitive %}` 对非经理角色隐藏，不构成敏感数据泄露。（2026-06-12 复审确认）

**约定**：前端不加"功能介绍"类说明段落，只保留标题、字段标签、数据、告警与空状态提示。

---

## 7. 变更记录

一条一行，最新在前。细节查 `git log`。

- 2026-08-17: **Inbound contrast fix + receive dialog + Today page density** -- (1) **Bug: the inbound panels had no surface at all.** `.operation-card` was only ever defined inside outbound.html's inline <style>, so inbound used the same class names with no background/border and its form blended into the page. Promoted operation-card/-head/-title/-kicker and summary-badge into app.css, so both pages share one definition. (2) Awaiting receipt: wide scrolling table -> compact cards. (3) Receive dialog made scrollable (Confirm is always reachable) and much denser: three-up header fields with small labels, tighter line rows and 34px thumbs. (4) /today: dropped the payment doughnut (and with it the Chart.js CDN request) and moved Payment mix directly under the sales/orders card; KPI cards compacted (filler sub-lines removed, two-up on phones). 257 pass. NOTE: app.css changed -> run collectstatic on deploy.
- 2026-08-17: **Filter slimmed / Today orders inlined / Inbound follows the outbound pattern** -- (1) Product filter cut to two rows: the heading and every field label are gone (each select names itself through its first option), leaving a category chip row plus one control row with search, the three selects, Apply and Reset. (2) /today: orders no longer hide behind a per-order dialog; each renders as a full inline card (number, time, store, customer, payments, profit, total) with its products listed underneath -- past three they fold into a 'N more' disclosure, mirroring the phone card pattern. Modals removed. (3) Inbound rebuilt on the outbound logic: the tall Step-3 side panel is replaced by a sticky checkout bar (supplier, pcs, warning count, total cost, confirm) with the lines table now full width, dead panel CSS removed, and camera scanning added -- tuned for receiving, the camera stays open and keeps adding products instead of closing after one scan. 257 pass.
- 2026-08-17: **Product list desktop pass** -- category chips were clipped because they sat in one narrow toolbar column while forced equal-width and nowrap; they now get their own full-width row and size to their content (phones keep the equal-width single row). Desktop filter layout rearranged to categories on row 1, then search + brand/stock/sort on row 2 (>=1200px). The three counter cards were removed as a separate section and folded into the Product Table header as compact pills, on desktop and mobile alike. 257 pass.
- 2026-08-17: **Product detail density + products become manager-owned** -- (1) Detail page: the Product Profile table repeated what the page header already showed (display name, barcode, category) and spread six values over a row-per-field table; replaced with a compact multi-column definition grid, dropped the duplicated fields and the explanatory sub-headings, and tightened hero/KPI/gallery spacing on phones (KPIs two-up, 3-up gallery). (2) **Employees are now view-only for products**: add_product and edit_product require manager access, and the Add Product button plus every Edit affordance are hidden from employees in the list. Tests that asserted the old employee add/edit rights were inverted. (3) Outbound line modal: the unit price is **editable again** (Retail/Wholesale still prefill it) -- reverting the earlier lock-to-discount-only decision at the owner's request. 257 pass.
- 2026-08-17: **Product list mobile pass 3** -- filter actions no longer look identical: Apply is a full-width primary, 'More filters' an outline disclosure with an icon, Reset a plain link; dropped the equal-width flex that was squeezing and clipping the button labels, and category chips became rounded pills (smaller type so names like 'Accessories' fit). Page header slimmed on phones (smaller title, short 'Sync Shopify' / '+ Add' labels). With that space freed, product rows were enlarged: 52px thumb, larger name, and the barcode is back on the meta line alongside the sales figures. 257 pass.
- 2026-08-17: **Product list mobile pass 2** -- category chips forced onto one row (equal widths, no wrap); the three counters compressed from stacked cards into one compact 3-up strip with shorter labels (Products / In stock / Units on page); wholesale price added back to the phone row as a muted 'Whs' line under retail; product name and specification (volume) split onto separate lines; filter block slimmed -- heading hidden on phones, smaller labels/inputs, and Brand/Stock/Sort moved behind a 'More filters' collapse on phones while staying inline on desktop via display:contents. 257 pass.
- 2026-08-17: **Product list mobile density** -- each product used to render a ~280px stacked card (64px thumb block + four Sales/Retail/Wholesale/Stock tiles + View/Edit buttons), so barely two fitted on a phone screen. Rebuilt as a dense tappable list: one ~64px row per product (thumb / brand / model+name+spec / price + stock pill), the whole row links to the detail page and a pencil column links to edit -- roughly 4x more products per screen. Wholesale, barcode and category moved off the phone row (still on desktop + detail). Export panel folded into a collapsed <details> so it no longer pushes the list down, and the secondary filters sit two-up on phones. Desktop table unchanged. 257 pass.
- 2026-08-17: **Outbound follow-ups** -- (1) Scanner read nothing even with a sharp barcode: removing the crop box while also asking for 1080p meant ZXing had to decode a full 1080p frame per pass, far too slow to ever land a read. Now a generous crop (92% x 60%) at 1280x720, more 1D formats (CODE_39/ITF), and any decoded value is looked up so an unknown code reports 'not found' instead of being silently dropped. Added a typed-barcode fallback inside the scanner. (2) Scanner dialog capped at 38vh video + scrollable so it fits the screen. (3) Customer block reduced to one compact row; all 'walk-in' explanation removed (shows a dash instead). (4) Replaced the meaningless 'line' wording everywhere with 'product'. (5) **Bug: the out-of-stock banner never disappeared** -- only the info toast had a timer, so a stale 'no stock' error stayed visible while later products scanned fine. All toasts now auto-dismiss and are cleared as soon as a good product is opened or added. All changes apply to desktop and mobile. 257 pass.
- 2026-08-17: **Outbound: scanner accuracy + page/dialog redesign (desktop + mobile)** -- (1) Scanning failed because the qrbox crop threw away most of the frame and the video ran at default resolution; now it scans the FULL frame at 1920x1080 with continuous autofocus, uses the native BarcodeDetector where available, and adds a zoom slider (starting at 2x) plus a torch button. Small barcodes are read by zooming, not by moving closer -- phone lenses cannot focus under ~10cm, which is why getting close blurred. (2) The big 'Review & confirm' side panel is gone (its content is already in the confirm dialog); the cart is now full width and a compact **sticky checkout bar** (lines/pcs, warning count, total, confirm) sits above the fold on desktop and mobile, so selling never needs scrolling to the bottom. (3) The confirm dialog was rebuilt dense like the add-product modal -- total first, then a 3-up facts strip, payments, warnings and lines -- and is scrollable so the Confirm button is always reachable. Dead side-panel CSS removed. 257 pass.
- 2026-08-17: **Outbound page: camera scanning + line-modal redesign** -- (1) barcode field gains a camera button; scanning uses the already-vendored html5-qrcode (EAN-13/8, UPC, CODE-128), lazy-loaded on first use so the ~370KB is not paid on every POS load; works on iOS Safari + Android (needs HTTPS). (2) The add-to-cart modal was taller than the screen; rebuilt as a dense one-screen layout (compact product header with thumb + brand/name/spec, and modal-dialog-scrollable so the Add button is always reachable). (3) Quantity is now 1-5 chips (plus a + chip for larger), capped/disabled by available stock. (4) The price is fixed once Retail/Wholesale is picked -- the editable price box is gone and only Discount adjusts it; a manual field appears only when the product has no saved price at all. (5) Payment methods shrank from three tall cards to one compact row. 257 pass.
- 2026-08-17：**今日区细节修整**——删掉无意义的 "Today focus" 标签；收款方式从“宽度随金额伸缩的胶囊行”改为与 KPI 完全一致的**等宽磁贴**；修复订单明细**横向溢出屏幕**的根因(grid 子项 min-width:auto 导致长品名无法收缩)；明细行改为**品牌/型号·名称/规格分行换行展示**，并补上**每行支付方式**。不再需要横屏。⚠ 含 app.css 改动，PA 需 collectstatic。
- 2026-08-17：**手机端 Dashboard(员工今日区)重做**——① Today operations 三个 KPI 从"每个占一整行的大卡"改成**紧凑三格条**(手机上金额格占满一行、Sold/Orders 并排),不用一直下滑。② Today sales orders 从"表格套表格 + 左右横滑"改成**扁平订单卡片列表**:每单一张卡(单号·时间·客户·件数·金额),点 Items 展开成**竖排明细行**(缩略图+品名 / ×数量·金额),完全不用横滑、无嵌套表。③ 收款方式改成紧凑胶囊行。④ 顶部 "Mobile access" 文字按钮改成**二维码小图标按钮**(点开弹窗看/扫大图),不再占一条。⚠ 含 app.css 改动,PA 需 collectstatic。——从"白底+灰图标"的素样式改成**与侧边栏同色系的深色导航条**(navy 渐变、顶部细描边+上投影);选中项用 **Material-3 风格高亮胶囊**(图标坐在蓝色圆角底上、文字转白加粗),点按有轻微缩放反馈。手机顶栏(深)+底栏(深)包住浅色内容,视觉统一。⚠ 改的是 app.css,PA 需 `collectstatic` 才生效。
- 2026-08-17：**全局手机端优化（共享层）**——① 新增**底部标签栏**(手机 ≤767.98px):Home/Sell/Products/Sales + Menu(开侧边栏),SVG 图标、拇指可达、当前页高亮;侧边汉堡菜单保留放完整菜单。② **安全区适配**:顶栏 `env(safe-area-inset-top)`、底栏 `inset-bottom`,内容底部留出 `--tabbar-h` 不被遮挡;各页吸底保存条/悬浮按钮(sticky-actions/form-actions/back-top)自动抬到标签栏之上。③ **密度微调**(≤576):标题/关键数字/卡片内边距再收紧(在已有的 16px 输入防缩放、按钮 ≥44px 触控基础上)。全部改在 app.css + base.html,一次覆盖 38 页,未逐页改。

- 2026-08-17：**手机出库页交互/CSS 优化**——① 购物车每行从"6 个满宽标签行"压成**紧凑卡片**:产品占第一行,第二行是一排带小标签的 chip(Qty/Unit/Disc/Pay/= 合计),编辑/删除单独一行右对齐。② 修掉手机端**错位的列标签**(旧的是 6 列表,加了 Disc/Pay 后变 7 列,标签全串位)。③ 底部**吸底确认按钮显示实时总额**("Review & confirm · €X.XX"),不用滚回去看金额。④ 标题/卡片/间距在手机上整体收紧,输入框保持 ≥16px 防 iOS 缩放,编辑/删除按钮保持 ≥40–44px 触控尺寸。纯 CSS + 一处 JS 文案,无逻辑改动。

- 2026-08-17：**一批 UI/边界优化**——① 出库边界:经理选"全部店铺"时**禁止出库**(该聚合视图没有唯一归属店铺,否则销售会静默落到错误店铺),GET 显示提示条+禁用确认按钮、POST 服务端硬拦截;员工锁定本店不受影响。② Today 页订单改为**最近售出在前**(`-created_at`)。③ 产品列表页 Client/Shopify 两个导出框合并成**一个标签页切换**面板(省空间)。④ 产品列表 Category 下拉框改成**快速筛选按钮组**(All/Perfumes/Accessories/Shisha,共 3 个分类;隐藏域保存值,点按钮即时提交且不丢其它筛选)。⑤ Supplier 页由卡片改成**按国家分组的表格**(与产品列表一致的行式风格)。1 测试(出库拦截)。

- 2026-08-16：**修复员工登录后看不到"今日销售"的回归**——删 Today operations 时把员工唯一能看今日销售的地方也删了(而 /today 页是 `@manager_required`,员工进不去)。现按**员工专属**恢复该区块(经理仍走干净仪表盘 + /today):今日售出件数/销售额/订单数、今日收款方式占比、今日订单明细。视图里这段**只在非经理时计算**(不跑利润重放,经理仪表盘不受影响)。顺带修好被这次删除弄红的回归测试(`test_dashboard_hides_sensitive_sections_for_regular_user` 断言 "Sales Today" 却已被删)。
- 2026-08-16：**仪表盘再提速**——低库存那块(每次都跑的 sold 相关子查询)改为**按店铺+分类缓存 5 分钟**、且产品改成轻量 dict(不再缓存模型实例);月度/环比缓存 60s→**300s**(减少重算频率);低库存缩略图改用 **Cloudinary CDN 小图**(96px、边缘缓存,取不到回退本地)+ `loading=lazy`。256 通过。
- 2026-08-16：**仪表盘提速 + 删除 "Today operations" 区块**(已有 /today 页)。① `sale_profit_map_for_sale_ids` 加**快速路径**:销售有 `cost_basis` 就直接算利润,全部有则**完全跳过整库 FIFO 重放**(回填后仪表盘/各页利润不再重算历史)。② 删掉仪表盘 Today operations(模板 + 视图里今日销售/进货/利润的未缓存重活),保留月度总览和低库存预警。移除一个过时测试。256 通过。
- 2026-08-16：**修复利润用错批次成本的 bug(根因:事后 FIFO 重放因无留痕的库存改动而错位)**。改为**销售当下记录真实成本**:`consume_stock_fifo` 返回本次消耗的 FIFO 成本,存进新字段 `Sale.cost_basis`(迁移 0036);利润优先用 `cost_basis`,无则回退重放。新增 `backfill_sale_cost_basis` 命令给历史销售回填成本——用"实际每批消耗(qty−remaining)+ 销售按最新优先匹配"的**倒序 FIFO**,让近期销售锚定到真正来源的最新批次(例:近单从旧批 21.95 修正为新批 15.50)。3 测试,257 通过。
- 2026-08-15：**修复分装"数量0仍可购买"的根因**——分装变体是 `tracked=False`+`policy=CONTINUE`,Shopify 当它无限有货。库存同步现在会把要动的变体强制成 `tracked=True`+`inventoryPolicy=DENY`(数量才生效),即使数量没变也修策略。另修 `all_variants_by_sku` 只取首个变体的 bug → 改 `variants(first:10)`,批量同步现在真正管理分装(之前只动 100ml)。client 加 `set_variant_stocked`;记录含 tracked/policy。1 测试。
- 2026-08-15：**分装规则最终确定**——预留的 2 瓶=两店样品;100ml=max(N−2,0);**分装(5ml/10ml)只要有货(N≥1,样品可分装)就=99,完全没货(N=0)才=0**。(撤销当天早些时候"分装跟随100ml"的误改。)N=0 产品要真正变 0 需**完整跑一次**库存同步(之前中断过);若某"没货"产品同步后分装仍显示,是 app 在库≠0(库存数据要补正)。
- 2026-08-15：分装库存规则改为**分装跟随 100ml**——100ml 有货(N>2)时 5ml/10ml=99,**100ml 为 0(N≤2)时分装也=0**(整个产品显示断货),不再在 N≤2 时把分装留 99。`_inventory_targets` 用 `full=max(N-2,0)`;新增测试证明产品页按钮路径(`find_variant_by_sku`)与批量算出的目标一致(无独立 bug,"变成 0"是该产品在库≤2)。2 测试。
- 2026-08-15：`sync_shopify_perfumes` **提速**——已存在产品直接用 `all_variants_by_sku` 拿到的 GID,只推**有变化的库存 + 品牌合集**(不再逐个 `find_product_by_sku` / 每次重推 description);`--create` 才建缺失产品(避免误建 decant/mix)、`--full` 才重推描述。默认几十次调用、秒级。按钮走默认快速版。1 测试。
- 2026-08-09：同步创建香水时 Shopify **标准分类设为 Eaux de Parfum**(`hb-3-2-8-3`,仅香水);批量 `sync_shopify_perfumes` 额外**把每个香水加入其品牌的手动合集**(智能合集按 vendor 自动归类,不动);client 加 `all_collections/collection_add_products`。2 测试。
- 2026-08-09：客户产品清单 Excel 导出新增 **EAN 列**(在 Product 与 Category 之间,文本格式保留完整位数);**产品名后追加 Specification(容量)**。新增 **`backfill_perfume_spec`** 命令:给缺 Specification 的香水补 `100ml`(已有容量的不动;非香水不动;dry-run 默认)。3 测试。
- 2026-08-09：新增 **`sync_shopify_storefront`**(每日 PA 定时任务,dry-run 默认)——① 断货隐藏:香水在库 N=0→产品设 DRAFT、N≥1→ACTIVE(N≤2 时 100ml 断货但分装在售,产品仍可见);② **Novidades** 合集设为最新 20 个香水(按 created_at);③ **O Mais Vendido do Mês** 合集(缺则创建)设为当月 app 销量前 5。client 加 `set_product_status/find_collection_by_title/create_collection/collection_product_ids/set_collection_products`,`all_variants_by_sku` 增 status。合集按标题匹配,主题区块由用户在 Shopify 手动接。**Back in stock(需 Product 加断货/补货状态字段)= Phase 2 待做**。2 测试,241 通过。
- 2026-08-08：产品列表加 **"Sync all perfumes to Shopify" 批量按钮**(经理)——后台跑 `sync_shopify_perfumes --apply`(分离进程,避免 web 请求超时;日志 `logs/shopify_perfumes_sync.log`):对所有香水创建缺失/更新描述/推价格+分装库存。**产品描述按保存格式上传**(空行→段落、换行→`<br>`,不再合成一坨;创建和每次同步已有产品都应用)——`_shopify_description_html` + client `update_product_description`。库存分装规则已是 100ml=max(N−2,0)/分装 99·0,按 sku 识别整瓶变体(与 ml 标签无关)。5 测试,239 通过。
- 2026-08-08：产品页(仅香水、经理)加 **"Sync to Shopify" 按钮**(`sync_product_to_shopify`)——一键把该产品推到 Shopify:没有则创建(ACTIVE,含变体/价/库存/图/SEO),然后按分装规则推价格+库存。可靠的手动替代实时信号,也用于新品上架。香水口径 = category 含 "perfum"。另加 `SHOPIFY_LOCATION_ID` 可配置库存地点(多地点时指定 Amadora)。4 测试,234 通过。
- 2026-08-07：Shopify 库存同步改为**分装感知**——变体 SKU 约定 `条码/条码-10ML/条码-5ML`;整瓶在库 N 下:**100ml = max(N−2,0)**(预留 2 瓶给分装,≤2 断货)、**10ml/5ml = 99(N≥1)/0(N=0)**;无分装的产品 100ml=N。`_inventory_targets` + `DECANT_RESERVE=2`/`DECANT_AVAILABLE=99`;命令与实时(售出/进货/调整)都走这套。3 测试,230 通过。
- 2026-08-07：新增 **Shopify 价格+库存同步(app 为准)**——`sync_shopify_inventory` 命令按 barcode=SKU 把 `default_price` 和在库(Σremaining)推到 Shopify(一次拉全部变体本地比对,dry-run 默认,支持 `--price-only/--inventory-only/--brand`);外加**实时**:`SHOPIFY_INVENTORY_SYNC=1` 时,售出/进货/库存变动在提交后把该产品在库推到 Shopify;**改价格也实时推价**(Product pre/post_save 检测 default_price 变化)。幂等、不阻断保存、默认关。client 加 `all_variants_by_sku/find_variant_by_sku/update_variant_price/set_inventory_available`,shopify_sync 加 `sync_product_price_inventory`,signals 加 Sale/Purchase 库存推送。5 测试,225 通过。
- 2026-08-06：新增 **`sync_shopify_barcodes`** 命令——EAN 在 app 改过后,按**产品标题**(稳定键,标题没变)匹配 Shopify 产品,把当前条码写回 Shopify 变体的 SKU+barcode;一次性拉全部 Shopify 产品在本地匹配(dry-run 只几次 API),重复标题视为歧义跳过;默认 dry-run,`--apply` 才写。配套 client 加 `all_products_by_title` / `update_variant_barcode_sku`。Cloudinary 侧用现有 `sync_cloudinary_images --apply` 按新条码重传。4 测试。验证过 token(216 产品,标题零重复,标题匹配可靠)。
- 2026-08-06：**已上线到 PythonAnywhere 欧盟区**(scentory.eu.pythonanywhere.com,Python 3.13,数据+图片已迁移,PA 为准数据源)。新增**备份体系**:`manage.py backup_db`(SQLite 在线备份 API,一致性快照+完整性校验+保留N份)供 PA 每日定时任务用;`scripts/pull_backup_from_pa.py`(纯标准库,PA API 拉最新快照到 U盘,含"错过即补跑",U盘没插则安全跳过)。docs/BACKUP.md 重写为云端模型;`backups/`+`scripts/.pa_backup.ini` 已 gitignore。另加**仪表盘一键下载按钮**(经理可见,`download_db_backup` 视图 → 一致性快照直接下载,可配合浏览器下载目录=U盘)。修复:requirements 缺 `qrcode`;STATICFILES_DIRS 空目录守卫。3 测试。下一步:Shopify 库存双向同步。
- 2026-08-04：**上线准备(第一阶段:生产安全加固)**——`settings.py` 新增 `if not DEBUG` 生产块:`SECURE_PROXY_SSL_HEADER`、强制 HTTPS(可环境变量关、测试运行时自动跳过避免重定向)、安全+HttpOnly Cookie、HSTS(3600s 起步渐进)、防嗅探、`X_FRAME_OPTIONS=DENY`、12h 滑动会话自动登出。只在线上 `DEBUG=False` 生效,本地/测试不受影响(`check --deploy` 已验证,213 通过)。新增 **docs/DEPLOY.md**——PythonAnywhere 欧盟区分步上线手册(域名 `gestao.scentory.pt` 走 Amen CNAME、环境变量、WSGI、静态文件、备份 3-2-1)。下一阶段:django-axes 登录锁定 + 2FA。
- 2026-07-31：产品详情页移除 **Stock Ledger · Full Path** 分区(已融合进 `/sales-history/` 专页,产品页仅保留"View full sales history"链接,不再重复);产品详情视图不再计算台账。213 通过。
- 2026-07-31：产品销售历史专页 `/sales-history/` 将 **Sales Detail 与 Stock Ledger 融合成一条时间线**——销售明细(店铺/客户/单价/小计/支付/利润*)内联进 Stock Ledger 的销售行,配运行余额+对账横幅,便于一眼观察"卖了什么、库存怎么走";明细的独立表格移除。利润改为对全量流水一次性计算并复用。共享片段 `_stock_ledger.html` 新增 `ledger_detailed` 详细模式(产品详情页仍用精简模式)。213 通过。PRD F2.2.17。
- 2026-07-28：新增**产品销售历史专页** `/sales-history/`（经理）——按产品名/条码搜索→选中后展示完整销售明细(时间/**店铺**/订单#/客户/数量/单价/小计/支付/利润*)+按店铺/按月汇总+KPI+复用的 Stock Ledger 完整流水;支持日期区间/店铺过滤。产品详情页 Sales History 精简为**仅最近 10 天**,其余通过"View full sales history →"跳转该专页(附旧单条数)。Stock Ledger 抽为共享片段 `_stock_ledger.html`。5 测试,213 通过。PRD F2.2.17。
- 2026-07-28：产品详情新增 **Stock Ledger（完整库存流水，Layer 1 重建账）**——合并采购(+)/销售(−,尊重 `affects_stock`)/手动调整(±) 为一条时间线并算运行余额,与实际在库(Σremaining)对账;不符时红色横幅指出"X 件无法用已登记事件解释"(多为批次数量编辑/删除等未留痕的漏洞)。修正 total-stock 上调的双计(建批次+日志)。无迁移,只读;经理可见。5 测试,208 通过。往后记录"销售吃了哪个批次"的 StockMovement(Layer 2)为下一步。PRD F2.5.2.10。
- 2026-07-28：销售记录页(经理)默认改为**本月销售日历**——月网格显示有销售的天(订单数+金额),点击弹窗看当日订单详情(含对应店铺,复用新片段 `_sales_order_entry.html`);顶部切月/年度视图/**按产品过滤整页**;年度趋势移到 `?view=year`,月柱点击进入该月日历;区间列表仅非日历模式渲染。KPI/图表沿用。新增 3 测试;更新 2 个旧测试(默认视图改变)。203 测试通过。PRD F2.2.16。
- 2026-07-27：修复 Shopify 导出 CSV 的 `Product category` 列——原来写的是本地分类裸名 "Perfumes"，Shopify 标准分类法匹配不到 → 触发 ML 自动分类把香水误归到 Pet 节点。改为映射到全路径 `Health & Beauty > … > Perfumes & Colognes > Eaux de Parfum`（Google 列同理映射到 Perfume & Cologne）；`Type` 保持本地名不变。存量 203 个香水已通过 API 批量改为 Eaux de Parfum。200 测试通过。
- 2026-07-23：产品性别分类上线——`Product.gender`（men/women/unisex，迁移 0035）+ 表单下拉 + Shopify 导出自动带 Homem/Mulher/Unissexo tag；存量 229 香水自动回填（男 47/女 45/中性 137）；Shopify 全部 227 产品打 tag、建 3 个性别智能 collection、主菜单加 Categorias 下拉（Homem/Mulher/Unissexo/Novidades）。199 测试通过。副本主题另新增：品牌走马灯、Top 5 月销区块（本地销量，可切线上）、Novidades 无限循环聚焦轮播、实体店版块、白色产品卡配色。
- 2026-07-22：Shopify 全量香水同步——228 个有图香水全部上架（112 已有 + 116 新建，CSV 导入 + Cloudinary 图链），品牌 collection 全配齐（9 个手动填满 + 5 个新智能 vendor 规则 + Novidades）；主菜单/footer/Contactos/Revendedor 页重建（葡语）；副本主题 "Copy of Dawn" 按 auryaperfumes.com 结构重排（首页/产品页/分类页/品牌走马灯/淡黄黑配色），待预览发布。ARD AL ZAFRAN→ARD AL ZAAFARAN 品牌拼写合并、1Ooml 笔误修正（本地+Shopify）。
- 2026-07-21：香水自动定价上线并合并（Perfumes 分类：批发=⌈当前 FIFO 成本+10⌉、零售=批发+12；成本变动自动重算；`price_locked` 经理锁价；价格对员工只读 server 端强制；`sync_perfume_prices` 回填实跑 4 更新）。195 测试通过。
- 2026-07-16：员工界面收窄（可见页面 Dashboard/Outbound/Products/Sales/Customers；AR/进货/考勤/Today/Catalog 对员工整页 302 拦截）。Products 对员工开放查看/搜索/新增/**编辑**/下载客户 Excel（隐藏成本/供应商比价/销售历史；进货、删产品/图、Shopify 导出仍仅经理）。Sales/Customers 员工视图为单日订单/无分析的精简版（按订单号搜索直达详情；按客户查订单对账；每单 **View 弹窗 + Print 按钮**；订单详情按店铺隔离）。样式与经理页统一。登录/登出自动开关考勤班次（免手动打卡）。178 测试通过。
- 2026-07-16：安全加固——`SECRET_KEY` 轮换并改从 `.env` 读（旧 key 弃用）、`DEBUG` 由 `.env` 控制（本机仍 True）、加 WhiteNoise + `STATIC_ROOT`（备将来 `DEBUG=False`）、`ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS` 可由环境扩展、加 `.env.example`。147/147 通过。仓库转 private 待手动操作。
- 2026-07-16：上线本地每日备份（`Downloads\InventoryApp-Backups`，计划任务，含恢复步骤 [BACKUP.md](./BACKUP.md)）。
- 2026-07-15：Shopify CSV 导出的图片两列改用 Cloudinary 公网 URL（此前输出局域网地址，Shopify 抓不到，一直是死链接）。新增 `services/cloudinary_urls.py`。
- 2026-07-14：补录历史订单可选「不影响库存」（`SaleOrder.affects_stock`，迁移 0033）；此类单不扣库存、不参与 FIFO，利润按销售额 50% 估算。
- 2026-07-13：订单修正支持改店铺（修下错店铺的单），订单与其全部明细行一并迁移，审计日志记 旧→新。
- 2026-07-13：产品图自动镜像到 Cloudinary（`public_id`=条码，动态文件夹模式）；本地图改为按品牌分目录（迁移 0032）。
- 2026-07-11：Shopify 缺品自动创建同步（`productSet` 一次调用建品，默认 DRAFT）。
- 2026-07-11：Shopify 商品图同步（staged upload → `productCreateMedia`，按 barcode=SKU 匹配）。
- 2026-07-04：代码清理（`/simplify`，4 个并行审查代理，行为不变）。
- 2026-07-04：修复当日支付统计不反映拆分支付（改从订单级 `SaleOrderPayment` 读）。
- 2026-07-04：修复订单修正页拆分支付重载即丢失的缺陷。
- 2026-07-04：订单修正中心按日期分组 + All stores 时显示店铺列。
- 2026-07-04：仪表盘多店铺营业额对比卡 + Today 订单店铺列。
- 2026-07-04：仪表盘细节优化（移除 7 处重复眉标、内联色令牌化）。
- 2026-07-04：全站通用 a11y + 一致性 sweep（12 个模板：去 emoji、`<h1>` 语义化、标签关联、主操作统一蓝）。
- 2026-07-04：出货页 audit 跟进（a11y harden、移动端触控目标 44px）。
- 2026-07-04：出货页 critique 跟进（去 Step 眉标、支付明细堆叠）。
- 2026-07-04：出货/POS 页视觉重设计（"锐利、数据自信的仪器感"，仅视觉层，交互与数据契约不变）。
- 2026-06-12：仪表盘内容调整——移除补货建议/策略洞察面板；新增当日支付方式统计与独立年度销售趋势页。
- 2026-06-12：**模块化单体重构阶段 1**——`stock/models.py` 拆为 `stock/models/` 域分区包（保持单一 app / 单一迁移历史）。路线图见 ARCHITECTURE 第 8 节。
- 2026-06-12：库存并发安全改造——新增 `services/stock_ops.py`（基于 `F()` 的条件 UPDATE 乐观并发，替代在 SQLite 上为 no-op 的 `select_for_update()`），冲突时 fail-fast。
- 2026-06-12：新增 `StockAdjustmentLog` 库存调整审计（迁移 0024）；调整 API 权限统一为 `has_manager_access`。
- 2026-06-12：多店铺 Phase 1（`Store`/`StoreProfile`，迁移 0028+0029 播种回填）→ 店铺管理页 → Phase 2 全量按店铺过滤 → 打印小票抬头按店铺（迁移 0030+0031）。
- 2026-06-12：Sales Trend 合并进 Sales 记录页（概览→下钻）。
- 2026-06-12：订单修正中心重写为与 POS 一致的购物车逻辑，修复"删除行不回滚库存"的缺陷。
- 2026-06-12：收银（POS）重构——行项目弹窗、订单级拆分支付（`SaleOrderPayment`，迁移 0026）→ 改按行选择支付 → 再增行内拆分。
- 2026-06-12：进货暂定→确认收货流程（`pending_receipt`，货到才产生库存）；确认收货改为弹窗。
- 2026-06-12：供应商管理增强（记分卡、平均交货周期、联系人字段迁移 0027、按国家分组）；移除已无用的散单归集功能。
- 2026-06-12：客户页可视化（月度消费柱/支付环形/Top Products/采购节奏）+ 日期范围筛选。
- 2026-06-12：销售记录页区间可视化（每日资金流/支付环形/Top Products）。
- 2026-06-12：新增当日汇总页 `/today/`（KPI、支付占比、订单表含详情弹窗、Top 产品、打印）。
- 2026-06-12：仪表盘增强（MoM 同期对比、销售目标 `SalesTarget` 迁移 0025、性能优化与缓存）。
- 2026-06-12：引入共享设计系统 `static/css/app.css`（收敛 27 个模板各自内联的 8 套漂移令牌）；后续 CSS 统一 Pass 1/2；表格溢出保护。
- 2026-06-12：整体布局改为经典 ERP 左侧固定侧边栏（移动端离屏抽屉）。
- 2026-06-12：目录页（对客）重设计为编辑式"香水索引"（瓷白 + 酒红 + 衬线，统一比例图块）。
- 2026-06-12：全站移除"功能介绍"说明文字（约 50 处）+ 统一精简提示消息。
- 2026-06-12：清理技术债（删死代码 469 行、一次性脚本、临时文件、未使用 import 与依赖）。
- 2026-06-12：项目纳入 Git，产出 `docs/`（PRD / ARCHITECTURE / STATUS）。

---

## 8. 建议的下一步（按优先级）

1. ~~推送到远程~~ ✅ 已完成（2026-07-16）。~~数据备份~~ ✅ 本地每日备份已上线；仅剩**数据异地副本**（Downloads → Google Drive 同步）。
3. **`SECRET_KEY` / `DEBUG` 收敛**：`settings.py` 已有 `.env` 加载器（见其顶部），把两者搬进 `.env` 即可；仓库是 public，旧 `SECRET_KEY` 必须换新。
4. **轮换 Cloudinary API Secret**（明文存于 U 盘上的 `.env`）。
5. **统一运行环境**：修复或删除 `.venv`，避免与便携版/系统 Python 三者混淆。
