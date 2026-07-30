# KHAN PERFUME 库存管理系统 — 产品需求文档（PRD）

> 本文档基于现有代码库（Django 5.2.4 + SQLite）逆向整理，描述系统当前已实现的功能范围、业务规则与验收标准，作为后续维护、测试与迭代的基线参考。
>
> - 版本：v1.0（基线版本，对应 git 初始提交）
> - 适用范围：`stock` 应用全部功能模块
> - 文档结构：第一部分为产品需求（PRD），第二部分为对应的验收标准（AC）

---

## 1. 产品概述

### 1.1 产品定位
面向小型零售门店（KHAN PERFUME，葡萄牙阿玛多拉香水零售店）的库存 + 收银（POS）+ 客户关系一体化管理系统。核心解决：

- 进货入库、库存批次（FIFO 成本）管理
- 销售收银（多商品购物车、多种支付方式）
- 客户、供应商档案与往来记录
- 应收账款（赊账/AR）跟踪
- 经营仪表盘（销售额、利润、库存预警、环比对比、年度销售趋势）
- 员工账号与考勤管理
- 历史订单修正与审计留痕
- 产品目录（对客展示）、报表导出（Excel/CSV/PDF）、小票打印

### 1.2 技术基线
- 后端：Django 5.2.4，数据库 SQLite（`db.sqlite3`）
- 前端：Django 模板 + Bootstrap，`django-widget-tweaks`
- 报表：openpyxl（Excel）、reportlab（PDF）、qrcode（二维码）、csv（Shopify 导出）
- 外部集成：Shopify Admin GraphQL（`requests`）、Cloudinary 图床（`cloudinary`），均由环境变量门控、默认关闭
- 时区：`Europe/Lisbon`
- 认证：Django 内建用户体系，登录页 `/login/`，登录后默认跳转 `/dashboard/`

### 1.3 角色与权限模型

| 角色 | 判定条件 | 典型权限 |
|---|---|---|
| **Employee（员工）** | 已登录的普通用户（非 `is_staff`/非 "Managers" 组/非超管） | 出货（POS）、查看/搜索/新增/编辑产品 + 下载客户产品 Excel（均不含成本等敏感数据）、当日销售查看+订单号查对账、客户搜索+新增+订单对账（每单 View 弹窗 / Print）、登录/登出自动打卡 |
| **Manager（经理）** | `is_staff=True` 或属于 "Managers" 组，或 `is_superuser` | 员工权限 + 查看成本/利润等敏感数据、进货、删除产品/图片、Shopify 导出、供应商管理、AR 管理、完整销售/客户分析页、团队考勤汇总、库存调整 API |
| **Admin（超级管理员）** | `is_superuser=True` | 经理权限 + 员工账号管理、历史订单修正中心、强制删除产品（含历史记录） |

权限判定集中在 `stock/permissions.py`：
- `has_manager_access()` — 经理及以上
- `has_sales_sensitive_access()` — 等价于经理及以上（销售敏感数据，如成本/利润）
- `has_order_reconciliation_access()` — 任意已登录用户（订单金额对账）
- `has_admin_access()` — 仅超级管理员

#### 1.3.1 员工界面收窄（Employee interface restriction）
员工登录后可见/可用的功能被收窄为一个精简子集，而非"完整页面隐藏字段"：

- **可见页面（仅这 5 个）**：Dashboard、Outbound（POS）、Products、Sales、Customers。侧边栏隐藏 Today（daily_summary）、Inbound、Attendance、Catalog View、Suppliers、IOU/AR、Admin 分组；对应视图也用 `@manager_required` 在视图层拦截（员工直接访问 URL 会被 302 到 dashboard），不仅仅是导航隐藏。
- **Products 对员工开放查看/新增/编辑/导出**：员工可搜索浏览列表、看详情、新增并**编辑**产品（改字段 + 加图），并下载**面向顾客的产品 Excel**（图片/产品/分类/零售价/批发价/库存，无成本）。**仍仅经理**：进货（Inbound）、删除产品/图片、Shopify 导出。成本、"Suppliers & cost" 比价、批次成本、销售历史/指标继续对员工隐藏，仅展示零售/批发价。无审批/待确认工作流。
- **Sales（销售，员工视图）**：单日订单列表（默认今天，可切换日期），展示时间/客户/件数/支付方式/金额，无图表、无年度总览、无采购数据，按登录用户所在店铺范围。新增**按订单号搜索**：输入订单号直接跳到该订单详情（`sale_order_detail`，与经理共用同一详情/小票页，员工只能开本店订单）。每个订单行提供 **View**（弹窗内联显示该单商品明细/合计，不含利润成本）+ **Print**（链到订单详情）两个按钮，供快速对账。经理登录仍看到完整的 `/sales-records/` 页面（含图表、年度趋势、采购对比）。
- **Customers（客户，员工视图）**：无查询词时不展示任何客户列表（避免整表暴露）；输入姓名/电话/邮箱/NIF 关键字后仅展示匹配客户的姓名/电话/邮箱（不含消费统计/分析）。保留"新增客户"（沿用既有 AJAX 新增接口，弹窗内完成，无需离开搜索页）。点击某客户进入的详情页仅展示**该客户的订单列表**（日期/订单号/件数/支付方式/金额，每行 **View 弹窗 + Print 按钮**）用于对账，不含消费分析图表、AR 余额、时间线。经理仍看到完整的客户详情页（图表/AR/时间线等）。员工的 Sales/Customers 页样式与经理页一致（共享设计骨架）。
- **自动考勤（Auto-attendance）**：员工不再需要手动打卡——登录时自动开一条考勤记录（若当天已有未下班记录则复用；若存在前一天未关闭的记录会先自动补下班），登出时自动关闭当前班次。经理/管理员账号登录登出不产生考勤记录。团队考勤汇总页（`/attendance/`）仅经理及以上可见。

---

## 2. 功能模块详述

### 模块 2.1 — 登录与导航

- **F2.1.1** 用户必须登录才能访问除登录页外的所有页面（`@login_required`）。
- **F2.1.2** 登录成功后默认跳转到 `/dashboard/`；未登录访问受保护页面跳转到 `/login/`。
- **F2.1.3** 导航栏依据角色动态显示菜单项（如团队管理仅 Admin 可见，AR/供应商管理仅 Manager 及以上可见）。

---

### 模块 2.2 — 经营仪表盘（Dashboard）

入口：`/dashboard/`，核心实现 `services/dashboard.py::build_monthly_dashboard_snapshot`

- **F2.2.1** 默认展示当前自然月（或用户通过月份选择器导航的月份）的经营快照，包含：
  - 月度累计销售额、毛利润、采购支出、AR 应收余额
  - 当日销售额、当日订单数、当日采购笔数
  - 每日销售趋势图表数据
- **F2.2.2** 支持按月切换查看历史快照（`resolve_dashboard_month`），不影响当月实时数据写入。
- **F2.2.3** Top 榜单：本月 Top 客户、Top 产品（按销量/销售额）、Top 供应商（按采购额）。
- **F2.2.4** 滞销品（slow movers）识别：长期无销售记录但有库存的产品列表。
- **F2.2.5** 资金占用（capital-locked）分析：按当前 FIFO 成本估算库存占用资金的产品排行。
- **F2.2.6** 低库存预警：按品牌分组展示库存数量 < 5 的产品。
- **F2.2.8** 默认按分类筛选（默认分类为 "Perfumes"），用户可切换分类查看对应仪表盘数据。
- **F2.2.9** 页面内嵌二维码生成（用于快速分享/打印某个链接，如目录页）。
- **F2.2.10** 敏感财务数据（毛利润、成本类指标）仅对经理及以上角色展示；员工视图隐藏对应区块/数值。
- **F2.2.11 环比对比（MoM）**：销售额、毛利润（仅 `show_profit`）、平均客单价等头部 KPI 旁展示与上月**同期窗口**（上月第 1 天至同样天数）的增减幅徽标（▲/▼ 百分比）。同期窗口保证"本月至今 13 天 vs 上月前 13 天"的公平对比，而非拿"本月至今"与"上月整月"比较。
- **F2.2.12 月度销售目标与达成预测**：按分类配置的 `SalesTarget`（见 F2.5.4.3）驱动；仪表盘汇总当前筛选分类（未筛选时为全部已配置分类）的目标额，展示进度条（已达成 %）与基于当前日均跑率的月末预测额/预测达成率（仅当月有效）。无正向目标时不展示该控件。
- **F2.2.13 当日支付方式统计（Payment methods today）**：在"Today operations"区块按支付方式（cash/card/mbway 等）汇总当日销售额、件数与占当日销售比例，所有登录用户可见（与"当日销售额"同口径）。**支付金额从订单级 `SaleOrderPayment`（`_order_tender_amounts`）聚合**，以正确反映行内拆分支付（如刷卡+现金）；有分类过滤时按过滤后小计比例缩放，无 `SaleOrderPayment` 的旧单回退按行 `Sale.payment_method`。件数仍按行主方式归属（拆分无法拆件数）。
- **F2.2.14 年度销售趋势页（`/sales-trend/`）**：独立页面，从仪表盘"Sales trend"卡片的"Full year view"按钮或侧边栏"Sales Trend"进入。展示某年的 12 个月柱状图（点击柱跳到该月销售历史）、逐月明细表（订单数/件数/销售额/利润*/平均客单价，利润仅超管 `show_profit`）、年度合计与支付方式占比；支持上一年/本年/下一年导航。利润对全年销售做一次 FIFO 重放计算。
- **F2.2.15 店铺营业额对比（仅 All stores）**：当活动店铺为 **All stores**（跨店聚合）且在营店铺 ≥2 家时，仪表盘经营快照区展示 **Store comparison** 卡片——按当前月份与分类筛选，逐店展示营业额、订单数、件数、平均客单价、占全店销售比例与相对条形（利润列仅超管 `show_profit`），按营业额降序。数据由 `build_monthly_dashboard_snapshot` 对已加载销售按 `Sale.store` 分桶产出（`month_store_breakdown`，`select_related("store")` 避免 N+1）；选中单一店铺时该卡不显示。仅经理及以上可见。
- **F2.2.16 销售记录月历（经理页默认视图）**：`/sales-records/` 无日期参数时**默认展示本月销售日历**（`calendar_mode`），而非之前的年度趋势。日历为月网格（周一起始），只有当天有销售的格子显示**订单数 + 当日销售额**（金额仅 `show_order_financials`）并可点击，弹出**当日销售详情弹窗**（复用共享片段 `_sales_order_entry.html`，逐单显示客户/**对应店铺**/时间/商品明细/支付/合计/利润*，含 Print 到订单详情）。顶部工具条支持 ◀/▶ 切月、"This month"、"Year view"（`?view=year` 进入原年度趋势），以及**按产品名/条码过滤整页**（`product_q`：日历/KPI/图表/Top 均只算该产品；带清除 chip）。月度 KPI、每日资金流/支付环形/Top 产品图表沿用现有实现，按选定月份口径。显式 `start_date`/`end_date` 区间仍走原有堆叠列表（`{% if not calendar_mode %}`）；年度趋势的月柱点击改为进入该月日历（`?month=YYYY-MM`）。仅经理及以上；员工页不变。

---

### 模块 2.3 — 进货管理（Inbound）

入口：`/inbound/`，核心实现 `views.py::inbound_view`

- **F2.3.1** 提供购物车式录入界面：扫码/搜索添加产品，逐行填写数量、进价（cost_price）。**供应商使用模糊搜索**（`suppliers_autocomplete` API + 文本框联想）替代下拉框；**Invoice Date 默认为当天**，可改选。
- **F2.3.2 两种入库路径**：
  - **有供应商 → 暂定订单（pending）**：因为只是向供应商下单、收到发票，货物运输需要时间。提交后创建 `InboundOrder(status='pending_receipt')`，行项目存为 **`InboundPendingItem`**（**不创建 `Purchase`、不产生库存**），`invoice_date` 缺省取当天。
  - **无供应商 → 即时入库**：仍为每个商品直接生成"散单"`Purchase`（`inbound_order=None`，`supplier=None`，`remaining=quantity`），立即产生库存。
- **F2.3.3 待收货列表（展示在 Inbound 页面）**：Inbound 页面顶部列出所有 `pending_receipt` 订单（供应商/发票/行数/总额/创建时间 + "Review & Receive" 入口）。
- **F2.3.4 复核 / 编辑 / 确认收货 / 取消（弹窗）**：在 Inbound 页面点击某暂定单的"Review & Receive"打开**该订单的弹窗**（每张暂定单一个 modal，含小尺寸产品缩略图，不再跳转独立页面），**再次确认所有信息**，可改供应商（模糊搜索）/发票号/发票日期/备注、可改各行数量与进价、可删行；表单提交到 `inbound_receive_view`（`/inbound/<id>/receive/`，仅处理 POST：receive/save/cancel；GET 重定向回 Inbound）。
  - **Confirm receipt（确认收货）**：把每条 `InboundPendingItem` 转为 `Purchase`（`remaining=quantity`，`date=now`），订单置为 `received` 并记录 `received_at`，删除已转换的 pending 行 → **此时才正式入库产生库存**。
  - **Save changes**：仅保存编辑、保持 pending。**Cancel order**：删除该暂定单（货未到/订单取消）。
  - 删除全部行项目时自动移除空订单。
- **F2.3.5** **幂等性保护**：对提交内容（含商品明细、供应商、发票号等）计算 SHA256 摘要，使用 `cache.add()` 设置 8 秒幂等窗口；窗口内的重复提交会被识别并拒绝，避免重复创建订单。
- **F2.3.6** `InboundOrder.total_amount` 在创建暂定单与确认收货/编辑时均按 `Σ(quantity × cost_price)` 自动回写。
- **F2.3.7** 入库操作要求登录（任意角色均可执行，无角色限制 — 即员工可录入进货与确认收货）。

---

### 模块 2.4 — 销售/收银（Outbound / POS）

入口：`/outbound/`，核心实现 `views.py::outbound_view`

- **F2.4.1** 购物车式收银：扫码/搜索定位商品后**弹出行项目弹窗**设置数量、价格与折扣，再加入购物车。
- **F2.4.1.1 行项目弹窗**：选中商品后弹窗内可设置——数量；价格来源在**零售价/批发价**间切换（零售为默认；无批发价时该项禁用），并支持手动微调；**每行固定额折扣（€ off，按单位）**；**每行支付方式**——默认 3 个圆球 Cash/Card/MBWay 单选（记忆上次选择作为新行默认），或勾选 **Split this line** 在该行内按金额拆分多方式（各方式金额之和须等于该行折后小计，弹窗显示 Remaining/Balanced 校验）。弹窗实时显示折后单价与本行小计。
- **F2.4.1.2 单独成行、可重编辑**：每次加入都是**独立一行**（同款商品不合并），购物车中每行可点击 **Edit** 重新打开该弹窗修改、或删除；购物车每行展示其支付方式（拆分行显示各方式金额）。
- **F2.4.1.3 折扣处理**：折扣**直接计入最终单价**（`Sale.unit_price` 存折后价，不单独建字段），不影响既有利润/报表口径；购物车与复核区展示**折扣合计金额**。
- **F2.4.2** 提交内容：`items_json = [{barcode, qty, price(折后单价), discount, payment}]` + `payments_json = [{method, amount}]`（按行支付方式汇总），可选关联客户。
- **F2.4.2.1 按行支付（可行内拆分）、按方式汇总**：每行可单一方式或在行内拆分多方式金额；订单的 **Payments** 明细为各行支付**按方式汇总**（如 Cash €30 + Card €20），复核区与最终复核弹窗展示该明细。提交前要求**每行支付完整**（单一方式已选，或拆分金额之和等于该行小计）且购物车非空。`items_json` 的 `payment` 取该行**主方式**（金额最大者），`payments_json` 为全单按方式汇总。`outbound_view` 仍支持订单级 `payments_json`（向后兼容）：有 `payments_json` 时按其汇总并校验总额一致；缺省时按行内 `payment` 聚合。
- **F2.4.3** **库存校验**：提交前（前端按"同条码合计数量 ≤ 库存"校验）与提交时（FIFO 扣减失败即整单回滚）双重保证不超卖。
- **F2.4.4** **FIFO 扣减**：按批次创建时间顺序（`date` 升序）依次扣减 `Purchase.remaining`，直到满足销售数量；用于后续成本/利润核算。
- **F2.4.5** 创建一个 `SaleOrder`（订单头）+ 若干 `Sale`（行项目；`payment_method` = **该行支付方式**，拆分行取主方式=金额最大者）+ 若干 `SaleOrderPayment`（按方式汇总的支付记录，见 2.4.9）。
- **F2.4.6** **幂等性保护**：对提交内容（含 `payments_json`）计算 SHA256 摘要 + `cache.add()` 8 秒窗口，防止重复提交生成重复订单。
- **F2.4.7** 销售记录创建/更新/删除会触发 `DailySalesSummary` 异步重算（通过 Django signal + `transaction.on_commit`，见模块 2.10）。
- **F2.4.8** 出货操作要求登录（任意角色均可执行）。
- **F2.4.9 `SaleOrderPayment`（按方式汇总的支付记录）**：`order`（FK→SaleOrder，related_name `payments`）、`method`（cash/card/mbway）、`amount`、`created_at`。记录订单按支付方式汇总的金额（一笔订单可有多行）。当前各行支付方式即 `Sale.payment_method`，故仪表盘/年度的分类口径"支付占比"按 `Sale.payment_method` 聚合即与之一致。

---

### 模块 2.5 — 产品管理

#### 2.5.1 产品列表
入口：`/products/`，核心实现 `views.py::product_list_view` + `get_filtered_product_list_state`

- **F2.5.1.1** 支持按关键字（名称/型号/品牌/规格/颜色/条码）、品牌、分类等条件筛选。
- **F2.5.1.2** 支持多字段排序（如库存量、价格、品牌等）。
- **F2.5.1.3** 分页展示，每页 10 条。
- **F2.5.1.4** 列表项展示当前库存合计（`total_stock()`）、FIFO 成本价、零售价、批发价等；**成本/利润相关字段仅经理及以上可见**，员工视图中对应列隐藏。
- **F2.5.1.5** 列表页提供导出入口：导出 Excel（`export_product_list_excel`）、导出 Shopify 库存 CSV（`export_shopify_inventory_csv`）。

#### 2.5.2 产品新增/编辑/详情/删除
入口：`/add-product/`、`/products/<pk>/`、`/products/<pk>/edit/`、`/products/<pk>/delete/`

- **F2.5.2.1** 新增/编辑产品（仅经理及以上）：表单字段包含分类、品牌（含级联"新增品牌"内联创建）、系列（级联"新增系列"内联创建）、名称、规格、颜色、条码（唯一）、描述、默认售价、批发价。
- **F2.5.2.2** 保存时自动规整：若选择了系列，则品牌自动与系列所属品牌同步、`model` 字段同步为系列名称；字符串字段自动 trim。
- **F2.5.2.3** 支持产品多图上传（`ProductImage`），编辑页兼容 `images` 与 `images[]` 两种表单字段名；上传后给出成功/警告提示。
- **F2.5.2.4** 产品详情页（任意登录用户可访问）展示：基础信息、当前库存、FIFO 成本价、采购批次历史（含供应商）、销售历史（含客户）。敏感数据按角色显隐。**经理及以上**额外展示"**Suppliers & cost**"区块：按供应商汇总该产品的最近成本、min/avg/max 成本与批次/件数，并在多个供应商有报价时标记**最低价（Cheapest）**供应商，用于比价/选源。
- **F2.5.2.5** **删除产品（仅经理及以上）**：
  - 若该产品**无任何采购记录且无任何销售记录** → 直接删除，同时清理其图片文件。
  - 若该产品**存在采购或销售记录**：
    - 普通经理：拒绝删除，提示存在 N 条采购记录 / M 条销售记录，无法删除。
    - **超级管理员可"强制删除"**（`force_delete=1`）：级联删除产品及其所有采购/销售记录与图片；同时清理因此变为空的 `SaleOrder` 与 `InboundOrder`（自动删除空订单，并重算受影响 `InboundOrder.total_amount`）。
- **F2.5.2.6** 编辑页展示"是否可删除"标志（`can_delete_product`）及"是否可强制删除"标志（`can_force_delete_product`，仅超级管理员为真），供前端控制按钮显隐。
- **F2.5.2.7 香水自动定价（Perfume auto-pricing）**：分类为 **Perfumes** 的产品，其批发价/零售价按当前 FIFO 成本自动计算——`批发价 = ⌈当前 FIFO 成本 + 10⌉`（向上取整）、`零售价 = 批发价 + 12`（例：成本 12.34 → 批发 23、零售 35）。每当该产品"当前正在售卖"的批次成本可能变化时（进货入库、出货/归还切换到下一批次、库存调整 API 改动批次）自动重新计算并写入；只有价格确实变化时才写入（幂等）。产品可勾选 **"Lock price"**（`price_locked`，仅经理及以上表单可见/可改）锁定手动设定的价格，锁定后自动定价对其永久跳过。已存量的香水产品可通过 `sync_perfume_prices` 管理命令批量补算（支持 `--dry-run` 预览）。
- **F2.5.2.8 员工价格只读**：`default_price`（零售价）、`wholesale_price`（批发价）与 `price_locked`（锁价勾选）三个字段对非经理角色（员工）在新增/编辑产品表单中均为**禁用只读**（可见其当前值，但不可修改）——服务端强制（`ProductForm` 对应字段 `disabled=True`，即使构造篡改的 POST 提交也会被表单忽略，不会写入），适用于全部产品，不限于 Perfumes。
- **F2.5.2.10 库存流水台账（Stock Ledger · 完整路径，Layer 1 重建账，仅经理）**：产品详情页展示一条按时间倒序的库存变动流水,把三张表合并为统一台账并带**运行余额**——采购批次（**+quantity**,附成本/供应商/Inbound#/当前 remaining）、销售行（**−quantity**,尊重 `SaleOrder.affects_stock`；补录不影响库存的单标记为"did NOT affect stock"、delta 0）、手动 `StockAdjustmentLog`（**±(new−old)**,附操作人与 old→new）。台账末尾与**实际在库**（Σ `Purchase.remaining`）对账：一致时绿色"Reconciled",不一致时红色横幅给出**无法用已登记事件解释的差额**（`difference = 实际 − 重建`），提示多为批次数量编辑（[views.py inbound/direct edit](stock/views.py) 改 `remaining` 不留日志）或删除等未留痕路径。**双计防护**：`api_adjust_total_stock` 上调库存会既建批次又写 `total_stock` 日志,重建时该日志 delta 记 0（批次已计入）；下调走 FIFO 无批次,日志 delta 照计。纯只读、无迁移（服务 `services/stock_ledger.py`）；"销售消耗了哪个批次"的批次级归属需 forward-only 的 `StockMovement`（Layer 2），历史不可追溯。
- **F2.5.2.9 产品性别分类（gender）**：`Product.gender`（`men`/`women`/`unisex`，可留空），新增/编辑表单下拉可选。Shopify 导出（CSV 与 `productSet` 同步）自动携带对应葡语 tag：Homem / Mulher / Unissexo（`Product.GENDER_SHOPIFY_TAGS`），驱动 Shopify 侧按 tag 的性别智能 collection 自动归类。存量香水已按"标题关键词 > 描述关键词 > 已知产品线 > 默认 unisex"一次性回填（2026-07-23）。

#### 2.5.3 条码查询 / 自动补全 API
- **F2.5.3.1** `check_barcode`：按条码精确查询产品，返回展示名、品牌/型号/规格/颜色、当前库存、最近一次进价、零售价、批发价、首图 URL；用于进货/出货页扫码联想填充。
- **F2.5.3.2** `products_autocomplete`：产品名称模糊搜索补全 API。

#### 2.5.4 库存调整 API
- **F2.5.4.1** `api_adjust_purchase_stock`（经理及以上，`has_manager_access`）：调整单个采购批次的 `remaining` 数量。
  - 使用条件 `UPDATE ... WHERE remaining = <旧值>` 的乐观并发控制：若该批次在读取后已被其他请求修改，返回"Stock changed concurrently, please retry."，不写入任何数据。
  - 每次成功调整写入一条 `StockAdjustmentLog`（`adjustment_type='purchase_remaining'`，记录旧值/新值/操作人）。
- **F2.5.4.2** `api_adjust_total_stock`（经理及以上，`has_manager_access`）：将产品总库存调整为指定数值——
  - 若新值 > 当前总库存：创建一条 `cost_price=0` 的新批次，`remaining = 差值`。
  - 若新值 < 当前总库存：调用 `consume_stock_fifo()` 按 FIFO 顺序（最早批次优先）以条件 `UPDATE` 原子扣减各批次 `remaining`，直到达到目标值；若并发冲突或库存不足，整单失败且不修改任何数据。
  - 若新值 = 当前值：不做任何变更，直接返回当前库存快照。
  - 拒绝负数目标值。
  - 每次成功调整写入一条 `StockAdjustmentLog`（`adjustment_type='total_stock'`，记录旧值/新值/操作人）。
  - 返回 `inventory_snapshot`（最新批次明细）供前端刷新展示。

#### 2.5.5 月度销售目标配置
- **F2.5.4.3** `SalesTarget`（每分类一条，`category` 唯一 + `monthly_amount`）：配置各分类的每月销售目标，供仪表盘 F2.2.12 计算达成进度与预测。经理及以上可在 Django Admin 中编辑（`list_editable` 直接改额度）。目标按月通用（非按具体年月），低维护成本。

---

### 模块 2.6 — 客户管理

入口：`/customers/`、`/customers/<id>/`、`/customers/<id>/edit/`、`/customers/delete/<pk>/`

- **F2.6.1** 客户档案字段：NIF（葡萄牙税号，9 位数字，唯一）、姓名、邮箱、电话、备注。
- **F2.6.2** **客户搜索页**（`customer_search_view`）：支持按姓名/NIF 搜索；列表附带统计子查询——订单数（`order_count`）、累计消费（`spent`）、AR 余额（`balance`），近 60 天活跃判断。敏感统计仅经理及以上可见。
  - **F2.6.2.1 活跃度可视化**：页面顶部展示一张活跃度环形图（Chart.js doughnut，数据 `activity_breakdown`：Active 60 天 / Quiet（有订单但非近活跃）/ No orders），数据来自已有的 `customer_summary` 计数，不产生逐行查询；客户数为 0 时不渲染。
- **F2.6.3** **客户详情页**（`customer_detail_view`）：
  - **F2.6.3.0 日期范围筛选**：页头提供预设（All time=整体 / This month / This year）+ 自定义起止日期；默认 `整体（all）`。该筛选**统一作用于**汇总 KPI、可视化图表、Top Products 与订单时间线（`created_at__date` 落入区间），后端用 `preset` 或 `start_date`/`end_date`（`parse_date`）解析为有效区间，并回传 `range_key`/`range_label`/`range_active` 与有效起止日期用于回填输入与高亮预设。AR 概览不随区间变化（反映当前未结清状态）。
  - 按月/按日分组展示该客户订单历史（受日期区间约束），含每日/每月小计（金额、数量、利润）与支付方式分布。**时间线优化**：①月份块改为可折叠 `<details>`，**默认折叠**（仅显示月度小计），点击展开当月按日/按单明细；②时间线标题旁展示**区间汇总头**（区间标签 · 订单数 · 件数 · 金额[敏感]）。
  - 汇总指标：累计消费、累计商品数、累计订单数、累计利润（仅超级管理员可见）、最大单笔订单金额、平均订单金额、最近下单时间。
  - **采购节奏 KPI**（始终展示）：Customer Since（首单年月）、Avg Days Between Orders（相邻订单平均间隔天数）、Orders / Month（按首末单跨月数估算的月均下单频次）。
  - **可视化区块**（仅经理及以上 / `has_sales_sensitive_access`，且有数据时）：①**Monthly Spend** 月度消费柱状图（Chart.js bar，数据 `spend_trend`，按月升序）；②**Payment Mix** 支付方式占比环形图（Chart.js doughnut，数据 `payment_mix`，按方式汇总金额与百分比）。
  - **Top Products**（始终展示，有数据时）：该客户购买最多的产品列表（按消费额排序，无销售敏感权限时按件数排序），含缩略图、单位数、消费额（敏感）与相对条形，链接到产品详情。
  - **AR 概览**：该客户名下应收发票汇总（应收总额、已付总额、欠款余额）、按状态（未付/部分/已付）分类统计、逾期发票数；仅经理及以上且存在未结清欠款时展示 AR 区块。
- **F2.6.4** **客户编辑/删除**：编辑表单与新建一致；删除前应校验关联数据（如 AR 发票为 `PROTECT` 外键，存在未删除发票时无法删除客户，由数据库约束保证）。
- **F2.6.5** **快捷新建/查询 API**：
  - `check_customer`：按 NIF/姓名模糊查询，用于 POS 页快速识别已存在客户。
  - `add_customer`（POST）：内联新建客户，校验 NIF 必须为 9 位数字、不可与现有客户重复。
  - `customers_autocomplete`：客户姓名/NIF 自动补全（最多 10 条）。

---

### 模块 2.7 — 供应商管理

入口：`/suppliers/`、`/suppliers/new/`、`/suppliers/<id>/`、`/suppliers/<id>/edit/`、`/suppliers/<id>/delete/`（均要求经理及以上）

- **F2.7.1** 供应商档案字段：名称、**联系人**、WhatsApp 电话、**邮箱**、**网站**、**税号/NIF**、国家、地址、供应品类（多对多关联 `Category`）。
- **F2.7.2 供应商列表按国家分组**：列表按国家分组展示（未填国家的组排在最后），并提供"国家"下拉筛选（含"No country"选项）+ 关键字搜索。
- **F2.7.3** **供应商详情页**展示：
  - **联系区**：联系人/电话（带 `wa.me` WhatsApp 一键聊天）/邮箱（mailto）/网站/税号/国家/地址。
  - **记分卡（Scorecard，全周期，不受历史筛选影响）**：终身采购额、本年/本月采购额、平均订单金额、订单数与月均下单频率、**平均交货周期**（下单 `created_at` → 收货 `received_at`，仅统计有 `received_at` 的 received 订单）、供应 SKU 数、终身件数、按采购额排序的 **Top 5 产品**。
  - 已关联的进货订单 + 散单采购历史（按关键字/日期筛选，**分页**，每页 20 条）。
  - （已移除）"Missing Supplier Links"散单归集功能——历史早期未关联供应商时的临时工具，现已无需，不再提供。

---

### 模块 2.8 — 销售（Sales：趋势 + 记录，Records）

入口：`/sales-records/`（侧栏"Sales"），核心实现 `views.py::record_view`（系统中最复杂的视图之一）。**已合并原 Sales Trend 页**（`/sales-trend/` 现 302 重定向到本页，保留旧链接）。

- **F2.8.0 概览→下钻（合并 Sales Trend）**：**未选日期区间**时展示**年度趋势**——12 月销售柱状图（Chart.js，点击柱/月份行下钻到该月区间）、月度明细表（订单/件数/销售额/利润/客单价 + Open 链接）、支付占比、年度合计 + 年份导航（`?year=`）；**选定日期区间**（或点击某月）时切换为下方的每日/订单明细。年度趋势按活动店铺过滤（见 F2.19.6）。
- **F2.8.1** 支持自定义日期范围筛选（默认范围由实现决定，通常为近期）。
- **F2.8.2** 按"日期 → 订单"两级分组展示：
  - 每日汇总：当日销售总额、销售数量、采购总额、（仅超级管理员）利润。
  - 每个订单展示其行项目、支付方式分布、订单总额、订单详情跳转链接。
- **F2.8.3** 同时展示当日的进货记录（含进货订单详情跳转链接到 `inbound_order_edit`）。
- **F2.8.4** **利润数据仅超级管理员可见**（`show_profit` 基于 `is_superuser`），其余角色看不到成本/利润列。
- **F2.8.5** **订单金额对账信息**（`show_order_financials`）：基于 `has_order_reconciliation_access`（任意已登录用户）控制是否展示订单金额汇总。
- **F2.8.6** 提供导出入口：`export_sales_purchases_pdf`（PDF 报表，reportlab 生成）。
- **F2.8.7 区间可视化**（Chart.js，受与金额一致的权限门控；数据由所选日期区间驱动）：
  - **Daily Money Flow**：按活跃日的每日销售柱状图（`trend_data`）；经理及以上额外叠加每日采购（money out）柱，超级管理员额外叠加每日利润折线。
  - **Payment Mix**：销售按支付方式的环形图（`payment_chart`），取代旧的纯文字"Payment Split"。
  - **Top Products**：区间内最畅销产品表（按销售额排序，单位数 + 销售额 +（超管）利润 + 相对条形，链接产品详情；`top_products`）。
  - 三者均在有数据时渲染；`show_order_financials` 为假时不输出含金额的图表数据。

---

### 模块 2.9 — 历史订单修正与审计中心

入口：`/sale-orders/manage/`（仅 Admin）、`/sale-orders/manage/new/`、`/sale-orders/manage/<id>/edit/`，核心实现 `services/order_corrections.py` + `views.py` 的修正相关视图

- **F2.9.1** **修正中心列表**（`sale_order_correction_center_view`，仅超级管理员）：支持搜索历史订单、分页浏览，并展示最近的修改日志（`recent_logs`）。历史订单列表**按日期分组展示**（`order_date_groups`：对当前页订单按 `created_at` 本地日期分组，最新在前，每组头显示日期 + 当日订单数/金额小计；行内仅显示时间 `H:i`）。列表按**活动店铺过滤**，选择 **All stores** 时新增 **Store 列**（每单所属店铺）。
- **F2.9.2** **创建/编辑订单**（`_sale_order_correction_view` 共享逻辑）：
  - **与 POS 出货一致的购物车式编辑**（替代旧的行项目 formset）：产品自动补全搜索 → 行项目弹窗（数量、零售/批发价切换、每行 € 折扣、每行支付方式或行内拆分多方式），加入购物车后序列化为 `items_json=[{product_id, qty, price(折后), discount, payment}]` + `payments_json=[{method, amount}]`（按方式汇总）；编辑态把现有行项目预载入购物车（`initial_cart` 经 `json_script`）。订单头仍为客户、下单时间（可回填历史时间）、备注、修改原因。**拆分支付重载还原**：由于 `Sale.payment_method` 仅存主方式、拆分权威记录在订单级 `SaleOrderPayment`，`_build_correction_cart` 从 `SaleOrderPayment` 池按行贪心分摊重建各行 `isSplit`/`paymentSplit`，使拆分订单（如某行刷卡+现金）重开时完整还原、再次保存不丢失（此前重载会塌缩为单一方式）。
  - 后端 `_parse_correction_cart` 复用 POS 出货同一契约：校验行项目、按方式汇总支付、要求支付总额等于订单折后总额；`save_sale_order_correction` 在单个 `transaction.atomic()` 内重建 `Sale` 行 + `SaleOrderPayment`（订单级支付权威记录，与 POS 出货一致），`Sale.payment_method` 取该行主方式。
  - 保存前对该订单当前状态做快照（`snapshot_sale_order`，含 `payments`），保存后对比生成 `before_data`/`after_data`。
  - **库存联动 / 完整回滚**：编辑保存时先"归还"旧行项目对应的 FIFO 批次库存（`_restore_current_stock`），删除全部旧 `Sale` 与旧 `SaleOrderPayment`，再按新行项目重新"消耗"库存（`_consume_current_stock`）并重建。**因此移除某个产品行会真正回滚：其库存归还、销售记录删除**（修复了旧 formset 隐藏 `DELETE` 字段失效、删除行不生效的缺陷）。
  - 操作记录写入 `SaleOrderChangeLog`（含操作人 `changed_by`、操作类型 create/update/delete、修改原因 `reason`、变更前后 JSON 快照、时间戳）。
- **F2.9.3** **删除订单修正**（`delete_sale_order_correction`）：删除订单前归还其全部行项目对应的库存批次，并记录一条 `action='delete'` 的变更日志。
- **F2.9.4** 所有修正操作仅限超级管理员（Admin）执行（`@admin_required` 或等价校验）。
- **F2.9.5 修正时改店铺**：管理员在订单修正表单可选择本单归属店铺（仅活跃店铺），用于修复下错店铺的订单；保存后订单与其全部明细行改到所选店铺，审计日志记录 旧→新 店铺。库存不受影响，不涉及应收(AR)。
- **F2.9.6 补录订单可不影响库存**：新增历史订单时可取消「Affects inventory」，该单只记销售额/支付、不扣库存（用于货已出、库存已对账的遗漏订单），利润按销售额 50% 估算，且不参与 FIFO 成本；默认勾选＝正常扣库存。

---

### 模块 2.10 — 每日销售汇总（Daily Summary）

核心实现 `services/summaries.py` + `signals.py`，无独立页面入口（供仪表盘/报表/Admin 使用）

- **F2.10.1** `DailySalesSummary` 表按日期（唯一）预计算 `total_sales`、`total_profit`、`total_items_sold`。
- **F2.10.2** `recalc_summary_for_date(date)`：对指定日期做 FIFO 重放（replay）以重新计算该日的销售额/利润/销量。
- **F2.10.3** **自动重算触发**：`Sale` 模型的 `pre_save`/`post_save`/`post_delete` 信号会调度 `schedule_summary_recalc()`，通过 `transaction.on_commit` 在事务提交后异步重算受影响日期的汇总（创建/修改销售记录会同时影响修改前与修改后两个日期）。
- **F2.10.4** `rebuild_all_daily_summaries()`：全量重建所有历史日期的汇总，供数据修复/初始化使用；Django Admin 中 `DailySalesSummaryAdmin` 提供"重建所选汇总"批量操作。

---

### 模块 2.11 — 应收账款（Accounts Receivable, AR）

入口：`/ar/`、`/ar/new/`、`/ar/<id>/`、`/ar/<id>/add-payment/`、`/ar/<id>/add-items/`

- 创建发票（`ar_new_view`）、登记收款（`ar_add_payment_view`）、追加明细（`ar_add_items_view`）要求经理及以上（`@manager_required`）。
- **发票列表/详情页（`ar_list_view`/`ar_detail_view`）仅要求登录（`@login_required`，任意已登录角色可访问）**：页面内全部 € 金额字段均通过模板 `{% if show_sensitive %}` 条件对非经理角色隐藏，不构成敏感数据泄露。该权限设置为代码审查后确认维持现状的设计决策（见 STATUS.md 近期变更记录），非待修复项。

- **F2.11.1** **创建发票**（`ar_new_view`）：选择客户，填写到期日（可选）、备注，从表单提交的明细列表（商品名/数量/单价）创建 `ARInvoice` 及其 `ARItem` 列表；`total_amount` 按各行 `line_total` 之和自动计算；初始状态 `unpaid`。
- **F2.11.2** **发票列表**（`ar_list_view`）：
  - 支持按客户/关键字搜索、按状态（unpaid/partial/paid）筛选、按日期/到期日/金额排序。
  - 列表汇总指标：应收总额、已收总额、未结余额合计。
  - 标记逾期发票（`due_date` 已过且余额 > 0）。
- **F2.11.3** **发票详情**（`ar_detail_view`）：展示发票全部行项目、收款记录（`ARPayment`）历史、当前余额（`balance = total_amount - amount_paid`）。
- **F2.11.4** **登记收款**（`ar_add_payment_view`）：录入一笔 `ARPayment`（金额、支付方式、备注），累加 `amount_paid`；根据 `amount_paid` 与 `total_amount` 的关系自动更新发票状态：
  - `amount_paid == 0` → `unpaid`
  - `0 < amount_paid < total_amount` → `partial`
  - `amount_paid >= total_amount` → `paid`
- **F2.11.5** **追加明细**（`ar_add_items_view`）：向已有发票追加新的 `ARItem` 行项目，重新计算 `total_amount` 并按 F2.11.4 规则刷新状态。

---

### 模块 2.12 — 进货订单 / 散单编辑

入口：`/inbound-records/<order_id>/edit/`（`inbound_order_edit_view`）、`/direct-purchases/<purchase_id>/edit/`（`direct_purchase_edit_view`），均要求经理及以上

- **F2.12.1** **进货订单编辑**：编辑 `InboundOrder`（供应商、发票号、发票日期、备注）及其下属 `Purchase` 行项目（数量、进价）；使用 formset 校验——**不允许将某行数量降低到低于该批次已售出的数量**（即 `quantity - remaining` 已售出部分不可超过新的 `quantity`）。
- **F2.12.2** 保存后按行项目重新计算 `InboundOrder.total_amount`。
- **F2.12.3** **散单编辑**（`DirectPurchaseEditForm`）：编辑单条未关联进货订单的 `Purchase` 记录（供应商、数量、进价等），同样受"不可低于已售数量"约束。

---

### 模块 2.13 — 团队（员工）管理

入口：`/team/`、`/team/new/`、`/team/<id>/edit/`、`/team/<id>/toggle-active/`、`/team/<id>/delete/`，均要求 **Admin**

- **F2.13.1** **员工列表**（`employee_list_view`）：列出所有非超级管理员账号，支持按用户名/姓/名/邮箱搜索；展示角色标签（`is_staff` → "Manager"，否则 "Employee"）、最近一次打卡时间、当前是否正在打卡中（open shift）。
- **F2.13.2** 列表顶部汇总：活跃账号数、经理数、当前打卡中人数。
- **F2.13.3** **创建/编辑员工账号**（`EmployeeAccountForm`）：用户名、姓名、邮箱、角色（employee/manager，决定 `is_staff`）、是否激活、密码（创建时必填，编辑时可选修改）。
- **F2.13.4** **启用/停用账号**（`employee_toggle_active_view`，POST）：切换 `is_active`，不删除账号数据。
- **F2.13.5** **删除员工账号**（`employee_delete_view`，POST）：物理删除用户记录（不可删除超级管理员）。

---

### 模块 2.14 — 考勤管理（Attendance）

入口：`/attendance/`，核心实现 `views.py::attendance_view`

- **F2.14.1** **个人打卡**：当前登录用户可"打卡上班"（创建 `AttendanceRecord`，`clock_in_at=now`）与"打卡下班"（更新当前未结束记录的 `clock_out_at=now`）；同一时刻每个用户最多有一条"未结束"（`clock_out_at=null`）记录。
- **F2.14.2** **个人记录视图**：按月展示当前用户的考勤记录列表，每条记录展示上班/下班时间、工作时长（`worked_duration`，下班前以"当前时间"实时计算）、备注。
- **F2.14.3** 提供 `format_duration_hours` 将时长格式化为"X小时Y分钟"等可读格式；`append_attendance_note` 支持为记录追加备注文本。
- **F2.14.4** **团队视图**（经理及以上）：展示团队所有成员的考勤汇总——每人本月打卡次数（`shift_count`）、累计工时（`total_duration`）、当前是否处于打卡中状态（`open_shift`）。

---

### 模块 2.15 — 产品目录（对客展示，Catalog）

入口：`/catalog/`、`/catalog/export-excel/`，核心实现 `views.py::catalog_view` + `export_catalog_excel`

- **F2.15.1** 按品牌 → 型号（系列）分组展示在售产品，每个产品展示图片、名称、规格/颜色变体、零售价。
- **F2.15.2** **库存可用性徽章**：
  - 库存 ≥ 4 → "Available now"（有货）
  - 0 < 库存 < 4 → "Low stock"（库存紧张）
  - 库存 = 0 → "Currently unavailable"（无货）
- **F2.15.3** **热销榜**：按累计销量 Top 5 的产品单独展示（hot products）。
- **F2.15.4** 该页面面向客户展示，**不暴露成本/进价/利润等敏感信息**，仅展示零售价与库存可用性徽章（非精确数字，按区间分级）。
- **F2.15.5** **导出 Excel**（`export_catalog_excel`，经理及以上）：将当前目录数据导出为 Excel 文件，便于线下分享/打印。

---

### 模块 2.16 — 打印设置 / 小票打印

入口：`/print-profile/`（`print_profile_edit_view`），订单详情页 `/sale-orders/<order_id>/?print=1&layout=a4|pos`

- **F2.16.1** `PrintProfile` 为单例配置（`get_solo()`，固定 `pk=1`），字段：店铺名称、NIF 税号、电话、地址、邮箱、小票页脚备注语；经理及以上可编辑。
- **F2.16.2** **订单详情页打印模式**：
  - `print=1&layout=a4`：A4 纸张排版的订单/收据。
  - `print=1&layout=pos`：小票打印机宽度排版的收据。
  - 打印页展示店铺信息（来自 `PrintProfile`）、订单行项目、合计、支付方式分布。
- **F2.16.3** **金额展示权限**：`show_receipt_amounts` 基于 `has_order_reconciliation_access()`（任意登录用户），即所有登录用户打印的小票上都会显示金额（与"敏感成本数据"不同，金额本身不算敏感）。
- **F2.16.4** 任意已登录用户可打印收据（`can_print_receipt = request.user.is_authenticated`）。

---

### 模块 2.17 — 订单详情页

入口：`/sale-orders/<order_id>/`（`sale_order_detail_view`）

- **F2.17.1** 展示订单全部行项目（商品图片、展示名、数量、单价、小计）、订单总数量、订单总金额、按支付方式的金额分布。
- **F2.17.2** 提供两种打印链接（A4 / POS 小票，见模块 2.16）。
- **F2.17.3** 仅超级管理员可见"管理员修正"入口链接（`can_admin_correct`），跳转至模块 2.9 的修正编辑页。
- **F2.17.4** 敏感数据（成本、利润）按角色显隐，与产品列表/详情一致的权限规则。

---

### 模块 2.18 — 数据导出

| 导出项 | 入口 | 权限 | 格式/技术 |
|---|---|---|---|
| 产品列表导出 | `/products/export-excel/` | 经理及以上 | Excel（openpyxl） |
| Shopify 库存导出 | `/products/export-shopify-csv/` | 经理及以上 | CSV（Shopify 库存更新格式） |
| 销售/进货报表导出 | `/export-sales-purchases-pdf/` | 经理及以上 | PDF（reportlab） |
| 产品目录导出 | `/catalog/export-excel/` | 经理及以上 | Excel（openpyxl） |

- **F2.18.1** 各导出功能均基于当前页面的筛选条件（关键字/日期范围/分类等）生成对应数据集。
- **F2.18.2** 导出文件直接以 HTTP 响应下载，不在服务器持久化存储（不应在仓库中残留临时导出文件，如已发现的 `tmp_product_export.xlsx` 应被清理并加入 `.gitignore`，目前已通过 `tmp_*.xlsx` 规则覆盖）。
- **F2.18.3 Shopify CSV 导出图片用 Cloudinary URL**：Shopify 库存 CSV 的 `Product image URL`/`Variant image URL` 两列输出公开的 Cloudinary 交付 URL（`c_pad,b_white,w_1600,h_1600,q_auto/<条码>.jpg`），供 Shopify 导入时直接抓取，每个产品呈现统一的 1:1 白底方图。此前两列输出的是 `request.build_absolute_uri` 的本机局域网地址，Shopify 服务器无法访问，实为死链接。无图片的产品两列留空；未配置 Cloudinary 时回退到本机绝对 URL。

---

### 模块 2.19 — 多店铺（Multi-store）

- **F2.19.1 共享 vs 按店铺**：**库存（总库存/批次）、入库、供应商、产品档案在所有店铺间共享**；**销售、AR、员工与相关报表按店铺区分**。客户档案全局共享（NIF 全局唯一），"本店客户"由销售归属派生。
- **F2.19.2 数据模型**：`Store`（name/code/is_active/is_default）、`StoreProfile`（用户 home store）。`SaleOrder`/`Sale`/`ARInvoice` 带 `store` 外键。数据迁移播种默认店铺并回填全部历史数据 + 为已有用户建 profile。
- **F2.19.3 活动店铺与权限**：员工**锁定**在其 home store（不可切换）；经理/管理员可在侧栏切换任意在营店铺或 **All stores**（跨店聚合），未选择时默认 All stores。切换写入会话（`set_active_store`，`stores/switch/`）。
- **F2.19.4 落账**：出货（`outbound_view`）、订单修正（`save_sale_order_correction`）、AR 新建按**当前活动店铺**写入 `store`（订单修正在编辑既有订单时保留其原店铺）；All stores 下回退到操作者 home store 或默认店。
- **F2.19.5 读侧过滤**：销售记录页销售侧、AR 列表、员工列表按活动店铺过滤（All stores 显示全部）；新建员工自动分配到当前店铺的 `StoreProfile`。
- **F2.19.5.1 店铺管理页**（Admin，`/stores/`）：列表展示各店铺（名称/代码/启用状态/默认标记/员工数/订单数），支持**新增、编辑**（名称、代码、启用、设为默认）与**删除**（仅当店铺无销售/发票/员工且非默认时可删）。始终保证有且仅有一个默认店铺（设为默认时自动取消其它、无默认时回落到当前店铺）。侧栏 Admin 分组新增 **Stores** 入口。店铺亦可在 Django Admin 管理。
- **F2.19.6 全量按店铺过滤**：以下页面均按活动店铺过滤（All stores 显示合计）——**仪表盘**（销售 KPI、支付占比、当日订单、月度图表、MoM 对比；库存/采购保持全店共享）、**销售趋势**、**订单修正中心**列表、**客户详情**（该客户订单与 AR）、**客户列表**（统计与"本店客户"）、**考勤**团队区。
- **F2.19.7 打印小票抬头按店铺**：每个店铺有独立的打印抬头（`PrintProfile.store`，店名/NIF/电话/地址/邮箱/页脚）。小票按**订单所属店铺**渲染抬头；抬头设置页编辑**当前活动店铺**的抬头（All stores 时编辑默认店铺，页面标注所编辑店铺）。数据迁移把原单例抬头挂到默认店铺，并为其余店铺各建一份（从默认抬头 + 店名播种）。
- **F2.19.8 仍待接入**：销售目标 `SalesTarget` 由按分类改为按店铺（当前仪表盘目标进度用店铺销售额对比全局分类目标，为已知局限）；员工表单增加显式店铺选择（当前新建自动按当前/默认店铺分配）。

---

### 模块 2.20 — 当日汇总（Daily Summary）

入口：`/today/`（`daily_summary_view`，侧栏 Dashboard 下方 **Today**）。面向收银/交班的当日速览，**按活动店铺过滤**（All stores 显示合计）。

- **F2.20.1** 默认展示当天；`?date=YYYY-MM-DD` 可查看历史某天（不超过今天），提供前一天/今天/后一天导航。
- **F2.20.2 KPI**：销售额、订单数、件数、客单价、净利润（仅超级管理员）、当日入库件数与单数（库存全店共享，标注 all stores）。
- **F2.20.3 支付占比**：环形图 + 逐方式金额与占比（读 `window.CHART_PALETTE`）。支付明细（当日 Payment mix 与每单 `summary_pay`）从订单级 `SaleOrderPayment`（`_order_tender_amounts`）聚合，正确反映行内拆分支付（刷卡+现金）；无 `SaleOrderPayment` 的旧单回退按行 `Sale.payment_method`。
- **F2.20.4 当日订单表**：时间、客户（链接客户详情）、件数、支付（拆分行上下堆叠）、金额、利润（超管）。每单两个操作——**Details**（打开订单详情弹窗：逐行商品缩略图/数量/单价/支付/小计/利润 + 合计 + 拆分支付汇总）与 **Print**（导航到发票/小票页 `sale_order_detail`）。**All stores 时新增 Store 列**（每单所属店铺 `summary_store`），单一店铺视图不显示该列。
- **F2.20.5 Top 产品**：当日按销售额排序的畅销榜（含相对条形，链接产品详情）。
- **F2.20.6** 提供跳转当日"Full records"（`sales_records?start=end=当天`）与**打印**（`@media print` 隐藏侧栏与操作按钮）。金额受 `has_order_reconciliation_access` 门控，利润受 `is_superuser` 门控。

---

## 3. 非功能性需求（NFR）

- **NFR-1 数据一致性**：所有涉及库存批次扣减/归还、订单与行项目联动修改的操作必须在 `transaction.atomic()` 中完成，避免部分写入导致库存与订单不一致。
- **NFR-2 幂等性**：进货（2.3）、出货（2.4）等高频表单提交接口必须具备幂等保护（SHA256 + 短 TTL 缓存），防止网络重试/双击导致的重复记录。
- **NFR-3 权限隔离**：成本价、FIFO 进价、利润等"销售敏感数据"在所有页面（产品列表/详情、客户详情、销售记录、订单详情、目录页）必须按 `has_manager_access` / `has_sales_sensitive_access` / `is_superuser` 规则统一控制可见性，不应在前端 HTML 中泄露给无权限角色（即使隐藏显示，也不应将原始数值输出到页面 DOM/JSON 中）。
- **NFR-4 审计可追溯**：历史订单修正必须保留修改前后快照（`SaleOrderChangeLog`），记录操作人与时间。
- **NFR-5 库存约束**：数据库层面通过 `CheckConstraint` 保证 `Purchase.remaining >= 0` 且 `Purchase.quantity >= Purchase.remaining`，任何业务逻辑不得绕过该约束产生非法数据。
- **NFR-6 性能**：`sale_profit_map_for_sale_ids` 当前对全量历史 Purchase+Sale 做 FIFO 重放，在当前数据规模（约 3744 条销售记录）下可接受；当数据量显著增长时需评估优化（增量缓存/物化）。
- **NFR-7 时区**：所有日期分组、统计、考勤计算均以 `Europe/Lisbon` 时区为准。
- **NFR-8 并发安全**：库存批次（`Purchase.remaining`）的扣减/归还/调整必须通过条件 `UPDATE`（`F()` 表达式 + 行数检查，见 `services/stock_ops.py`）实现乐观并发控制，而非"读取-修改-写入"；检测到冲突时整体操作失败并提示调用方重试（fail-fast），不得静默丢失更新。**不得依赖 `select_for_update()` 提供锁保护**——该方法在本项目使用的 SQLite 后端上是 no-op（`has_select_for_update = False`）。

---

# 第二部分：验收标准（Acceptance Criteria）

> 格式说明：每条标准采用 `Given / When / Then` 或勾选清单（Checklist）形式，编号对应上文 PRD 编号（如 AC-2.3 对应模块 2.3）。

## AC-2.2 经营仪表盘

- [ ] **AC-2.2.1** Given 当前自然月存在销售/采购/AR 数据，When 访问 `/dashboard/`，Then 页面展示的月度销售额/利润/采购额/AR 余额与数据库聚合结果一致（误差为 0）。
- [ ] **AC-2.2.2** Given 用户选择历史月份，When 切换月份，Then 页面数据切换为该月份快照，且不影响当月正在产生的新数据。
- [ ] **AC-2.2.3** Given 某产品本月库存数量 < 5，Then 该产品出现在"低库存预警"区块，并按品牌分组展示。
- [ ] **AC-2.2.4** Given 当前用户为 Employee（非经理），When 访问 Dashboard，Then 利润/成本类区块不可见或不在响应内容中出现。
- [ ] **AC-2.2.5** Given Dashboard 加载，Then 页面包含可正常解析的二维码图片（用于分享目录链接）。
- [ ] **AC-2.2.6** Given 本月销售额为 X、上月同期窗口销售额为 Y(>0)，When 访问 Dashboard，Then 销售额 KPI 旁展示 `(X-Y)/Y` 的增减幅徽标（方向箭头 + 绝对百分比）；Given 上月同期无销售(Y=0)，Then 展示"新增/无往期"提示而非除零错误。
- [ ] **AC-2.2.7** Given 为 "Perfumes" 配置 `SalesTarget=200`、为其它分类配置不同目标，When 以 Perfumes 筛选访问 Dashboard 且本月销售额为 100，Then 目标控件 `target_amount=200`、进度 50%（只计入所选分类目标）；Given 未配置任何目标，Then 不展示目标控件。
- [ ] **AC-2.2.8** Given 当日存在 cash 支付的销售合计 EUR 30，When 访问 Dashboard，Then "Payment methods today"区块出现 Cash = EUR 30 的统计卡片（含件数与当日占比）。
- [ ] **AC-2.2.9** Given 某年存在销售数据，When 访问 `/sales-trend/`，Then 返回 12 行逐月明细（`monthly_rows` 长度 12），年度合计 `year_sales_amount` 等于该年各月销售额之和；Given 当前用户为超管，Then 明细表与年度合计含利润列，否则不含。

## AC-2.3 进货管理

- [ ] **AC-2.3.1** Given 已登录用户填写进货购物车（含供应商、发票号、≥1 个商品行）并提交，When 提交成功，Then 创建一条 `InboundOrder(status='pending_receipt')` 且 `total_amount = Σ(quantity × cost_price)`，行项目存为 `InboundPendingItem`，**不创建任何 `Purchase`、产品库存不变**。
- [ ] **AC-2.3.2** Given 未选择供应商提交进货购物车，When 提交成功，Then 为每个商品立即创建 `Purchase`（`inbound_order` 与 `supplier` 均为空、`remaining==quantity`），不创建 `InboundOrder`。
- [ ] **AC-2.3.3** Given 一张暂定订单，When 在确认收货页提交 `action=receive`，Then 每条 `InboundPendingItem` 转为 `Purchase`（`remaining==quantity`），订单 `status` 变为 `received` 且 `received_at` 被设置，产品库存增加对应数量，pending 行被清空。
- [ ] **AC-2.3.4** Given 一张暂定订单，When 提交 `action=cancel`，Then 该 `InboundOrder` 及其 `InboundPendingItem` 被删除，且未产生任何库存。
- [ ] **AC-2.3.5** Given 同一份进货表单数据在 8 秒内被提交两次，When 第二次请求到达，Then 不创建第二份重复订单（幂等命中）。
- [ ] **AC-2.3.6** Given 访问 `/inbound/`，Then 页面列出全部 `pending_receipt` 订单，且 Invoice Date 输入默认填充为当天。

## AC-2.4 销售/收银（POS）

- [ ] **AC-2.4.1** Given 商品当前总库存为 N，When 提交销售购物车中该商品数量 > N，Then 整单提交被拒绝，不创建任何 `SaleOrder`/`Sale`/扣减任何 `Purchase.remaining`。
- [ ] **AC-2.4.2** Given 商品当前总库存充足且分布在多个批次，When 提交销售，Then 按批次 `date` 升序（最早批次优先）依次扣减 `remaining`，扣减总量等于销售数量，且任意批次 `remaining` 不会变为负数。
- [ ] **AC-2.4.3** Given 提交销售购物车成功，Then 创建 1 个 `SaleOrder` 与 N 个 `Sale`（N = 购物车行数），且 `SaleOrder.total_amount == Σ(item.quantity × item.unit_price)`。
- [ ] **AC-2.4.4** Given 同一份销售表单数据在 8 秒内重复提交，When 第二次请求到达，Then 不创建重复订单（幂等命中），库存不会被二次扣减。
- [ ] **AC-2.4.5** Given 销售创建成功，When 查询该日期对应的 `DailySalesSummary`，Then 其 `total_sales`/`total_items_sold`/`total_profit` 在事务提交后被异步重算为最新值。
- [ ] **AC-2.4.6** Given 一个订单含两行（A：2×€15 选 cash；B：1×€20 选 card），When 提交成功，Then 行 A 的 `Sale.payment_method=cash`、行 B `=card`，并创建按方式汇总的 `SaleOrderPayment`（cash €30、card €20）。（向后兼容：直接提交订单级 `payments_json` 时仍按其汇总并校验总额一致。）
- [ ] **AC-2.4.7** Given 支付拆分金额之和与订单折后总额不一致（差额 > €0.01），When 提交，Then 返回错误（"must add up …"）且不创建任何 `SaleOrder`/`Sale`/`SaleOrderPayment`。
- [ ] **AC-2.4.8** Given 同款商品被两次加入购物车，Then 购物车中表现为两行独立行项目（不合并），每行可独立编辑/删除。

## AC-2.5 产品管理

- [ ] **AC-2.5.1** Given Employee 角色访问产品列表，Then 成本价/利润相关列不可见（不在页面 HTML/响应数据中）。
- [ ] **AC-2.5.2** Given Manager 创建新产品并选择"新增品牌"/"新增系列"，When 保存成功，Then 新 `Brand`/`ProductSeries` 被创建，且产品的 `brand`/`model` 字段与所选系列保持一致。
- [ ] **AC-2.5.3** Given 一个产品没有任何 `Purchase` 与 `Sale` 记录，When Manager 执行删除，Then 产品及其图片被删除，操作成功。
- [ ] **AC-2.5.4** Given 一个产品存在 ≥1 条 `Purchase` 或 `Sale` 记录，When 非超级管理员的 Manager 尝试删除，Then 删除被拒绝，并提示存在的采购/销售记录数量。
- [ ] **AC-2.5.5** Given 同上场景，When 超级管理员勾选"强制删除"并提交，Then 产品及其全部 `Purchase`/`Sale`/`ProductImage` 被删除；因此变为空的 `SaleOrder`/`InboundOrder` 被自动清理，未变空的 `InboundOrder.total_amount` 被重新计算为剩余行项目之和。
- [ ] **AC-2.5.6** Given 扫码查询一个存在的条码，When 调用 `check_barcode`，Then 返回包含 `exists=true`、展示名、当前库存、最近进价、零售价等字段；条码不存在时返回 `exists=false`。
- [ ] **AC-2.5.7** Given 调用 `api_adjust_total_stock` 将某产品库存从 10 调整为 15，Then 新建一条 `cost_price=0, remaining=5` 的 `Purchase` 记录，且产品 `total_stock()` 变为 15。
- [ ] **AC-2.5.8** Given 调用 `api_adjust_total_stock` 将某产品库存从 10 调整为 4，Then 按 FIFO 顺序从最早批次开始扣减 `remaining`，合计扣减 6，且不产生负数 `remaining`。
- [ ] **AC-2.5.9** Given 调用 `api_adjust_total_stock` 传入负数目标值，Then 返回 `success=false` 且不修改任何数据。
- [ ] **AC-2.5.10** Given Employee（非经理）角色，When 调用 `api_adjust_purchase_stock` 或 `api_adjust_total_stock`，Then 返回 `success=false`、HTTP 403，且不修改任何数据。
- [ ] **AC-2.5.11** Given 调用 `api_adjust_purchase_stock`/`api_adjust_total_stock` 成功，Then 生成一条对应的 `StockAdjustmentLog` 记录（旧值/新值/操作人/调整类型与本次调整一致）。
- [ ] **AC-2.5.12** Given 两个并发请求同时调整同一 `Purchase.remaining`，When 其中一个请求先提交成功，Then 后提交的请求检测到 `remaining` 已发生变化并返回"please retry"错误，不产生丢失更新（lost update）。
- [ ] **AC-2.5.13** Given 同一产品分别从供应商 A（成本 8）与 B（成本 11）进货，When 经理访问产品详情页，Then "Suppliers & cost"区块 `cheapest_supplier_id` 为 A，并对 A 标记"Cheapest"。

## AC-2.6 客户管理

- [ ] **AC-2.6.1** Given 新建客户时 NIF 不是 9 位数字，When 提交，Then 返回错误"NIF must be exactly 9 digits."，不创建客户。
- [ ] **AC-2.6.2** Given NIF 已存在于系统中，When 再次以相同 NIF 创建客户，Then 返回错误"Customer with this NIF already exists."。
- [ ] **AC-2.6.3** Given 客户存在多笔订单，When 访问客户详情页，Then 按"年月 → 日期"两级分组展示订单，且每级小计（金额/数量/利润）之和等于该客户全部订单的总计。
- [ ] **AC-2.6.4** Given 客户存在未结清的 AR 发票（余额 > 0），且当前用户为 Manager 及以上，Then 客户详情页展示 AR 概览区块；Given 该客户无未结清发票或当前用户为 Employee，Then AR 概览区块不展示。
- [ ] **AC-2.6.5** Given 当前用户非超级管理员，When 访问客户详情页，Then `total_profit`/各订单 `line_profit` 不展示真实数值（为 0 或不渲染）。
- [ ] **AC-2.6.6** Given 客户有订单历史且当前用户为 Manager 及以上，When 访问客户详情页，Then `spend_trend`（按月升序）、`payment_mix`（按方式汇总）、`top_products`（按消费额聚合单位数与消费额）与 `cadence`（首单、平均间隔、月均频次）均被填充并渲染对应图表/区块；Given 当前用户为 Employee，Then 不输出 `spendTrendData`/`payMixData` 等销售敏感图表数据到页面。
- [ ] **AC-2.6.7** Given 目录存在客户记录，When 访问客户搜索页，Then 渲染活跃度环形图（`activity_breakdown` = Active/Quiet/No orders，三段之和等于可见客户数），且该统计来自汇总查询而非逐客户查询。
- [ ] **AC-2.6.8** Given 客户在某日期区间内只有部分订单，When 以该区间（`start_date`/`end_date` 或 `preset`）访问客户详情页，Then 汇总 KPI、`spend_trend`、`top_products` 与订单时间线仅统计区间内订单（`range_key='custom'`、`range_active=True`）；Given 不带任何区间参数访问，Then `range_key='all'` 且统计覆盖该客户全部订单。

## AC-2.7 供应商管理

- [ ] **AC-2.7.1** Given 存在国家为 France 的两个供应商与 Spain 的一个供应商，When 访问供应商列表，Then `supplier_groups` 按国家分组（France、Spain，未填国家组排最后），且各组内按名称排序。
- [ ] **AC-2.7.2** Given 列表附加 `?country=Spain`，Then 仅展示 Spain 分组。
- [ ] **AC-2.7.4** Given 当前用户为 Employee，When 访问供应商列表/详情/编辑/删除 URL，Then 被拒绝访问（权限不足）。
- [ ] **AC-2.7.5** Given 某供应商有一条 4 件 × €10 的进货（received，`created_at` 比 `received_at` 早 2 天），When 经理访问供应商详情，Then 记分卡 `lifetime_spend=€40`、`units=4`、`top_products[0]` 为该产品、`avg_lead_days≈2`。
- [ ] **AC-2.7.6** Given 新建供应商时填写联系人/邮箱/网站/税号，When 提交成功，Then 这些字段被正确保存。

## AC-2.8 销售记录与报表

- [ ] **AC-2.8.1** Given 选择某个日期范围，When 访问 `/sales-records/`，Then 返回的每日分组中，订单总数与该日 `SaleOrder` 实际数量一致，每个订单展示的行项目与 `Sale` 记录一致。
- [ ] **AC-2.8.2** Given 当前用户非超级管理员，When 访问 `/sales-records/`，Then 响应中不包含利润（profit）相关数值。
- [ ] **AC-2.8.3** Given 当前用户为超级管理员，Then 响应中每日/每单的利润数值等于通过 `sale_profit_map_for_sale_ids` 独立计算的结果。
- [ ] **AC-2.8.4** Given 当日存在进货记录，When 访问 `/sales-records/`，Then 当日区块展示对应进货订单，且链接可跳转到 `inbound_order_edit`。

## AC-2.9 历史订单修正与审计

- [ ] **AC-2.9.1** Given 当前用户非超级管理员，When 访问 `/sale-orders/manage/` 或其编辑/创建子路径，Then 返回权限拒绝（不可访问）。
- [ ] **AC-2.9.2** Given 超级管理员编辑一个订单，将某行项目商品 A 数量从 3 改为 1，When 保存成功，Then 商品 A 对应的 `Purchase.remaining` 总和增加 2（先归还旧数量再按新数量消耗），且整个过程在单一事务内完成（若中途失败应整体回滚）。
- [ ] **AC-2.9.3** Given 保存修正成功，Then 生成一条 `SaleOrderChangeLog`，`action='update'`，`before_data`/`after_data` 分别为修改前后的订单快照 JSON，`changed_by` 为当前操作用户。
- [ ] **AC-2.9.4** Given 超级管理员删除一个订单修正，Then 该订单全部行项目对应的库存被归还到相应 `Purchase.remaining`，并生成一条 `action='delete'` 的 `SaleOrderChangeLog`。
- [ ] **AC-2.9.5** Given 修正中心列表页，When 搜索关键字，Then 返回的订单列表与搜索条件匹配，并展示最近的变更日志。
- [ ] **AC-2.9.6** Given 一个含商品 A、B 两行的订单，When 编辑时仅提交 A（移除 B）并保存，Then B 的 `Sale` 行被删除、B 的库存被归还（`Purchase.remaining` 回升），A 保持不变。
- [ ] **AC-2.9.7** Given 通过修正中心创建/编辑订单并提交 `payments_json`（或按行支付聚合），When 保存成功，Then 重建出与该订单一致的 `SaleOrderPayment`（按方式汇总），且各 `Sale.payment_method` 取该行主方式——与 POS 出货生成的订单形态一致。

## AC-2.10 每日销售汇总

- [ ] **AC-2.10.1** Given 创建一条新的 `Sale` 记录（日期为 D），When 事务提交后，Then `DailySalesSummary(date=D)` 的 `total_sales`/`total_items_sold`/`total_profit` 被重新计算并与该日全部 `Sale` 数据一致。
- [ ] **AC-2.10.2** Given 修改一条已存在 `Sale` 的日期从 D1 改为 D2，When 事务提交后，Then `D1` 与 `D2` 两个日期的 `DailySalesSummary` 均被重算。
- [ ] **AC-2.10.3** Given 删除一条 `Sale` 记录（日期为 D），When 事务提交后，Then `D` 的 `DailySalesSummary` 被重算，且其总额相应减少。
- [ ] **AC-2.10.4** Given 在 Django Admin 中对某条 `DailySalesSummary` 执行"重建所选汇总"操作，Then 该记录的三个统计字段被重新计算为通过 FIFO 重放得到的值。

## AC-2.11 应收账款（AR）

- [ ] **AC-2.11.1** Given 创建一张包含 N 个明细行的 AR 发票，When 保存成功，Then `ARInvoice.total_amount == Σ(item.unit_price × item.quantity)`，状态为 `unpaid`，`amount_paid == 0`。
- [ ] **AC-2.11.2** Given 一张发票 `total_amount=100`，登记一笔 `amount=40` 的收款，When 保存成功，Then `amount_paid==40`，状态变为 `partial`，`balance==60`。
- [ ] **AC-2.11.3** Given 同上发票继续登记一笔 `amount=60` 的收款，When 累计 `amount_paid==100`，Then 状态变为 `paid`，`balance==0`。
- [ ] **AC-2.11.4** Given 向已有发票追加明细行，When 保存成功，Then `total_amount` 重新计算为全部行项目之和，并按 F2.11.4 规则刷新状态（如追加后 `amount_paid < total_amount` 则状态从 `paid` 退回 `partial`）。
- [ ] **AC-2.11.5** Given 某发票 `due_date` 已过且 `balance > 0`，When 访问 AR 列表，Then 该发票被标记为逾期（overdue）。
- [ ] **AC-2.11.6** Given Employee 角色，When 访问任意 `/ar/...` 路径，Then 被拒绝访问。

## AC-2.12 进货订单 / 散单编辑

- [ ] **AC-2.12.1** Given 某 `Purchase` 行 `quantity=10`，已售出（`quantity - remaining`）=6，When 编辑表单将 `quantity` 改为 5，Then 表单校验失败，提示不可将数量降低到低于已售数量（5 < 6）。
- [ ] **AC-2.12.2** Given 上述 `quantity` 改为 8（≥ 已售 6），When 提交成功，Then `Purchase.quantity=8` 且 `remaining` 相应调整（`remaining = quantity - 已售数量 = 2`），并通过数据库 CheckConstraint。
- [ ] **AC-2.12.3** Given 编辑某 `InboundOrder` 的行项目进价，When 保存成功，Then `InboundOrder.total_amount` 重新计算为全部行项目 `quantity × cost_price` 之和。

## AC-2.13 团队（员工）管理

- [ ] **AC-2.13.1** Given 当前用户非超级管理员，When 访问 `/team/` 或其子路径，Then 被拒绝访问。
- [ ] **AC-2.13.2** Given 超级管理员创建一个角色为 "manager" 的员工账号，When 保存成功，Then 新用户 `is_staff=True`；角色为 "employee" 时 `is_staff=False`。
- [ ] **AC-2.13.3** Given 对某员工执行"停用"，When 操作完成，Then 该用户 `is_active=False`，但用户记录与历史数据仍存在；该用户登录将被拒绝。
- [ ] **AC-2.13.4** Given 对某员工执行"删除"，When 操作完成，Then 该用户记录被物理删除；不可对 `is_superuser=True` 的账号执行此操作（应被拒绝或不可选）。
- [ ] **AC-2.13.5** Given 某员工当前处于"打卡中"状态，When 访问员工列表，Then 该员工行展示 `open_shift` 标记及对应的 `AttendanceRecord`。

## AC-2.14 考勤管理

- [ ] **AC-2.14.1** Given 当前用户没有未结束的打卡记录，When 执行"打卡上班"，Then 创建一条 `AttendanceRecord(clock_in_at=now, clock_out_at=null)`。
- [ ] **AC-2.14.2** Given 当前用户存在未结束的打卡记录，When 执行"打卡下班"，Then 该记录的 `clock_out_at` 被设置为当前时间，`worked_duration` 等于 `clock_out_at - clock_in_at`。
- [ ] **AC-2.14.3** Given 当前用户存在未结束的打卡记录，When 再次执行"打卡上班"，Then 系统不应创建第二条未结束记录（应阻止或先结束前一条 — 以代码实际行为为准并据此补充用例）。
- [ ] **AC-2.14.4** Given Manager 访问团队考勤视图，Then 每位团队成员展示本月 `shift_count`、`total_duration`（累计工时）与 `open_shift` 状态，且数值与各成员的 `AttendanceRecord` 汇总一致。

## AC-2.15 产品目录（Catalog）

- [ ] **AC-2.15.1** Given 某产品库存为 5，Then 目录页该产品展示"Available now"徽章；库存为 2 时展示"Low stock"；库存为 0 时展示"Currently unavailable"。
- [ ] **AC-2.15.2** Given 访问 `/catalog/`，Then 响应内容中不包含任何成本价/进价/利润字段或数值。
- [ ] **AC-2.15.3** Given 目录数据按品牌→型号分组，Then "热销榜"展示按累计销量排序的前 5 个产品，且与全量销售数据统计结果一致。
- [ ] **AC-2.15.4** Given Employee 角色，When 访问 `/catalog/export-excel/`，Then 被拒绝访问；Manager 及以上访问时返回可正常打开的 Excel 文件。

## AC-2.16 打印设置 / 小票打印

- [ ] **AC-2.16.1** Given 修改 `PrintProfile` 的店铺名称/地址等字段并保存，When 之后打开任意订单的打印页，Then 打印页头部信息与最新 `PrintProfile` 数据一致。
- [ ] **AC-2.16.2** Given 访问订单详情页并附加 `?print=1&layout=a4`，Then 返回 A4 排版的打印视图；附加 `&layout=pos` 时返回小票宽度排版视图。
- [ ] **AC-2.16.3** Given 任意已登录用户（含 Employee）访问打印视图，Then 金额信息可见（不受"销售敏感数据"权限限制隐藏）。
- [ ] **AC-2.16.4** Given Employee 角色，When 访问 `/print-profile/`，Then 被拒绝访问（仅经理及以上可编辑）。

## AC-2.17 订单详情页

- [ ] **AC-2.17.1** Given 访问某订单详情页，Then 页面展示的 `total_qty`/`total_amount`/`pay_break` 与该订单全部 `Sale` 行项目汇总结果一致。
- [ ] **AC-2.17.2** Given 当前用户为超级管理员，Then 页面包含指向 `sale_order_correction_edit` 的"管理员修正"链接；其他角色不展示该链接。

## AC-2.18 数据导出

- [ ] **AC-2.18.1** Given 在产品列表页设置筛选条件后点击"导出 Excel"，Then 导出文件中的记录集与当前筛选结果一致（行数相同）。
- [ ] **AC-2.18.2** Given 导出 Shopify CSV，Then 文件列结构符合 Shopify 库存更新所需的列名规范，可被直接导入。
- [ ] **AC-2.18.3** Given 导出操作完成，Then 服务器文件系统中不产生新的持久化临时文件（响应以流式/内存方式返回）。
- [ ] **AC-2.18.4** Given Employee 角色，When 访问任意导出端点，Then 被拒绝访问。

---

## 4. 已知技术债 / 后续建议（不在本期验收范围内）

以下问题在代码审查中被识别，记录于此供后续迭代规划，**不作为本基线版本的验收条款**：

1. `settings.py` 中 `SECRET_KEY` 硬编码、`DEBUG=True`。仓库是 public，旧 key 视为已泄露需轮换；`settings.py` 已有 `.env` 加载器，搬进去即可。
2. `db.sqlite3` 与 `media/`（79MB，产品图片）无自动备份；且本地领先远程 34 个提交，代码亦无异地副本。
3. `sale_profit_map_for_sale_ids` 的全量 FIFO 重放在数据量持续增长后可能成为性能瓶颈，建议规划增量化或缓存方案。

> 详细清单与优先级见 [TODO.md](./TODO.md)，状态层面摘要见 [STATUS.md](./STATUS.md) 第 6 节。
