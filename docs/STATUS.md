# 项目状态文档（Status）

> 快照时间：2026-06-12。记录当前代码、数据、测试、环境与版本控制的实际状态，便于评估"现在能不能上线/部署/迭代"。配合 [PRD.md](./PRD.md)（功能与验收标准）与 [ARCHITECTURE.md](./ARCHITECTURE.md)（技术架构）一起阅读。

---

## 1. 总体结论

| 维度 | 状态 | 说明 |
|---|---|---|
| 功能完整度 | ✅ 已覆盖 PRD 全部 18 个模块 | 正在生产使用中（真实业务数据） |
| 自动化测试 | ✅ 86/86 通过 | `python manage.py test stock` |
| 数据库迁移 | ✅ 无待生成迁移 | `makemigrations --check --dry-run` → "No changes detected" |
| 版本控制 | ✅ 已接入 Git + GitHub | 见第 4 节 |
| 本地虚拟环境 | ⚠ `.venv` 不完整/未使用 | 见第 3 节，实际运行依赖系统级 Python |
| 生产可用性 | ⚠ 仍是开发配置 | `DEBUG=True`、`SECRET_KEY` 硬编码（见第 5 节） |
| 数据备份 | ❌ 无 | `db.sqlite3` + `media/`（图片）无任何备份机制 |

---

## 2. 当前数据规模

（来自 `db.sqlite3` 实时查询，2026-06-12）

| 实体 | 数量 |
|---|---|
| Product（产品） | 386 |
| Purchase（采购批次） | 534 |
| InboundOrder（进货订单） | 202 |
| SaleOrder（销售订单） | 1,582 |
| Sale（销售行项目） | 3,749 |
| Customer（客户） | 32 |
| Supplier（供应商） | 15 |
| ARInvoice（应收发票） | 4 |
| DailySalesSummary（每日汇总） | 298 |
| 用户账号 | 3（其中 2 个超级管理员、2 个 staff） |

数据量处于"小型门店"规模，当前架构（含 `sale_profit_map_for_sale_ids` 的全量 FIFO 重放）尚未出现性能问题，但应作为未来扩容前的基线参考。

---

## 3. 运行环境状态 ⚠

- **系统 Python**（`C:\Users\maoru\AppData\Local\Programs\Python\Python313`）已安装 Django 5.2.4 及 `requirements.txt` 中的全部依赖，**当前项目实际依赖此环境运行**（`python manage.py ...` 默认命中该环境）。
- **项目内 `.venv/`** 仅安装了 `pip`、`Pillow`、`openpyxl` 三个包，**未安装 Django**，`manage.py` 在该环境下无法运行（`ModuleNotFoundError: No module named 'django'`）。
  - 即：`.venv` 目录存在但处于"半初始化"状态，不能直接 `.\.venv\Scripts\activate` 后运行项目。
  - 建议二选一：① 在 `.venv` 中补齐 `pip install -r requirements.txt`；② 若团队约定使用系统 Python，可考虑移除 `.venv` 以减少混淆（`.venv/` 已在 `.gitignore` 中，不影响仓库）。

---

## 4. 版本控制状态

- 仓库已初始化，远程已关联：`origin = https://github.com/mrui0516/InventoryApp.git`，本地分支 `master` 跟踪 `origin/master`。
- 已完成 1 次提交并推送（"Initial commit: Django inventory management system"，130 files）。
- **当前未提交内容**：`docs/` 目录（本次新增的 `PRD.md`、`ARCHITECTURE.md`、`STATUS.md`）尚未 `git add`/`commit`。
- `.gitignore` 已正确排除 `db.sqlite3`、`media/`、`.venv/`、临时文件等。

---

## 5. 测试与质量状态

- `python manage.py test stock` → **86 个测试全部通过**（耗时约 73s）。
- `python manage.py check` → 无系统检查问题。
- `python manage.py makemigrations --check --dry-run` → 无待生成迁移，模型与迁移文件一致。
- 迁移历史：27 个迁移文件（`0001_initial` ~ `0027_supplier_contact_person…`），体现了从早期"扁平 Sale 表"到"SaleOrder + Sale 行项目"、品牌/系列结构化、AR 模块、考勤模块、打印配置、库存调整审计、销售目标等逐步演进的过程。

---

## 6. 已知风险与技术债（详见 PRD 第 4 节，此处为状态层面摘要）

| 项 | 现状 | 风险等级 |
|---|---|---|
| `SECRET_KEY` 硬编码在 `settings.py`，`DEBUG=True` | 仓库已推送到 GitHub（需确认仓库可见性是 private/public） | 高（若仓库 public） |
| `db.sqlite3` + `media/`（约 89MB）无备份 | 单点故障即数据全部丢失 | 高 |
| `sale_profit_map_for_sale_ids` 全量 FIFO 重放 | 当前 3,749 条销售记录下可接受，规模增长后需关注 | 低（中长期） |
| `.venv` 不完整（见第 3 节） | 新环境/新协作者按 `.venv` 操作会直接失败 | 中 |

**已审查并确认维持现状的事项**：
- `ar_list_view`/`ar_detail_view` 仅使用 `@login_required`（不要求经理权限）：经审查确认页面内全部 € 金额字段已通过模板 `{% if show_sensitive %}` 对非经理角色隐藏，不构成敏感数据泄露，故维持现状（2026-06-12 复审确认，不应在后续代码审查中重复提出）。

---

## 7. 近期变更记录

- 2026-06-12：项目纳入 Git 版本控制，配置 `.gitignore`，初始提交并推送至 `github.com/mrui0516/InventoryApp`（分支 `master`）。
- 2026-06-12：基于现有代码逆向整理产出 `docs/PRD.md`（产品需求 + 验收标准）、`docs/ARCHITECTURE.md`（技术架构）、`docs/STATUS.md`（本文档）。
- 2026-06-12：库存并发安全改造——新增 `stock/services/stock_ops.py`（`consume_stock_fifo`/`restore_stock_fifo`，基于 `F()` 表达式的条件 `UPDATE` 实现乐观并发控制，替代在 SQLite 上为 no-op 的 `select_for_update()`）；`outbound_view` 与 `services/order_corrections.py` 的库存扣减/归还逻辑改为调用该模块，检测到并发冲突时 fail-fast 并提示用户重试。
- 2026-06-12：新增 `StockAdjustmentLog` 模型（迁移 `0024_stockadjustmentlog`）作为库存调整审计日志，Django Admin 只读展示；`api_adjust_purchase_stock`/`api_adjust_total_stock` 权限统一为 `has_manager_access`（原为 `is_staff or is_superuser`），每次成功调整写入审计记录，`api_adjust_purchase_stock` 同时改为条件 `UPDATE` 的并发安全写入。
- 2026-06-12：审查确认 `ar_list_view`/`ar_detail_view` 维持 `@login_required`（不升级为 `@manager_required`），详见上方"已审查并确认维持现状的事项"。
- 2026-06-12：上述改动后 `python manage.py test stock` 52/52 测试全部通过，无回归。
- 2026-06-12：启动"模块化单体"重构（保持单一 `stock` app / 单一迁移历史）。**阶段 1 完成**：原 `stock/models.py` 拆为 `stock/models/` 域分区包（core/catalog/partners/inventory/sales/finance/reporting/hr），`__init__.py` 重导出全部模型类，跨域外键统一改为字符串引用；`attendance_models.py` 改为兼容 shim。`makemigrations --check --dry-run` → "No changes detected"，52/52 测试通过。后续阶段路线图见 ARCHITECTURE.md 第 8 节。
- 2026-06-12：仪表盘内容调整（按需求）：移除 Reorder soon（补货建议）与 Operator insights（策略洞察）两个面板及其后端计算/常量/测试；Today operations 新增"当日支付方式统计"（按 cash/card/mbway 汇总当日销售额/件数/占比）；新增独立"年度销售趋势"页 `/sales-trend/`（`yearly_sales_view` + `build_yearly_sales_overview`/`resolve_year`），含 12 月柱状图、逐月明细表、年度合计、支付占比与年份导航，入口在仪表盘 Sales trend 卡片按钮 + 侧边栏。新增 2 个测试，58/58 通过。
- 2026-07-04：**出货/POS 页视觉-工艺重设计**（impeccable `shape`→build，仅视觉层，交互模型/数据契约/校验/弹窗流程完全不变）。目标：从"友好圆角卡片 SaaS"改为"锐利、数据自信的仪器感"。①硬编码色统一到 `app.css` 令牌（`--app-accent*`/`--app-surface-2`/`--app-border*`/语义 `--app-warn/danger/good` 及其 `-soft`），移除装饰性渐变（原 `.review-stat.highlight` 线性渐变→纯 `--app-accent-soft` 底 + 强调边）。②确认按钮由 `btn-danger`（红）改为 `btn-primary`（强调蓝）——红色仅保留给真正的破坏性操作（删行）；红转为语义正确的主操作。③移除 UI chrome 中的 emoji（标题 `🚚`、新增客户 `💾 Save`→纯文本，遵循 no-emoji/childish anti-ref）。④Review 侧栏改为**收据式**摘要（label/value 单行、`tabular-nums`、Total 作为放大强调锚点）；`.operation-card` 圆角 `1rem`→`var(--app-radius)`、字重从铺满 `900` 收敛为真正层级（标题/值 700、标签 600）；购物车 `thead` 改为小号大写弱化的**数据表表头**。⑤新增 `.pos-title`（`--font-display` 衬线，与其它页标题一致）。同步改 1 个测试断言（按钮文案 "Review and Confirm Sale"→"Review and confirm sale"），86/86 通过、check 通过、无待生成迁移。**未在浏览器验证**（无 GUI）。同时经 impeccable `init` 产出根目录 `PRODUCT.md`（register=product、用户/目的/品牌人格=sharp·data-confident、anti-ref、5 条设计原则、WCAG AA）。文档同步 ARCHITECTURE §6.0。
- 2026-07-04：**出货页 critique 跟进修复**（clarify + quieter + polish 一次性）。①clarify：Review 侧栏 "Guardrails"（开发术语）→ "Before you confirm"（面向收银员的自然语言），同步测试断言。②quieter：移除 "Step 1/2/3" 数字步骤 kicker 徽标（三张卡标题已自述 Customer/Cart/Review，且步骤非强制顺序——可随时选客户，数字是脚手架非信息），删除死 CSS `.operation-kicker`、`.operation-title` 上边距归零。③polish：支付明细行改为 label 上、pills 下堆叠（`.review-stat--stack`），避免三方式拆分在窄侧栏挤压；产品搜索下拉在请求中显示 "Searching…" 即时反馈。**设计决策记录**：行项目弹窗"每件必开弹窗"为**有意为之**（确保每行价格/支付被明确确认），critique 的 P1"快速加入"建议按用户决定不做。86/86 通过、check 通过。**未在浏览器验证**（无 GUI）。
- 2026-07-04：**出货页 audit 跟进修复**（harden + adapt + polish 一次性；技术质量分 15/20→目标提升 a11y 维度）。①harden（a11y）：所有表单标签补 `for`/`id` 关联（客户搜索、行弹窗 数量/价格/折扣、新增客户 NIF/Name/Phone/Email/Notes——此前仅 `#barcode` 正确）；4 个模态 `.btn-close` 补 `aria-label="Close"`；动态区域补 live region（`#scan-info` `role="status" aria-live`、成功/错误 alert `role="alert"`、Guardrail 列表 `aria-live="polite"`）；自动完成项补 `role="option"`（产品下拉 div、客户下拉 button）+ 容器 `role="listbox"`；装饰性支付图标 `.pay-ball-dot` 补 `aria-hidden`；页面主标题 `<h2>`→`<h1>`（`.pos-title` 固定 `font-size:1.75rem` 避免尺寸跳变）。②adapt：购物车 Edit/× 按钮移动端（≤576px）`min-height:44px`（`btn-sm` 原 ~31px 不达触控目标）。③polish：残余硬编码中性色（`#fff`/`#f8fafc`/焦点 `color-mix`+`rgba`/hover）全部换令牌；Review 侧栏行**扁平化**为细线分隔（去掉盒中盒，仅 Total 高亮为强调框，更贴收据观感）。对比度按令牌核算无 P1（muted≈4.9:1）。86/86 通过、check 通过。**未在浏览器验证**（无 GUI；触控目标/朗读顺序需真机确认）。
- 2026-07-04：**全站通用 a11y + 一致性 sweep**（用户选"通用 sweep，不做逐页视觉重设计"）。跨 12 个模板机械化套用 outbound 已验证的安全改动：①**去 chrome emoji**（edit_customer `✏️💾↩️`、ar_new `🧾💾✕＋`、login `🔐`、inbound `📥✅✖`→纯文本/`&times;`）。②**页面主标题 `<h2>`→`<h1>`** 并统一到共享 `.pos-title`（app.css 新增，`font-display` 衬线 1.75rem）：add_product/edit_product/ar_detail/ar_list/inbound/edit_customer/ar_new（login 主标题→`<h1 class="h3">`）。③**模态关闭按钮补 `aria-label="Close"`**（dashboard/edit_product/customer_search/inbound/customer_detail/sale_order_correction_form/daily_summary 共 13 处；base.html 的消息框架早已带 `role=alert`+labeled close）。④**主操作按钮统一蓝**：inbound「Confirm receipt」「Confirm inbound now」`btn-success`→`btn-primary`（红/绿仅留破坏性与语义色）。⑤**表单标签 `for`/`id` 关联 + 图标按钮无障碍名**：ar_new（客户搜索/到期日/备注 + 移除行 `aria-label`）、login（用户名/密码）、edit_product（库存输入）；ar_new 客户下拉补 `role=listbox`/`role=option`；inbound/ar_new 页面级 alert 补 `role=alert`；sale_order_correction_form 支付图标补 `aria-hidden`。86/86 通过、check 通过。**未在浏览器验证**（无 GUI）。**仍待**（如需继续）：其余页面（product_list/customer_*/sales_records/supplier_*/employee_*/store_* 等）主标题的 `h1` 语义化（当前用 `.dash-title/.page-title/.corr-title` 等已样式化标题类）、以及各表单逐输入的 `for`/`id` 全量关联。
- 2026-07-04：**dashboard 细节优化**（quieter + token polish；CSS 已在 app.css，纯 HTML 改动）。①移除 7 处 `.kicker` 眉标（"Business Snapshot / Sales Trend / Inventory & Buying / Strategic Signals / Inventory Pressure / Today Operations / Inventory Risk"）——其中多处与其下方标题重复（"Today Operations"/"Today operations"、"Sales Trend"/"Sales trend" 完全重复），属 AI 脚手架眉标，去除后每节仅留描述性 `<h2>/<h3>` 标题，去脚手架、层级更干净（与 outbound 去 Step 眉标一致）。②内联硬编码色令牌化：walk-in 汇总行 `#f5f9ff`→`var(--app-accent-soft)`、两处模态 `border-radius:1rem`→`var(--app-radius)`。dashboard 本就用 app.css 类（`.dash-card/.snapshot-card/.metric-*` 等），无大块内联样式。86/86 通过、check 通过。**未在浏览器验证**（无 GUI）。
- 2026-07-04：**dashboard 多店铺分析 + Today 订单店铺列**（逻辑功能）。①**店铺营业额对比**：`build_monthly_dashboard_snapshot` 复用已加载的 sales，按 `sale.store` 分桶（`select_related("store")` 避免 N+1）产出 `month_store_breakdown`（逐店：营业额/订单数/件数/客单价/利润 + 占比 + 相对条形，按额降序）；`dashboard_view` 传 `store_is_all`；`dashboard.html` 新增 **Store comparison** 卡（Bootstrap `row/col` + 复用 `.mini-card/.mix-track/.mix-fill`，无新增 CSS），仅当 **All stores 且 ≥2 店** 显示，随月份/分类筛选联动，利润列受 `show_profit` 门控。②**Today 订单店铺列**：`daily_summary_view` 给每单挂 `summary_store`（`select_related("store")`），`daily_summary.html` 的 Today's orders 表在 **All stores** 时新增 Store 列。新增 2 个测试（All stores 仪表盘 store 明细金额/排序/卡片渲染、Today 表 All stores 显示 Store 列），88/88 通过、check 通过。**未在浏览器验证**（无 GUI）。
- 2026-07-04：**订单修正中心 Historical orders 按日期分组 + All stores 店铺列**。`sale_order_correction_center_view` 对当前页订单按 `created_at` 本地日期分组（`order_date_groups`，最新在前，每组挂日期 + 当日订单数/金额小计），并传 `store_is_all` + 每单 `view_store`（`select_related("store")`）；模板改为分组渲染——每组一行 `table-light` 日期头（`Y-m-d (D) · N orders · EUR X`），行内时间简化为 `H:i`；All stores 时新增 Store 列（表头 + 单元格，colspan 随之 6/7）。新增 1 个测试（All stores 分组按日期、组内含两店订单、Store 列与店名渲染），89/89 通过、check 通过。文档同步 PRD F2.9.1。**未在浏览器验证**（无 GUI）。
- 2026-07-04：**修复订单修正拆分支付重载丢失的缺陷**（用户报告：把一行从"只刷卡"改为"刷卡+现金"保存后，重开修正页仍只显示刷卡）。根因：拆分支付**已正确写入** `SaleOrderPayment`（`save_sale_order_correction`），但 `_build_correction_cart` 重建购物车时仅读每行 `Sale.payment_method`（单一主方式），且模板 preload 硬编码 `isSplit:false/paymentSplit:null` → 重载即塌缩为单方式，再次保存会真丢失。修复：`_build_correction_cart` 改为从订单级 `SaleOrderPayment` 池**按行贪心分摊**重建各行支付映射（cash→card→mbway 顺序填满每行折后额；无 payments 记录时回退各行自有方式；池不足时用该行方式兜底），单行订单即完整还原其拆分；`_correction_cart_item` 增加 `is_split`/`payment_split`→输出 `isSplit`/`paymentSplit`；模板 preload 改为读取之。新增 1 个回归测试（单行 card €30+cash €20 创建→重开修正页 `initial_cart[0].isSplit=True`、`paymentSplit={card:30,cash:20}`），90/90 通过、check 通过。文档同步 PRD F2.9.2。**未在浏览器验证**（无 GUI）。
- 2026-07-04：**修复当日支付统计不反映拆分支付**（用户报告：把某单改成刷卡+现金后，Daily Summary 与 Dashboard 的当日"支付方式"统计仍只显示刷卡）。根因：两处当日支付明细都按 `Sale.payment_method`（该行**主方式**）聚合，而行内拆分的权威记录在订单级 `SaleOrderPayment` → 拆分被塌缩为主方式。修复：新增 `_order_tender_amounts(order, subtotal, items)` 助手，从 `SaleOrderPayment` 读订单级拆分（无缩窄时用原额、有分类过滤缩窄时按比例缩放到过滤后小计；无 payments 记录的旧单回退按行方式）。`daily_summary_view`（无分类过滤，直接用订单级拆分——同时修好每单 `summary_pay` 与当日 Payment mix）与 `dashboard_view`（"Payment methods today" 金额改从助手累计，件数仍按主方式）均改用之；两处 orders 查询加 `prefetch_related("payments")` 避免 N+1。新增 1 个回归测试（今日 card €30+cash €20 拆分单 → daily/dashboard 当日支付明细均含 Card 30 + Cash 20），91/91 通过、check 通过。文档同步 PRD F2.2.13/F2.20.3。**未在浏览器验证**（无 GUI）。
- 2026-07-04：**代码清理（/simplify，4 个并行审查代理，行为不变）**。①`_order_tender_amounts` 从 `views.py` 迁到 `services/dashboard.py` 并公开为 `order_tender_amounts`（与月度 `month_payment_breakdown` 同层，两个 view 共用 → 正确 altitude）。②删除 dashboard `dashboard_view` 今日循环的死状态 `view_pay_break`（无模板读取）。③修复 dashboard 今日订单 N+1：`prefetch_related` 补 `items__product__images`（与 daily_summary 一致），今日热路径不缓存。④`_build_correction_cart` 贪心分摊内层微简化（`min()`、去掉每方式仅访问一次时多余的 `.get()` 累加）。**有意跳过**（超出清理范围/会改行为）：抽取 outbound+correction 共享的 tender 解析器（POS 热路径风险）、把 today 支付聚合下沉为 service 级 snapshot（较大重构）、订单级 tender 编辑器替代按行拆分 UI（已知模型/UI 局限）、`sale_order_detail` 收据支付改走同一 helper（会改收据显示行为）、常量提取（价值低且需触及范围外的 POS 解析）。91/91 通过、check 通过。
- 2026-07-11：**Shopify 商品图同步**（连到 Scentory 商店 `66tcd5-su.myshopify.com` / www.scentory.pt）。背景：本地 app 只在 localhost，Shopify 只接受公网图片 URL 或 staged 上传，且无本地→Shopify 图片桥；已验证「staged upload（把本地字节 POST 到 Shopify 预签名 GCS URL，无需认证）→ productCreateMedia 挂图」这条链路可行（已手动为 Khamrah Waha 挂图成功）。产出：①`stock/services/shopify_client.py`（requests 版 Admin API 客户端：`graphql`/`find_product_by_sku`（按 barcode=SKU 匹配）/`stage_and_upload_image`/`attach_image`；配置读环境变量，token 不入库）。②`stock/services/shopify_sync.py::sync_product_image`（找 Shopify 商品→无图或 `overwrite` 时上传本地首图并挂载；返回状态码，预期情况不抛异常）。③`manage.py sync_shopify_images`（**默认 dry-run**，`--apply` 才写；`--brand/--barcode/--in-stock/--limit/--overwrite`；逐条 + 汇总）。④**自动同步**：`ProductImage` post_save 信号，`SHOPIFY_AUTO_SYNC` 开启时在 `transaction.on_commit` 后台推图、异常只记日志不阻断上传（默认关，dev/tests 不外呼）。⑤`settings.py` 加 `SHOPIFY_STORE_DOMAIN/ADMIN_TOKEN/API_VERSION/AUTO_SYNC`（环境变量）；`requirements.txt` 加 `requests==2.32.5`；`docs/SHOPIFY_SYNC.md`（custom app 建 token + scopes read/write_products + 用法）。新增 6 个测试（全 mock，无实网：挂图/已有图跳过/不在 Shopify/dry-run/命令 dry-run/信号默认关），97/97 通过、check 通过。**局限**：仅按 barcode=SKU 匹配（占位假条码不匹配）、只补缺图（`--overwrite` 可替换）、**只同步图片不创建商品**（Shopify 里不存在的新品由自动同步记录并跳过，需先建品；完整建品同步为后续单独命令）。Scentory 现有 ~100+ Lattafa 商品缺图，可用 `sync_shopify_images --brand Lattafa --apply` 批量补齐。
- 2026-07-11：**Shopify 缺品自动创建同步**（在图片同步基础上扩展为整品创建）。用 `productSet`（`synchronous:true`，经 graphql_schema 核对 `ProductSetInput`）**一次调用**创建：干净标题（品牌/系列/名称/规格 title-case + 修复 EDP/ml 等 token）、vendor=品牌、type=分类、tags、描述、**SEO 标题+meta 描述**、单变体（price/sku/barcode/单位成本/`inventoryItem.tracked` + 在门店 location 设当前库存）、以及商品图（staged upload 后放进 `files`）。①`shopify_client.py` 加 `get_location_id`（缓存）+`product_set`。②`shopify_sync.py` 加 `create_product_in_shopify` + 统一 `sync_product(create_missing, overwrite_image, status)`（找到→挂图；未找到且允许→建品），字段构造 `_shopify_title/_shopify_tags/_truncate`。③新命令 `manage.py sync_shopify_products`（**默认 dry-run**，`--apply/--status active|draft/--overwrite-image/--brand/--barcode/--in-stock/--limit`）。④**自动同步**升级：`ProductImage` 信号改调 `sync_product`，`SHOPIFY_AUTO_CREATE` 开启时对 Shopify 里不存在的新品直接建品（`SHOPIFY_NEW_PRODUCT_STATUS` 默认 **DRAFT** 更安全）。⑤settings 加 `SHOPIFY_AUTO_CREATE/NEW_PRODUCT_STATUS`；scopes 补 `write_inventory/read_locations`；docs 更新（含"先 `--barcode … --apply` 测一个再批量"）。新增 6 个测试（建品/禁用不建/已存在则挂图/dry-run/标题清洗/命令 dry-run，全 mock），103/103 通过、check 通过。**局限**：单变体、默认建 DRAFT、按 barcode=SKU 匹配。**未对真实 Shopify 跑过 `--apply` 建品**（仅图片路径已在 Khamrah Waha 验证过）——建议先 `sync_shopify_products --barcode <一个> --apply` 验证一件再批量。
- 2026-06-12：**按 ui-ux-pro-max 技能优化 UI**。技能推荐 "Data-Dense Dashboard / industrial slate + stock green"。曾试库存绿强调色，用户选择**回退为蓝 `#2563eb`**（强调色保持蓝）。**保留交互打磨**（与颜色无关，纯 UX 提升）：`app.css` 中 `.btn-primary`/`.btn-outline-primary`/`.text-primary` 绑定 `--app-accent`（主按钮=强调蓝）、`~160ms` 状态过渡、数据表 `tbody tr:hover` 行高亮、可点元素 `cursor:pointer`、`.btn:active` 下压。86/86 通过、check 通过。文档同步 ARCHITECTURE §6.0。
- 2026-06-12：**目录页（对客）重新设计**为编辑式"香水索引"。放弃原暖奶油+大圆角+厚阴影+渐变球（AI 默认三件套之一）；改为瓷白画布 `#f5f4f1` + 墨黑字 + 单一酒红强调 `#6e2b3e` + `--font-display` 衬线（hero/品牌名/区标题）对比系统 sans（价格/元信息）；产品卡为**统一固定比例图块**（4/5 白底 + 内边距 + `object-fit:contain`，完整显示整瓶不裁剪、留白不铺满；序号加白底小片保证可读）；按 house（品牌）排版索引，细发丝分隔、小圆角、库存为安静圆点而非大徽章；顶部瓷白全出血 + 细边 sticky 搜索条。新增 1 个渲染 smoke 测试，86/86 通过。文档同步 ARCHITECTURE §6.0。
- 2026-06-12：当日汇总订单表——每单加**订单详情弹窗**（Bootstrap modal，逐行商品缩略图/数量/单价/支付/小计/利润 + tfoot 合计 + 拆分支付汇总 + Print receipt），操作按钮改为 **Details**（弹窗）+ **Print**（导航到发票/小票页 `sale_order_detail`）。视图 `daily_summary_view` 为每单挂 `summary_items`（含 `image_url`）。85/85 通过。
- 2026-06-12：新增**当日汇总页**（`/today/`，`daily_summary_view`，侧栏 Dashboard 下方 **Today**）。按活动店铺过滤，含：KPI（销售额/订单数/件数/客单价/利润[超管]/当日入库件数[全店共享]）、支付占比（doughnut + 逐方式金额与占比）、当日订单表（时间/客户/件数/支付[拆分上下堆叠]/金额/利润 + 查看）、Top 产品；支持前后日导航 + 跳转当日"Full records"（`sales_records?start=end=day`）+ 打印（`@media print` 隐藏侧栏/操作）。模板复用 `app.css` 密集-扁平组件，支付 doughnut 读 `window.CHART_PALETTE`。新增 1 个测试（当日订单按店铺过滤），85/85 通过。
- 2026-06-12：修复出货购物车表格溢出遮盖 Review & confirm。根因：`.operation-card` 是 `.operation-main` 网格项（默认 `min-width:auto`），过宽的 `nowrap` 购物车表把主列撑宽、盖住 sticky 侧栏。修复：`.operation-main/.operation-side/.operation-card` 加 `min-width:0`（使 `.table-responsive` 内部横向滚动而非撑破布局）、长商品名 `overflow-wrap:anywhere`。并在 `app.css` 加**全局表格溢出保护**：`.table-responsive/.table-wrap{max-width:100%}` + 卡片类（`.dash-card/.page-card/.split-col/.operation-card/.panel/.cardx/.corr-card/.viz-card/.top-card`）`min-width:0`。审计全部表格：均在滚动容器内（`.table-responsive/.table-wrap/.table-shell/.line-table`），仅 `product_detail` 键值表与 `sale_order_detail` 小票表为窄表低风险。84/84 通过。
- 2026-06-12：**CSS 统一 Pass 2**（POS + 图表 + 圆角）。①`inbound.html`/`outbound.html`：删除 `:root{--ds-*}` 本地重定义，`app.css` 补齐 `--ds-*` 全量别名（→canonical，强调色 `#3b82f6→#2563eb`），POS 重阴影（`0 14px 32px`/`0 18px 40px`）→`var(--app-shadow[-md])`。②图表令牌化：`--chart-1..5` 定为易读分类色，`base.html` 头部一次读入 `window.CHART_PALETTE`（带回退），`sales_records`/`customer_detail` 支付 doughnut 改读之。③`1.1rem` 卡片圆角在 8 个后台页对齐 `var(--app-radius)`。84/84 通过、check 通过。仍待：其余圆角字面值、更多图表单序列色、彻底移除内联 `<style>`。文档同步 ARCHITECTURE §6.0。
- 2026-06-12：**CSS 设计系统统一 + 移动端强化（Pass 1）**。根因：`app.css` 是密集-扁平设计系统，但 26/31 模板各自内联 `<style>` 重定义令牌，产生"三种视觉方言"（强调色 5 种 #155eef/#2563eb/#3b82f6/#214b50/#0f766e、圆角 10–22px、阴影扁平 vs `0 18px 40px`、近黑 #0f172a/#10213a）。①`app.css`：规范强调色→`#2563eb`；别名补齐 `--radius*`/`--shadow-md`/`--success*`；新增 `--font-display`（标题衬线）+ `--chart-1..5` + `.num` `tabular-nums` + 全局 `:focus-visible` + `prefers-reduced-motion` + `img/svg max-width` + **移动端**（≤576px：16px 输入防 iOS 缩放、≥44px 触控、宽边距、防横向溢出）。②统一页面：`customer_detail`/`customer_search`/`sales_records`/`ar_detail`/`ar_list`/`product_detail` 删除页内令牌重定义；`supplier_*`/`store_*`/`print_profile_form`/`sale_order_correction_form`/`employee_*` 字面重阴影/近黑对齐到 `var(--app-*)`。③`catalog.html` 有意保留暖色 boutique 身份（面向顾客）。84/84 通过、check 通过。**仍待**：inbound/outbound POS 内联样式、圆角字面值、Chart.js 颜色改读令牌。文档同步 ARCHITECTURE §6.0。
- 2026-06-12：**Sales Trend 合并进 Sales 记录页**（概览→下钻）。`record_view` 无区间时渲染年度趋势（`build_yearly_sales_overview`：12 月柱状图 + 月度明细表 + 支付占比 + 年度合计 + 年份导航），点击柱/月份行下钻到该月区间明细；有区间时为原每日/订单明细。`yearly_sales_view` 改为 302 重定向到 `sales_records`（保留 `sales_trend` URL 名 + 旧链接），删除 `sales_trend.html`，侧栏移除 Sales Trend、History 改名 **Sales**，仪表盘"Full year view"改指 `sales_records`。年度趋势按活动店铺过滤。改/新增测试（默认年度趋势、旧 URL 重定向、店铺过滤指向合并页），84/84 通过。文档同步 PRD 模块 2.8、ARCHITECTURE §6.1。
- 2026-06-12：**打印小票抬头按店铺**。`PrintProfile` 加 `store` OneToOne（迁移 0030），数据迁移 0031 把原单例抬头挂到默认店铺、为其余店铺各建一份（从默认抬头 + 店名播种）。`PrintProfile.get_for_store(store)` 按店铺取/建；小票用订单店铺抬头，抬头设置页编辑当前活动店铺抬头（All stores→默认店铺，页面标注）。`PrintProfileAdmin` 显示 store。新增 2 个测试（每店铺独立抬头、编辑落到活动店铺；小票用订单店铺抬头），83/83 通过。文档同步 PRD F2.19.7、ARCHITECTURE §5.9。
- 2026-06-12：**多店铺 Phase 2——全量按店铺过滤**。仪表盘（`services/dashboard.py` 四个函数加 `store=None`，经 `_apply_store()` 过滤销售+AR，库存/采购保持全店；快照缓存键加 store）、销售趋势、订单修正中心列表、客户详情（订单+AR）、客户列表（统计子查询 + 指定店铺仅显示"本店客户"）、考勤团队区（按 `user__store_profile__store`）均按活动店铺过滤，All stores 显示合计。新增 2 个测试（仪表盘+趋势按店铺、修正中心按店铺），81/81 通过。仍待：`SalesTarget` 改按店铺、员工表单加店铺选择。文档同步 PRD F2.19.6/7、ARCHITECTURE §5.9。
- 2026-06-12：多店铺新增**店铺管理页**（Admin，`/stores/`）：列表（名称/代码/状态/默认/员工数/订单数）+ 新增/编辑（`StoreForm`：name/code/is_active/is_default）+ 删除（仅无销售/发票/员工且非默认时可删）；保存强制恰有一个默认店铺；侧栏 Admin 分组加 **Stores** 入口。新增 3 个测试（创建+列表、设默认取消其它、非管理员被拦截），79/79 通过。
- 2026-06-12：**多店铺（Multi-store）Phase 1**。新增 `Store` + `StoreProfile` 模型（迁移 0028）+ 数据迁移 0029（播种默认店铺 `MAIN`、回填全部历史 `SaleOrder`/`Sale`/`ARInvoice`、为所有已有用户建 `StoreProfile`）。库存/入库/供应商/产品**全店共享**；销售/AR/员工**按店铺**；客户全局共享、按销售派生"本店客户"。`stock/stores.py` 解析活动店铺（会话 `active_store_id`；员工锁定 home store，经理/管理员可切换或 **All stores**，默认 All）；`context_processors.store_context` + settings 注册 + `base.html` 侧栏店铺切换器 + `set_active_store` 端点。落账：出货、订单修正、AR 新建写 `store`。读侧过滤：销售记录、AR 列表、员工列表（按 home store）+ 新建员工自动分配店铺。`Store`/`StoreProfile` 注册进 Django Admin（可创建/管理店铺）。新增 `MultiStoreTests`（5 个：默认店铺播种、出货落到活动店铺、记录按店铺过滤、员工锁定 home store、管理员默认 All 可切换），76/76 通过。**Phase 2 待接入**：仪表盘/销售趋势销售 KPI 按店铺、考勤按店铺、销售目标改按店铺、员工表单加店铺选择、客户列表"仅本店"过滤。文档同步 PRD 模块 2.18 + ARCHITECTURE §5.9。
- 2026-06-12：销售与采购记录页（`record_view`/`sales_records.html`）新增**区间可视化 + 排版优化**。①Daily Money Flow：每日销售柱（`trend_data`），经理叠加采购柱、超管叠加利润折线（Chart.js mixed）；②Payment Mix 环形图（`payment_chart`）取代旧纯文字 Payment Split 卡片；③Top Products 区间畅销榜（`top_products`，销售额排序 + 相对条形）。视图在销售循环内累计 `product_stats`，并按日合并 `trend_data`。排版：移除订单/采购展开区的功能说明 hint 文字（遵循 no-intro-text）、操作按钮右对齐。图表受与金额一致的权限门控。改 1 个旧断言（Payment Split→Payment Mix）+ 新增 1 个可视化上下文测试，71/71 通过。文档同步 PRD F2.8.7、ARCHITECTURE §6.1。
- 2026-06-12：**订单修正中心重写为与 POS 出货一致的购物车逻辑**，并修复删除行不回滚的缺陷。①根因：旧行项目 `formset` 的 `DELETE` 渲染为隐藏字段，JS 用 `.checked` 标记删除对隐藏 input 无效 → "删除"的行仍被保存，库存归还后又被消耗、`Sale` 记录重建（即用户报告的"库存没恢复、销售记录还在"）。②重写：`sale_order_correction_form.html` 改用产品自动补全 + 行项目弹窗（数量/零售批发/每行 € 折扣/每行支付方式或行内拆分），序列化 `items_json`（`product_id`）+ `payments_json`；视图新增 `_parse_correction_cart`/`_build_correction_cart`/`_enrich_correction_items`，移除 `SaleCorrectionLineForm`/`SaleCorrectionLineFormSet`/`ProductChoiceField`（清理死代码）。③服务 `save_sale_order_correction` 现接收 `payment_totals`，编辑时删除旧 `Sale`+旧 `SaleOrderPayment` 并重建二者（与出货同构，`snapshot` 增加 `payments`）。④移除前端功能介绍/操作步骤说明文字（遵循 no-intro-text）。重写 5 个旧测试为新契约 + 新增 1 个"移除行真正回滚库存与销售记录"测试，70/70 通过。文档同步 PRD F2.9.2/AC-2.9.6-7、ARCHITECTURE §5.5。
- 2026-06-12：客户详情页新增**日期范围筛选 + 时间线优化**。页头预设（All time/This month/This year）+ 自定义起止日期（默认整体 all），后端 `customer_detail_view` 按 `preset` 或 `start_date`/`end_date`（`parse_date` + `created_at__date__gte/__lte`）过滤订单，统一作用于 KPI、`spend_trend`/`payment_mix`/`top_products`/`cadence` 与时间线；回传 `range_key`/`range_label`/`range_active` 与有效起止日期。**Order Timeline** 月份块改为可折叠 `<details>`（默认折叠，仅显示月度小计，点击展开）+ 标题旁区间汇总头（区间 · 订单 · 件数 · 金额[敏感]）。AR 概览不随区间变化。新增 1 个测试（自定义区间仅统计区间内订单），69/69 通过。文档同步 PRD F2.6.3.0 + AC-2.6.8。
- 2026-06-12：客户页可视化与展示增强。**客户详情**（`customer_detail_view`/`customer_detail.html`）新增：①月度消费柱状图 + 支付方式占比环形图（Chart.js CDN，数据 `spend_trend`/`payment_mix` 经 `json_script` 注入，仅销售敏感角色）；②Top Products 区块（按消费额/件数聚合，缩略图 + 相对条形，链接产品详情）；③采购节奏 KPI（Customer Since / Avg Days Between Orders / Orders per Month）；并顺带修复订单行项目缩略图（此前 `item.image_url` 未在视图设置，一直显示 No image）。**客户列表**（`customer_search_view`/`customer_search.html`）新增活跃度环形图（`activity_breakdown`，数据源自既有汇总计数，无逐行查询）。新增 1 个测试（详情页可视化上下文 + 员工不泄露图表数据），68/68 通过。文档同步 PRD F2.6.2.1/2.6.3 + AC-2.6.6/2.6.7、ARCHITECTURE §6.1 图表说明。
- 2026-06-12：收银支付再增**行内拆分**——行项目弹窗的支付区新增 **Split this line**：单一方式（3 圆球）或在该行内按金额拆分多方式（3 金额输入 + Remaining/Balanced 校验，之和须等于该行折后小计）。购物车 Pay 列对拆分行显示各方式金额；订单 Payments 仍为各行支付**按方式汇总**（`linePaymentMap()`/`linePayments()`）；`items_json.payment` 取该行主方式（`linePrimaryMethod()`），`payments_json` 为全单汇总。`outbound_view` 后端无需改动（行内拆分对应"单行多方式"，由已有 `payments_json` 路径覆盖，`test_outbound_split_payment_records_each_method` 即此场景）。67/67 通过。
- 2026-06-12：收银支付改回**按行选择**——支付方式移入行项目弹窗（3 圆球，每行各选，记忆上次为默认），购物车新增 Pay 列；移除订单级"Split 拆分金额"面板；订单 Payments 明细改为各行小计**按方式汇总**（前端 `linePayments()`）。提交要求每行都已选方式。`outbound_view` 后端无需改动（旧的按行 `payment` 聚合路径即为此逻辑，仍兼容订单级 `payments_json`）。新增 1 个测试（按行不同方式 → 各 `Sale.payment_method` + 按方式汇总 `SaleOrderPayment`），67/67 通过。
- 2026-06-12：供应商列表卡片排版优化——`.supplier-grid` 由 `auto-fit` 改为 `auto-fill`（`minmax(260px,1fr)`），避免单个卡片被拉伸占满整行；删除按钮从列表卡片移到编辑页（`supplier_form.html` 编辑态底部的删除表单，带确认）。
- 2026-06-12：供应商列表**按国家分组**（未填国家组排最后）+ 国家下拉筛选；**移除"Missing Supplier Links"散单归集功能**（早期未关联供应商时的临时工具，已无需）——删除供应商详情页的归集区块、统计项与 `link_inbound_order`/`link_direct_group` POST 处理及未关联记录的查询（约 240 行），清理因此不再使用的 `Http404` import。新增 1 个测试，66/66 通过。
- 2026-06-12：供应商管理增强（4 项）：①供应商详情页新增"记分卡"（终身/本年/本月采购额、平均订单金额、订单数与月均频率、Top 5 产品、供应 SKU/件数，全部 DB 聚合）；②**平均交货周期**（下单 `created_at`→收货 `received_at`，复用进货暂定→收货流程的时间戳）；③供应商档案新增联系人/邮箱/网站/税号字段（迁移 `0027`），详情页联系区含 `wa.me` WhatsApp/mailto/网站链接；④产品详情页新增"Suppliers & cost"按供应商比价（最近成本 + min/avg/max + 最低价标记）。供应商详情历史改为分页（每页 20）。新增 `digits` 模板过滤器。新增 3 个测试，65/65 通过。
- 2026-06-12：清理技术债。删除死代码 `apply_fifo` + `_legacy_dashboard_view`/`_legacy_record_view`/`_legacy_customer_detail_view`（共 469 行，确认无引用）；删除一次性脚本 `addsale_before.py` 及其 `.pyc`；删除根目录临时文件 `tmp_product_export.xlsx`/`tmp_seg.txt`；清理 `views.py`/`admin.py` 未使用 import（ARItemForm/ARItemFormSet/ARPayment/ProductSeries/restore_stock_fifo/StockConflictError、mark_safe/Sum/Decimal）；`requirements.txt` 移除未使用的 `pandas`/`numpy`/`matplotlib` 及其专属传递依赖（contourpy/cycler/fonttools/kiwisolver/pyparsing）。`manage.py check` 无问题，62/62 通过，无新迁移。
- 2026-06-12：全站移除"功能介绍"说明文字——删除各页面/分区标题下的描述性段落（`dash-sub`/`page-sub`/`operation-copy`/`hero-copy`/`catalog-lead`/`section-copy` 及 section-head 内的裸 `<p>` 描述，约 50 处）。保留标题、字段标签、数据、告警与空状态提示。约定：后续新增功能也不在前端加这类介绍文字。62/62 通过。
- 2026-06-12：统一精简全站提示消息（`messages.*` / 内联 success-error / JS toast / admin / 登录页）——去除"lines/order lines/purchase line/across N products/not grouped/Historical/Pending inbound order"等冗词与装饰性 emoji（✅❌🕒💶⚠️），统一"Please fix the highlighted fields."等表述，只保留必要信息（动作、ID、名称、金额）。仅 `must add up`（被测试断言）保留。62/62 通过。
- 2026-06-12：进货确认收货改为**弹窗**（每张暂定单一个 modal，在 Inbound 页面内复核/编辑/确认收货/取消），删除独立的 `inbound_receive.html` 全页；`inbound_receive_view` 改为仅处理 POST（GET 重定向回 Inbound）。**修复**：确认收货页面产品图片占满全屏——`.cart-product*` 缩略图类此前只存在于 inbound/outbound 的内联样式中，已下沉到 `app.css` 成为共享组件（48–56px 统一缩略图）。
- 2026-06-12：进货（Inbound）暂定→确认收货流程。有供应商的入库改为创建"暂定订单"（`InboundOrder.status='pending_receipt'` + `InboundPendingItem`，**不入库、不产生库存**，`invoice_date` 默认当天）；货到后在 Inbound 页面的待收货列表打开 `inbound_receive_view`（`/inbound/<id>/receive/`）复核/编辑（供应商模糊搜索、发票、行数量/进价、删行），确认收货才把 pending 行转 `Purchase`（产生库存）、置 `received`/`received_at`，也可 Save（保持 pending）或 Cancel（删除）。供应商下拉框改为模糊搜索（新增 `suppliers_autocomplete` API）。无供应商入库仍即时。复用既有 `InboundPendingItem` 模型（无新迁移）。新增 2 个测试（暂定不入库、确认收货生成 Purchase），62/62 通过。**修复**：重构时 `@login_required` 误装饰到辅助函数导致 `inbound_view` 一度无鉴权 + 辅助函数报错，已更正。
- 2026-06-12：收银（Outbound POS）重构。新增 `SaleOrderPayment` 模型（迁移 `0026`，订单级拆分支付，Admin 可见）。收银流程改为：选品后弹出行项目弹窗（数量/零售批发切换/固定额折扣，折扣计入折后价），同款不合并、每行可重编辑/删除；支付改为订单级 3 圆球单选或 Split 按金额拆分（前后端均校验总额一致）；复核区展示折扣合计与 Payments 明细/汇总。`outbound_view` 接收 `items_json`+`payments_json`（兼容旧的按行 `payment` 负载），单一事务内 FIFO 扣减 + 写 `Sale`（主支付方式）+ `SaleOrderPayment`。修复收银页 CSS 排版（文字显示不全）。新增 2 个测试（拆分支付记录、金额不符拒绝），60/60 通过。
- 2026-06-12：整体布局改为经典 ERP 左侧固定侧边栏（`.erp-sidebar` 深色 248px + `.erp-content` 右侧内容区），替代顶部水平导航；移动端为离屏抽屉（汉堡 + 遮罩，纯 CSS + 一行内联 JS）。原下拉菜单展平为侧边栏分组（Dashboard/Operations/Catalog/Sales & Clients/Admin），角色门控不变。仪表盘网格断点相应上移以适配侧栏宽度。57/57 测试通过。
- 2026-06-12：引入共享设计系统 `stock/static/css/app.css`（密集型 ERP 风格：单一 `:root` 设计令牌 + 共享组件层 + Bootstrap 基元微调），收敛此前 27 个模板各自内联、8 套漂移 `:root` 的局面。已迁移 `base.html`（导航/全局 chrome/通用工具类移入 app.css）、`dashboard.html`、`product_list.html`（删除其内联 `<style>`，标记不变）。`findstatic` 确认 app.css 可解析，57/57 测试通过。待迁移：`inbound`/`outbound`/`sales_records` + 其余约 18 页（增量）。详见 ARCHITECTURE.md §6.0。
- 2026-06-12：仪表盘功能增强（4 项）：①月环比同期对比（MoM）KPI 徽标；②按分类的月度销售目标 `SalesTarget`（迁移 `0025_salestarget`，Admin 可编辑）+ 达成进度/月末预测控件；③补货建议（45 天滚动销量 → days-of-cover，仅经理可见）；④性能 cheap wins（AR 聚合改 DB `aggregate`、低库存列表 `prefetch_related('images')` 消除 N+1、月度快照 + MoM 对比 60s `LocMemCache` 缓存）。新增 `DashboardEnhancementTests`（5 例），57/57 测试通过。

---

## 8. 建议的下一步（按优先级）

1. **提交 `docs/` 到 git**，使文档纳入版本历史。
2. **数据备份**：为 `db.sqlite3` 与 `media/` 建立定期备份（脚本 + 任务计划/cron）。
3. **`SECRET_KEY` / `DEBUG` 收敛**：迁移到环境变量（`.env` + `python-dotenv` 或 `os.environ`），确认 GitHub 仓库可见性；若已 public，需立即更换泄露的 `SECRET_KEY`。
4. **统一运行环境**：修复或移除 `.venv`，在 README 中写明实际使用的 Python 环境与依赖安装方式。
5. ~~清理技术债~~ ✅ 已完成（见近期变更记录）。
