# 项目架构文档（Architecture）

> 前端样式与页面设计约定见 [UI_COMPONENTS.md](./UI_COMPONENTS.md)（共享类清单 + 四条硬标准：弹窗不超屏、信息密度、分断点适配、边界情况）。

> 描述 KHAN PERFUME 库存管理系统的技术架构：技术栈、目录结构、分层设计、核心数据模型与关键业务模式。配合 [PRD.md](./PRD.md)（功能需求与验收标准）一起阅读。

---

## 1. 技术栈

| 层 | 技术 |
|---|---|
| 后端框架 | Django 5.2.4（单体应用，无 DRF / 无前后端分离） |
| 数据库 | SQLite（`db.sqlite3`，单文件） |
| 前端 | Django Template + Bootstrap 5（本地静态资源，非 CDN）+ `django-widget-tweaks` |
| 扫码 | `html5-qrcode.min.js`（浏览器端摄像头扫码） |
| 图表 | Chart.js（本地静态资源；调色板经 `window.CHART_PALETTE` 读 CSS 令牌） |
| 报表/导出 | `openpyxl`（Excel）、`reportlab`（PDF）、`csv`（Shopify 导出）、`qrcode`（二维码生成） |
| 图片处理 | `Pillow` |
| 外部集成 | `requests`（Shopify Admin GraphQL）、`cloudinary`（图床镜像）；均由环境变量门控，默认关闭 |
| 认证 | Django 内建 `django.contrib.auth`（User + Group "Managers"） |
| 配置 | `settings.py` 顶部自带 `.env` 加载器（读 `BASE_DIR/.env`，真实环境变量优先），无 `python-dotenv` 依赖 |
| 部署形态 | 单进程 `manage.py runserver` / WSGI；实际由 U 盘上的 `start.bat` → `runserver.py` 用便携版 Python 启动，无容器化 |

---

## 2. 目录结构

```
InventoryApp/
├── manage.py                      # Django 管理入口
├── requirements.txt                # 依赖清单
├── db.sqlite3                      # SQLite 数据库（git 已忽略）
├── docs/                            # 项目文档（PRD / 架构 / 状态）
│
├── inventory_system/                # Django 项目配置包
│   ├── settings.py                  # 全局设置（DEBUG、时区、INSTALLED_APPS…）
│   ├── urls.py                      # 根路由：'/' → dashboard，'/admin/'，其余 include('stock.urls')
│   ├── wsgi.py / asgi.py
│
├── stock/                            # 唯一的业务应用（“大单体 App”）
│   ├── models/                       # 域分区模型包（模块化单体，仍为同一 app / 同一迁移历史）
│   │   ├── __init__.py               # 重导出全部模型类（保证 from .models import X 不变）
│   │   ├── core.py                   # PrintProfile
│   │   ├── catalog.py                # Category, Brand, ProductSeries, Product, ProductImage
│   │   ├── partners.py               # Supplier, Customer
│   │   ├── inventory.py              # InboundOrder, InboundPendingItem, Purchase, StockAdjustmentLog
│   │   ├── sales.py                  # SaleOrder, Sale, SaleOrderChangeLog, SaleOrderPayment
│   │   ├── finance.py                # ARInvoice, ARItem, ARPayment
│   │   ├── reporting.py              # DailySalesSummary, SalesTarget
│   │   └── hr.py                     # AttendanceRecord（原 attendance_models.py 迁入）
│   ├── attendance_models.py          # 兼容 shim：从 models.hr 重导出 AttendanceRecord
│   ├── views.py                       # 全部视图逻辑（约 4800 行，60+ 个视图函数）
│   ├── forms.py                       # 全部表单与 FormSet（534 行）
│   ├── admin.py                       # Django Admin 自定义（270 行）
│   ├── urls.py                        # 路由表（60 条 path）
│   ├── permissions.py                 # 角色判定 + 装饰器
│   ├── signals.py                     # Sale 信号→每日汇总重算；ProductImage 信号→镜像图片到 Cloudinary/Shopify（门控）
│   ├── apps.py                        # AppConfig，ready() 中注册 signals
│   ├── tests.py                       # 178 个测试用例（约 3400 行）
│   │
│   ├── services/                      # 业务逻辑层（从 views 中抽出的可复用逻辑）
│   │   ├── dashboard.py               # 月度快照 + MoM 同期对比 + 销售目标 + 多店铺对比
│   │   ├── profit.py                   # FIFO 利润重放：sale_profit_map_for_sale_ids
│   │   ├── order_corrections.py        # 历史订单修正/审计核心逻辑
│   │   ├── stock_ops.py                # FIFO 库存原子条件更新：consume_stock_fifo / restore_stock_fifo
│   │   ├── summaries.py                # DailySalesSummary 重算逻辑
│   │   ├── inventory.py                # build_inventory_snapshot（库存快照）
│   │   ├── shopify_client.py / shopify_sync.py       # Shopify Admin API：按 barcode=SKU 挂图/建品
│   │   ├── cloudinary_client.py / cloudinary_sync.py # 产品主图镜像到 Cloudinary（public_id=条码, asset_folder=product_images/<品牌>）
│   │   └── cloudinary_urls.py                        # 读取 Cloudinary 命名约定（public_id=条码），构造公开交付 URL；纯字符串拼接，不发网络请求、不读凭据；供 Shopify CSV 导出使用
│   │
│   ├── management/commands/
│   │   ├── rebuild_dailysummary.py     # 正式 management command：重建每日汇总
│   │   ├── import_notino_perfume_images.py  # 一次性图片导入脚本
│   │   ├── sync_shopify_images.py / sync_shopify_products.py  # 同步图片/建品到 Shopify（默认 dry-run）
│   │   └── sync_cloudinary_images.py   # 镜像产品主图到 Cloudinary（默认 dry-run）
│   │
│   ├── migrations/                     # 27 个迁移文件（0001 ~ 0027）
│   ├── templates/stock/                # 26 个页面模板（base.html 为公共布局）
│   ├── templatetags/
│   │   ├── access_tags.py              # is_manager_user 过滤器
│   │   └── math_extras.py              # mul / sum_list / map_attr 过滤器
│   └── static/                         # Bootstrap 5 全量资源 + html5-qrcode + popper
│
├── static/                              # STATICFILES_DIRS 引用的项目级静态目录
├── media/                                # 用户上传的产品图片（git 已忽略，约 79MB，按品牌分目录）
└── .venv/                                # 本地虚拟环境（状态见 STATUS.md）
```

**架构特点**：这是一个**单 App（`stock`）的 Django 单体应用**，没有按业务域拆分成多个 Django app；分层主要靠 `views.py`（控制器+大部分业务逻辑）+ `services/`（部分抽出的纯逻辑函数）+ `models/`（数据与少量模型方法）+ `forms.py`（输入校验）实现。

**模块化进展（Modular Monolith）**：项目正按域边界逐步重组为"模块化单体"——保留单一 `stock` app 与单一迁移历史，但内部按业务域（core / catalog / partners / inventory / sales / finance / reporting / hr）拆分代码包。第一阶段（models 拆包）已完成：原 `models.py` 拆为 `models/` 包，跨域外键统一改为字符串引用（`ForeignKey('Product')`）以消除子模块间的导入顺序耦合；`makemigrations --check --dry-run` 验证模型状态零变化、52/52 测试通过。后续阶段（views/forms/admin/urls/templates/tests 同步拆分）见第 8 节路线图。

---

## 3. 路由与请求流

```
浏览器请求
  → inventory_system/urls.py
      '/'            → redirect('dashboard')
      '/admin/...'   → Django Admin
      '/...'         → include('stock.urls')
  → stock/urls.py  (70+ path，按功能分组：登录/仪表盘/进出库/产品/客户/供应商/
                     团队/考勤/订单修正/打印/AR/库存调整API)
  → stock/views.py 中对应视图函数
      - 权限装饰器：@login_required / @manager_required / @admin_required
      - 读取/写入 stock/models.py
      - 复杂计算委托给 stock/services/*
      - 表单校验委托给 stock/forms.py
  → 渲染 stock/templates/stock/*.html（继承 base.html）
     或返回 JsonResponse（AJAX API）/ 文件流（导出）
```

---

## 4. 数据模型概览（ER 关系）

```
Category (自引用 parent)
  ├─< Brand (M2M categories)
  │     └─< ProductSeries (FK brand)
  └─< Product (FK category, FK brand_master→Brand, FK series_master→ProductSeries)
          ├─< ProductImage (FK product)
          ├─< Purchase (FK product, FK supplier→Supplier, FK inbound_order→InboundOrder)
          │       —— FIFO 批次：remaining ≤ quantity，remaining ≥ 0（CheckConstraint）
          ├─< Sale (FK product, FK order→SaleOrder, FK customer→Customer)
          └─< StockAdjustmentLog (FK product, FK purchase→Purchase nullable, FK user→AUTH_USER nullable)
                  —— 库存调整审计：记录 api_adjust_purchase_stock / api_adjust_total_stock 的旧值/新值

Supplier (M2M product_types→Category)
  └─< InboundOrder (FK supplier, nullable；status=pending_receipt/received，received_at)
          ├─< InboundPendingItem (FK inbound_order；暂定行项目，确认收货后转 Purchase)
          └─< Purchase (FK inbound_order)

Customer
  ├─< SaleOrder (FK customer, nullable)
  │       ├─< Sale (FK order)
  │       ├─< SaleOrderPayment (FK order；订单级拆分支付 method+amount，related_name payments)
  │       └─< SaleOrderChangeLog (FK order, nullable；order_id_snapshot 始终保留)
  └─< ARInvoice (FK customer, PROTECT)
          ├─< ARItem (FK invoice)
          └─< ARPayment (FK invoice)

AUTH_USER (Django User)
  ├─< AttendanceRecord (FK user)
  ├─< SaleOrderChangeLog.changed_by (FK user, nullable)
  └─< StockAdjustmentLog.user (FK user, nullable)

DailySalesSummary  —— 独立汇总表，date 唯一，无外键，由 Sale 信号驱动重算
SalesTarget        —— 每分类月度销售目标（category 唯一 OneToOne→Category，monthly_amount）
PrintProfile       —— 单例配置表（固定 pk=1）
```

**关键约束**：
- `Purchase`：`CheckConstraint(remaining >= 0)`、`CheckConstraint(quantity >= remaining)`，并建有 `(product, date)`、`(product, remaining)` 复合索引，支撑 FIFO 查询性能。
- `ARInvoice.customer` 为 `on_delete=PROTECT`：存在发票时不可删除客户。
- `Product.brand` / `Product.model` 为字符串字段，与 `brand_master` / `series_master` 结构化外键并存（历史兼容设计，`save()` 中做自动同步，见 PRD F2.5.2.2）。

---

## 5. 核心业务模式

### 5.1 FIFO 库存成本
- `Purchase` 表中的每条记录代表一个"入库批次"，`remaining` 字段随销售/调整递减。
- 出货（`outbound_view`）、库存调整（`api_adjust_total_stock`）、订单修正（`order_corrections.py`）均按 `Purchase.date` 升序（最早批次优先）扣减/归还 `remaining`。
- `Product.current_fifo_cost_price()` 取最早一条 `remaining > 0` 的批次成本价；若无库存则回退到 `last_known_cost_price()`（最近一条历史批次）。
- 利润计算（`services/profit.py::sale_profit_map_for_sale_ids`）对全量 `Purchase`+`Sale` 按时间顺序重放，逐笔还原每个 `Sale` 当时消耗的批次成本 → `profit = revenue - cost`。**这是全量重放，随数据量增长需关注性能**（见 STATUS.md）。
- **并发安全的批次更新**（`services/stock_ops.py::consume_stock_fifo` / `restore_stock_fifo`）：扣减/归还均通过 `Purchase.objects.filter(pk=..., remaining__gte=/lte=...).update(remaining=F('remaining') ± n)` 的条件更新实现乐观并发控制；若 `update()` 影响行数为 0，说明该批次自读取后已被并发修改，抛出 `StockConflictError`（`ValidationError` 子类，fail-fast，要求调用方提示用户重试）。`outbound_view` 与 `order_corrections.py` 的 `_consume_current_stock`/`_restore_current_stock` 均委托给这两个函数。**不依赖 `select_for_update()`**——该方法在本项目使用的 SQLite 后端上是 no-op（`has_select_for_update = False`），不会加行锁。

### 5.1b 香水自动定价（Perfume auto-pricing，`services/pricing.py`）
- **公式**：`wholesale = ⌈current_fifo_cost + 10⌉`（向上取整为整数）、`retail(default_price) = wholesale + 12`。例：成本 12.34 → 批发 23、零售 35。
- **仅限 Perfumes 分类**（`is_perfume(product)` 按 `category.name` 大小写不敏感等于 `"Perfumes"` 判定）；以下情况跳过（不写入）：产品 `price_locked=True`、非 Perfumes 分类、或 `current_fifo_cost_price() <= 0`。
- **`sync_perfume_price(product)`**：幂等——先比较算出的 `wholesale`/`retail` 与产品当前值是否相同，相同则不写、返回 `False`；仅当价格确实变化时才 `Product.objects.filter(pk=product.pk).update(wholesale_price=..., default_price=...)`（**从不调用 `save()`**，避免触发 `Product.save()` 里其它字段同步逻辑或递归信号）。
- **触发点**（凡是"当前 FIFO 成本"可能变化之处）：
  - `stock/signals.py::reprice_perfume_on_purchase`——`Purchase` 的 `post_save` 信号（新增/编辑批次，即进货入库触发）；异常被捕获记录日志，不让定价失败影响入库主流程。
  - `services/stock_ops.py::consume_stock_fifo` / `restore_stock_fifo` 末尾各调用一次——出货/归还消耗的是最早批次，走的是批量 `.update()`（不产生 `post_save` 信号），因此需要显式调用。
  - `views.py` 中的 `api_adjust_purchase_stock`、`api_adjust_total_stock` 两个库存调整 API（调整某批次或总库存后可能换到另一批次作为"当前"批次）。
  - 因为 `sync_perfume_price` 幂等且仅在价格真正变化时写入，**并非每次入库都会改价**——只有当"当前正在卖的那个批次"的成本变化时（如原批次卖空、切到下一批次，或调整的正是当前批次）才会重算出新值。
- **`Product.price_locked`**（`BooleanField`，默认 `False`，迁移 `0034_product_price_locked.py`）：新增/编辑产品表单上的 **"Lock price"** 复选框，勾选后该产品的手动定价被锁定，`sync_perfume_price` 对其永远跳过（即使成本继续变化）。
- **员工只读价格（Task 7）**：`ProductForm.__init__(..., can_edit_prices=True)` 在 `can_edit_prices=False`（非经理）时把 `default_price`/`wholesale_price`/`price_locked` 三个字段设为 Django `disabled=True`——服务端强制：即使篡改 POST 带上这些字段的值，Django 表单在 `disabled=True` 时会忽略提交值、改用 `initial`/实例原值，不会被写入。适用于**全部产品**（不限于 Perfumes）。视图层调用处按 `has_manager_access` 传入 `can_edit_prices`。
- **`sync_perfume_prices` 管理命令**（`management/commands/sync_perfume_prices.py`）：对全部 `category__name__iexact='Perfumes'` 的产品批量调用 `sync_perfume_price`，用于给已存量的香水产品补算价格；支持 `--dry-run`（仅打印将处理的产品，不写入）。

### 5.2 幂等提交（Idempotency）
- 进货（`inbound_view`）、出货（`outbound_view`）等高频表单提交对请求体做 SHA256 摘要，调用 `cache.add(digest, True, timeout=8)`。
- `cache.add` 仅在 key 不存在时写入并返回 `True`；8 秒内的重复提交会因 key 已存在而被识别为重复请求并拒绝，防止双击/网络重试导致的重复入库/出货。

### 5.3 每日汇总的异步重算
- `stock/signals.py` 监听 `Sale` 的 `pre_save`/`post_save`/`post_delete`。
- `pre_save` 记录变更前的日期，`post_save`/`post_delete` 对比变更前后日期，调用 `services/summaries.py::schedule_summary_recalc()`。
- `schedule_summary_recalc` 通过 `transaction.on_commit()` 注册回调，确保**事务成功提交后**才重算 `DailySalesSummary`（避免在回滚的事务中计算出错误汇总）。
- `rebuild_all_daily_summaries()` + Django Admin 批量 action 提供全量重建能力。

### 5.4 权限分层（见 `stock/permissions.py`）
```
has_manager_access(user)        → is_staff or in "Managers" group or is_superuser
has_sales_sensitive_access(user) → 等价于 has_manager_access（成本/利润可见性）
has_order_reconciliation_access(user) → 任意已登录用户（订单金额对账，非"敏感数据"）
has_admin_access(user)          → is_superuser only
```
- 装饰器 `manager_required` / `admin_required` 用于视图级拦截（未授权时重定向到 dashboard）。
- 模板级通过 `access_tags.py::is_manager_user` 过滤器 + 视图传入的 `show_sensitive`/`show_sales_sensitive`/`show_profit`/`show_order_financials` 等布尔上下文变量控制字段显隐。
- AJAX/JSON API（如 `api_adjust_purchase_stock`/`api_adjust_total_stock`）的权限校验不使用 `manager_required` 装饰器——该装饰器在未授权时 `redirect("dashboard")`，会破坏 JSON 响应契约；这类视图改为手动 `if not has_manager_access(request.user): return JsonResponse({...}, status=403)`。

### 5.4.1 员工界面收窄（Employee interface restriction，2026-07-16）
员工（`has_manager_access(user)` 为 False，即非 `is_staff`/非 "Managers" 组/非超管）的可用界面被收窄为一个精简子集，而非完整功能的隐藏字段版本。

- **侧边栏**：员工仅看到 Dashboard / Outbound（POS）/ Products / Sales / Customers 五个入口（`base.html` 用 `{% if user|is_manager_user %}` 包裹其余分组/条目）。隐藏：Today（daily_summary）、Inbound、Attendance、Catalog View、Suppliers、IOU/AR、Admin 分组。
- **视图级拦截（`@manager_required`，员工 GET 直接 302 到 dashboard）**：`daily_summary_view`、`inbound_view`/`inbound_receive_view`、`catalog_view`、`ar_list_view`/`ar_detail_view`（连同 AR 全部写操作视图）、`attendance_view`。产品的 `delete_product_view`/`delete_product_image`、以及 Shopify CSV 导出仍仅经理。
- **Products 对员工重新开放（查看/搜索/新增/编辑/导出）**：`product_list_view`/`product_detail_view`/`add_product_view`/`edit_product_view`/`export_product_list_excel` 均**已移除** `@manager_required`（只保留 `@login_required`）——员工可查看/搜索产品、看详情、**新增并编辑**产品（改字段 + 加图），并下载**面向顾客的产品 Excel**（列仅 图片/产品/分类/零售价/批发价/库存，无成本/供应商/利润）。**仍仅经理**：进货（inbound）、删除产品/图片（`edit_product.html` 中这两处删除控件对员工 `{% if user|is_manager_user %}` 隐藏）、Shopify CSV 导出。敏感产品信息（成本、"Suppliers & cost" 比价、批次成本、销售历史/指标）继续由既有 `show_sensitive`/`show_sales_sensitive` 模板门控隐藏。无审批/待定工作流，无 schema 变更。
- **Sales（员工）**：`record_view` 首行分支——非经理调用 `_employee_sales_day_view(request)`，渲染 `sales_records_employee.html`：单日订单列表（GET `date`，缺省今天）+ € 金额，无图表/年度总览/采购，按活动店铺（`scope_sales_by_store`）过滤。**订单号搜索**（GET `order`，`.isdigit()` 校验、店铺范围内命中即 302 到 `sale_order_detail`）。每个订单行提供 **View**（Bootstrap 弹窗内联显示该单明细：商品/数量/单价/小计/合计，**不含利润成本**）+ **Print**（链到 `sale_order_detail`）两个按钮。经理保留完整 `record_view` 页面。
- **Customers（员工）**：`customer_search_view` 首行分支——`_employee_customer_search_view`：无查询词不出列表，有词（name/phone/email/nif icontains）仅出姓名/电话/邮箱（上限 50），保留"Add customer"（`add_customer_ajax`，模态框 AJAX）。`customer_detail_view` 首行分支——`_employee_customer_orders_view`：该客户订单列表（日期/订单号/件数/支付/金额，按店铺过滤）供对账，每行同样是 **View 弹窗 + Print 按钮**，无分析图表/余额/时间线。经理保留完整客户页。
- **订单详情店铺隔离**：`sale_order_detail_view` 对非经理用 `scope_sales_by_store` 过滤——员工只能打开本店订单（越店 id 直接 404）；经理及无 `StoreProfile` 的用户不过滤（安全缺省）。
- **员工页样式**：三个精简模板（`sales_records_employee`/`customer_search_employee`/`customer_orders_employee`）已套用与经理页一致的共享设计骨架（`page-shell`/`page-card pad`/`page-head`/`page-title`/`strip-head`/`toolbar`/`table-wrap`），视觉与经理页统一。
- **自动考勤（Auto-attendance）**：`stock/signals.py` 新增 `user_logged_in`/`user_logged_out` 的 `@receiver`：`open_attendance_on_login` 对非经理用户在登录时开一条 `AttendanceRecord` 班次——若当天已有未打卡下班的班次则复用（不重复开），若存在**前一天**未关闭的班次则先自动补 23:59:59 打卡下班（`note` 追加 `auto-closed: no logout`）再开新班次；`close_attendance_on_logout` 对非经理用户在登出时关闭最近一条未下班的班次（`note` 追加 `auto: logout`）。经理登录/登出不产生任何 `AttendanceRecord`（`has_manager_access` 早退）。`AttendanceRecord` 模型与迁移历史均无变化，纯签入既有信号机制（`apps.py.ready()` 已注册 `signals` 模块，无需额外接线）。`attendance_view` 现为 `@manager_required`（团队考勤汇总视图，见上）。
- **既往"已审查决策"作废说明**：此前本节记录 `ar_list_view`/`ar_detail_view` 仅 `@login_required`（任意登录用户可访问、靠模板隐藏金额）为"已审查、维持现状"的设计决策；该决策已被本次改动**取代**——两个视图现均加了 `@manager_required`，员工完全无法访问 AR 列表/详情（302 到 dashboard），不再依赖模板隐藏金额。

### 5.5 历史订单修正与审计（`services/order_corrections.py`）
- **与 POS 出货同构的购物车式编辑**：修正表单（`sale_order_correction_form.html`）改用与 `outbound.html` 一致的客户端购物车（产品自动补全 → 行项目弹窗：数量/零售批发切换/每行 € 折扣/每行支付方式或行内拆分），序列化为 `items_json`（`product_id` 标识）+ `payments_json`；视图 `_parse_correction_cart` 复用与出货相同的校验/支付汇总契约，编辑态用 `_build_correction_cart` 把现有行预载（`initial_cart` → `json_script`）。**旧的行项目 `formset` 已移除**（其隐藏 `DELETE` 字段 + JS `.checked` 不生效，导致"删除行"不回滚的缺陷一并消除）。
- 修正前调用 `snapshot_sale_order()` 生成订单当前状态的 JSON 快照（含 `payments`）。
- `_restore_current_stock()` 归还旧行项目占用的 FIFO 批次，随后删除全部旧 `Sale` 与旧 `SaleOrderPayment`，`_consume_current_stock()` 按新行项目重新消耗批次并重建 `Sale` + `SaleOrderPayment`（与出货一致的订单级支付权威记录，`Sale.payment_method` 取该行主方式）—— 全程在 `transaction.atomic()` 中执行；两者均委托给 `services/stock_ops.py`（见 5.1）。**因此移除某产品行会真正回滚（库存归还 + 销售记录删除）。**
- 修正/创建/删除均写入 `SaleOrderChangeLog`（`before_data`/`after_data`/`changed_by`/`reason`），形成可追溯的审计链。
- 订单修正保存时可改归属店铺：`save_sale_order_correction(store=...)` 显式所选店铺优先，盖到 `SaleOrder` 与重建的每条 `Sale`；`snapshot_sale_order` 记录 `store_id/store_name` 供审计。仅活跃店铺可选，缺失/非法回退原店铺。
- 补录订单可选「不影响库存」：`SaleOrder.affects_stock`（默认 True）门控创建/编辑/删除的库存扣减与归还；`sale_profit_map_for_sale_ids` 对 `affects_stock=False` 的销售跳过 FIFO 批次、成本/利润各取销售额 50%（`BACKFILL_MARGIN`）。

### 5.6 库存调整审计（`StockAdjustmentLog`）
- `api_adjust_purchase_stock`（调整单个批次 `remaining`）与 `api_adjust_total_stock`（调整产品总库存）均要求 `has_manager_access`，并在调整成功后写入一条 `StockAdjustmentLog`（字段：`adjustment_type`、`old_value`、`new_value`、`product`、`purchase`（可空，仅单批次调整时关联）、`user`、`created_at`）。
- 在 Django Admin 中只读展示（`StockAdjustmentLogAdmin` 禁止新增/修改，仅供审计查阅，`list_filter`/`search_fields`/`date_hierarchy` 支持按类型/时间/产品检索）。
- 并发安全：`api_adjust_purchase_stock` 使用与 5.1 相同的条件 `UPDATE`（`WHERE remaining = <旧值>`）防止并发覆盖；`api_adjust_total_stock` 的减库存路径直接复用 `consume_stock_fifo`。

### 5.7 仪表盘分析与缓存（`services/dashboard.py`）
- `build_monthly_dashboard_snapshot` 计算月度全量快照（销售/采购/AR/库存/资金占用/滞销品等）。
- **MoM 同期对比**用轻量函数 `compute_period_headline`（仅做销售头部聚合，DB 端 `Sum(unit_price*quantity)`，利润仅在 `show_profit` 时才触发 FIFO 重放）计算上月同期窗口，避免为对比而重复整份快照的库存/AR 开销；`build_period_comparison` 产出各 KPI 的带符号增减幅。
- **销售目标**：`build_target_progress` 汇总所选分类的 `SalesTarget.monthly_amount`，给出达成进度与（仅当月）日均跑率月末预测。
- **年度销售趋势**：`build_yearly_sales_overview(year, …)`（`resolve_year` 负责年份夹取）按月聚合全年销售为 12 行明细 + 支付占比 + 年度合计，供 `record_view`（`/sales-records/`）在**无区间**时渲染 12 月柱状图与明细表（Sales Trend 已并入该页，`yearly_sales_view` / `/sales-trend/` 仅保留为旧链接的重定向）；利润对全年 sale_ids 做一次 FIFO 重放。
- **当日支付方式统计**：`dashboard_view` 在遍历当日订单时顺带按 `payment_method` 累加金额/件数，产出 `today_payment_breakdown`（实时，不走快照缓存）。
- **缓存（cheap win）**：视图层对月度快照与 MoM 对比按 `(month, sorted category ids, show_profit, period_end)` 维度做 60s `LocMemCache` 缓存；"今日操作"区块（含当日支付统计）仍每次实时计算，故缓存仅影响月度总览的新鲜度（≤60s），不影响当日订单。AR 汇总已由 Python 循环改为 DB `aggregate`，低库存列表补 `prefetch_related('images')` 消除 N+1。

### 5.8b 进货暂定→确认收货（Inbound pending receipt）
- **有供应商的入库 = 暂定订单**：`inbound_view` 对选了供应商的提交创建 `InboundOrder(status='pending_receipt')` + `InboundPendingItem` 行（**不建 `Purchase`、不产生库存**，`invoice_date` 缺省取当天）；无供应商则维持即时 `Purchase` 入库。
- **确认收货（弹窗）**：复核/编辑在 Inbound 页面的**每订单 modal** 中完成（`inbound_view` 用 `_build_pending_reviews()` 为每张暂定单预渲染 `InboundReceiveForm` + `InboundPendingFormSet`，含小缩略图）。modal 表单 POST 到 `inbound_receive_view`（`/inbound/<id>/receive/`，仅处理 POST）：`action=receive` 时在单一 `transaction.atomic` 内把 pending 行转为 `Purchase`（`remaining=quantity`，`date=now`）、置 `status='received'`/`received_at`、清空 pending 行；`action=save` 仅保存仍 pending；`action=cancel` 删除暂定单；GET 与校验失败均 `redirect('inbound')`。
- **待收货列表 + modal 均展示在 Inbound 页面**；供应商搜索（创建页与各 modal）由 `suppliers_autocomplete` API 驱动。缩略图统一用 app.css 的 `.cart-product*` 类（48–56px），避免无内联样式时图片占满全屏。

### 5.8 收银（Outbound POS）交互与支付模型
- **前端购物车为客户端数据模型**（`outbound.html` 内 JS：`cart[]` 数组），扫码/搜索命中后弹出行项目弹窗设置数量/价格（零售/批发切换）/固定额折扣/**支付方式**（3 圆球单选，记忆上次为默认；或 **Split this line** 在行内按金额拆分多方式，校验之和=该行小计），加入即为**独立一行**（同款不合并），每行可重开弹窗编辑或删除。折扣**计入折后单价**，不单独持久化。
- **支付按行选择（可行内拆分）、按方式汇总**：每行带自己的支付方式或拆分金额（`item.isSplit`/`item.paymentSplit`）；订单 Payments 明细 = 各行支付按方式聚合（前端 `linePaymentMap()`/`linePayments()`）。提交前要求每行支付完整（`linePayComplete()`）。
- **提交契约**：`items_json=[{barcode,qty,price(折后),discount,payment}]`（`payment`=该行主方式 `linePrimaryMethod()`）+ `payments_json=[{method,amount}]`（全单按方式汇总）。`outbound_view` 于单一 `transaction.atomic` 内 FIFO 扣减（`consume_stock_fifo`）、创建 `Sale`（`payment_method`=该行主方式）与 `SaleOrderPayment`（按方式汇总）。**向后兼容**：`outbound_view` 同时接受订单级 `payments_json`（按其汇总并校验总额一致）；缺省时回退按行 `payment` 聚合。

### 5.9 多店铺（Multi-store）
- **模型**：`Store`（core：`name`/`code`/`is_active`/`is_default`）+ `StoreProfile`（用户→home store 的 OneToOne）。`SaleOrder`/`Sale`/`ARInvoice` 新增可空 `store` FK（迁移 0028），迁移 0029 播种默认店铺（`code='MAIN'`）并回填全部历史销售/AR + 为所有已有用户建 `StoreProfile`。
- **共享 vs 按店铺**：库存（`Purchase`/总库存）、入库（`InboundOrder`）、供应商、产品档案**全店共享**；销售、AR、员工、报表**按店铺**。客户档案全局共享（NIF 全局唯一），但"该店铺客户"由销售归属派生（决策：shared customers, per-store sales）。
- **活动店铺解析**（`stock/stores.py`）：会话键 `active_store_id`。员工（非经理）**锁定**在 `StoreProfile.store`；经理/管理员可切换任意在营店铺或 **All stores**（聚合），未显式选择时**默认 All stores**。`resolve_active_store(request) -> (store|None, is_all)`；`store_for_new_sale(request)` 返回落账的具体店铺（All 时回退 home/默认店）；`scope_sales_by_store(qs, store, is_all)` 过滤（All 或 store 为 None 时不过滤）。
- **注入**：`stock/context_processors.py::store_context` 向所有模板暴露 `active_store`/`active_store_is_all`/`store_can_switch`/`available_stores`（在 settings 注册）；`base.html` 侧栏渲染店铺切换下拉（经理）或只读店名（员工）。切换端点 `set_active_store`（`stores/switch/`，仅经理，写会话后回退来源页）。
- **落账/过滤点**：出货 `outbound_view` 与订单修正 `save_sale_order_correction(store=...)` 写 `SaleOrder.store`+`Sale.store`（修正编辑时**保留原店铺**）；`ar_new_view` 写 `ARInvoice.store`。读侧按活动店铺过滤：销售记录 `record_view`（销售侧）、`ar_list_view`、员工列表 `employee_list_view`（按 `store_profile__store`），新建员工自动分配 `StoreProfile`。
- **读侧全量过滤（Phase 2 已完成）**：**仪表盘** `dashboard_view` + `services/dashboard.py`（`compute_period_headline`/`build_period_comparison`/`build_monthly_dashboard_snapshot`/`build_yearly_sales_overview` 均加 `store=None` 参数，经 `_apply_store()` 过滤销售与 AR；库存/采购/滞销分析保持全店共享；快照缓存键加入 store 维度）；**销售趋势**（并入 `record_view` 的无区间态）；**订单修正中心** `sale_order_correction_center_view` 列表；**客户详情** `customer_detail_view`（该客户的订单与 AR 按店铺）；**客户列表** `customer_search_view`（统计子查询按店铺 + 指定店铺时仅显示"本店客户"）；**考勤** `attendance_view` 团队区（按 `user__store_profile__store`）。所有过滤 All stores 时不生效。
- **店铺管理页**（Admin）：`store_list_view`/`store_create_view`/`store_edit_view`/`store_delete_view`（`/stores/*`，`@admin_required`）+ `StoreForm`，模板 `store_list.html`/`store_form.html`，侧栏 Admin 分组入口。保存时强制"恰有一个默认店铺"；删除仅在店铺无销售/发票/员工且非默认时允许。`Store`/`StoreProfile` 亦注册进 Django Admin。
- **打印小票抬头按店铺**：`PrintProfile` 加 `store` OneToOne（迁移 0030/0031：原单例抬头挂到默认店铺，其余店铺各建一份，从默认抬头 + 店名播种）。`PrintProfile.get_for_store(store)` 按店铺取/建；`get_solo()` 退化为默认店铺抬头。小票（`sale_order_detail`）用**该订单店铺**的抬头；抬头编辑页（`print_profile_edit_view`）编辑**当前活动店铺**的抬头（All stores 时编辑默认店铺）。
- **仍待接入**：按分类的销售目标 `SalesTarget` 改为按店铺（模型变更；当前仪表盘目标进度用店铺销售额对比全局分类目标，为已知局限）；员工创建/编辑表单增加显式店铺选择（当前新建按活动/默认店铺自动分配）。

### 5.10 门店可售分类（Store sellable categories）

库存、进货、供应商是全公司共享的；**卖什么**才是分门店的。
`Store.sellable_categories` (M2M → Category) 为空 = 该店销售全部分类。

| 门店 | 定位 | 可售 |
|------|------|------|
| Khan Perfume (90A) | 仓库 + 门店 | 全部（香水 + 手机配件/电子） |
| Scentory (SHOP2) | 纯香水店 | Perfumes |

生效范围由 `stock/stores.py::scope_products_by_store()` 统一实现，调用点：

- `product_list_view` —— 按当前门店过滤目录（All stores 不过滤）
- `check_barcode` / `products_autocomplete` —— 收银台查询默认按门店过滤

**例外：进货必须看到全部目录。** 库存是全公司共享的，Scentory 的账号收货时
也可能收到配件，所以 `inbound.html` 在请求上带 `?scope=stock`，
由 `views.py::_lookup_scope()` 放行。改动这两个端点时不要丢掉这个参数。

调整门店可售分类不需要改代码：Django admin → Store → Sellable categories。

### 5.11 无条码商品与内部条码（Internal barcodes）

手机壳、钢化膜、数据线大多**根本没有 EAN**，但 `Product.barcode` 是全系统的身份键：
收银台查询、Cloudinary `public_id`、Shopify variant SKU —— 全项目 122 处引用。
所以**不把它改成可空**，而是自己发号。

GS1 把 `02` 和 `20–29` 前缀留给店内自用，因此内部条码是一个**合法的 EAN-13**：
塞得进现有的 13 位字段、能被扫码枪读出、可以打印成货架标签，下游一行代码都不用改。

- 实现：`stock/services/barcodes.py`，前缀 `29` + 10 位流水 + 校验位。
- `Product.barcode_is_internal` 只用于 UI 区分号码来源，不参与任何业务逻辑。
- 表单留空即自动发号（`ProductForm.clean_barcode` + `save`）；**编辑时留空不会清掉已有条码**。
- SQLite 上 `select_for_update()` 是空操作，并发靠 unique 索引兜底：
  撞号就顺延取下一个（`assign_internal_barcode` 的重试循环）。

现有 238 条香水没有一条用 `29` 前缀，不存在冲突。

### 5.12 配件适配层（Devices / fitment）

配件不是按"它是什么"卖的，是按"它配什么"卖的。顾客说"15PM 的壳"，
每次说法还都不一样。三张表解决：

| 表 | 作用 | 例子 |
|---|---|---|
| `DeviceModel` | 一台真实机型 | Apple / iPhone 15 Pro Max |
| `DeviceAlias` | 同一台机器的其它写法（**数据，不是代码**） | `15PM` / `15 Pro Max` / `A2849` |
| `CompatibilityGroup` | 尺寸相同、可共用配件的一组机型 | "iPhone 15 Pro Max / 15 Plus" |

产品侧三个字段，`fits()` 里 **OR** 起来：

- `Product.universal_fit` —— 数据线、充电头、鼠标，配所有设备
- `Product.device_models` (M2M) —— 开模的壳，只配指定机型
- `Product.compatibility_groups` (M2M) —— 钢化膜之类，勾一个组等于勾了组里全部机型

**归一化**：`normalise_device_text()` 把 `iPhone 15 Pro Max` / `iphone-15 pro max` /
`IPHONE15PROMAX` 全部折成 `iphone15promax`。`DeviceModel.normalised` **只存机型名**，
因为店员打的是"iPhone 15 Pro Max"而不是"Apple iPhone 15 Pro Max"；
`resolve_device()` 会额外尝试**剥掉开头的品牌名**（品牌来自数据库，新增品牌不用改代码）。

`DeviceAlias.normalised` 上有 **unique 约束**：同一个简写不能指向两台机器，
否则收银台无从选择。

**收银台**：`products_autocomplete` 在常规文字匹配之外，会把查询词丢给
`search_devices()`；命中机型就把"配这台机器的产品"并进结果（`.distinct()` 去重）。
**通用商品故意不并进去** —— 否则店里每一根线都会出现在每一台手机的搜索结果里。

新机型上市 = admin 里加一行 DeviceModel + 几个别名，不需要改代码。

### 5.13 店铺自定义属性（Category attributes）

香水的容量/浓度/香型做成了真列。配件不能这么做：壳有类型（软胶/硬壳/翻盖/MagSafe），
膜有边型和胶型，下个月又是别的。每加一个字段就改一次 model + migration，不是店铺的工作方式。

所以**让店铺自己定义**：

| 表 | 作用 |
|---|---|
| `CategoryAttribute` | "配件有个字段叫 Case type，是选择型，且区分库存行" |
| `AttributeOption` | 这个字段的可选项 |
| `ProductAttributeValue` | 某个产品的答案，**分列存储**（数字存数字列，才能正确排序筛选） |

**沿分类树继承**：在 Accessories 上定义一次 Colour，下面所有子分类都有。
同一个 `code` **就近覆盖** —— 子分类可以重定义继承来的属性。
`attributes_for_category()` 带**环检测**（`Category.parent` 是普通 FK，没有任何东西阻止成环）。

**`variant_attribute` 是关键标志**：它标出"会让两个东西成为不同库存行"的属性。
黑色壳和透明壳是两个产品；同一个壳描述成"软触感"不是。
第 4 步的矩阵批量录入就读这个标志决定画哪个网格。

**表单**：每个分类的属性**全部渲染**，模板只显示当前分类的（沿用香水分组那套 `hidden` 做法）。
`save()` **只应用属于所选分类的属性** —— 藏起来的字段照样提交，绝不能让残留值覆盖东西
（改分类时旧分类的答案会被清掉）。**空答案不存行**："没填"和"填了否"是两回事。

**注意**：`{% render_field %}`（widget_tweaks）**不接受带过滤器的属性值**，
`id=x|add:"_id"` 会抛 `add requires 2 arguments`。用 `field.id_for_label`。
`manage.py check` 不编译模板，只有跑测试才会发现。

初始属性集：`python manage.py seed_accessory_attributes`（默认 dry-run，`--apply` 写入）。
可重复运行，不覆盖手工改过的属性。之后全部在 admin 里增改，不用改代码。

### 5.14 新增产品：先选分类，再按分类提问（Add-product wizard）

原来是一张 13 个字段的大表，香水和配件混在一起。现在分两步：

**第一步**：按钮选分类。顶级分类一排（Perfumes / Accessories / Shisha），
有子分类的（Accessories）点开第二排（Cases / Screen protectors / ... / Other accessories）。
没有子分类的一点即进表单。

**第二步**：表单只问这个分类该问的。

| 字段组 | 显示条件（`data-kind`） |
|---|---|
| Identity（条码/品牌/系列/名称） | always |
| Perfume（**性别** / 容量 / 浓度 / 香型 / inspired by） | `perfume` |
| Variant（Specification） | `general accessory` |
| ↳ Color（旧字段） | `general` —— 配件的颜色走自定义属性，否则会问两遍 |
| Fits（通用 / 机型 / 兼容组） | `accessory` |
| Specs（店铺自定义属性） | 按分类有没有定义 |
| Pricing / Description | always |

**判断依据是 `Category.form_kind`（数据），不是猜分类名。**
之前 `edit_product` 用 `/perfum/i.test(分类名)` 判断，新开一个"香水配件"分类就会误判。
`form_kind` 有三个值：`general` / `perfume` / `accessory`，
**沿父分类继承**（`effective_form_kind`）—— 在 Accessories 下新建"Tablet cases"自动就是配件表单。
在 admin 里改，不用改代码。

**注意**：隐藏的组**仍然在 DOM 里、仍然会提交**。Gender 现在放在 Perfume 组内，
新增产品没有旧值可丢所以安全；但**改这类结构时必须确认没有值会被静默清掉**
（历史教训：Gender 曾经只渲染在隐藏的香水组里，保存非香水时被清空）。
`add_product` / `edit_product` 都有测试断言每个字段只渲染一次。

表单校验失败重新渲染时，分类已经选好，页面**直接跳到第二步**，不用重选。

## 6. 前端架构

### 6.0 共享设计系统（`static/css/app.css`）
- **单一设计令牌来源**：`stock/static/css/app.css` 在 `:root` 定义规范化的"密集型 ERP"设计令牌（中性色板 + 蓝色强调色、surface/border/text、半径与阴影、状态色 ok/warn/danger）。`base.html` 在 `bootstrap.min.css` 之后加载它。
- **风格方向**：密集、数据导向——以 1px 细边框替代厚重阴影、较小圆角（`--app-radius:.625rem`）、紧凑内距、更高信息密度的表格。
- **演进背景**：此前 27 个模板各自内联 `<style>`，其中 8 个各自重定义了一套 `:root` 令牌（数值已轻微漂移，如页面背景 `#edf3f8` vs `#eef4f9`），导致跨页视觉不一致 + 27× 维护成本。`app.css` 将令牌与共享组件（页头、卡片、KPI/指标块、数据表、pill/badge、工具栏、低库存块、缩略图等）收敛为单一来源。
- **迁移策略（模块化、低风险）**：`app.css` 以各页**已有的类名**承载组件规则，并提供别名令牌（如 `--bg`/`--pl-*` → 规范令牌），因此迁移一个页面只需**删除其内联 `<style>` 里重定义的令牌**、保持 HTML 标记不变即可（页内令牌会覆盖 `app.css`：内联 `<style>` 在 body、晚于 head 的 app.css，同优先级后者生效，故必须删除页内重定义才能继承系统）。
- **令牌统一（2026-06）**：`app.css` 别名补齐 `--radius`/`--radius-sm`/`--radius-xs`/`--shadow-md`/`--success`/`--success-soft`，使更多页面可直接继承。
- **ui-ux-pro-max 优化（2026-06）**：按 `ui-ux-pro-max` 技能推荐（本产品=零售库存+POS→"Data-Dense Dashboard / industrial slate + stock green"）。曾试"库存绿"强调色，经用户确认**回退为蓝 `#2563eb`**（规范强调色保持蓝；`--app-accent-line #cbdbf4` 供 chip/pill 边框，新增 `--app-primary #334155` slate 令牌备用）。**保留的交互打磨**（与颜色无关）：`.btn-primary`/`.btn-outline-primary`/`.text-primary` 绑定到 `--app-accent`（主按钮=强调蓝，统一原生 BS `#0d6efd`）；`~160ms` 状态过渡；数据表 `tbody tr:hover` 行高亮；`button/summary/[role=button]/label[for]` `cursor:pointer`；`.btn:active` 轻微下压。新增基础层：`--font-display`（页面大标题用精致衬线，克制使用）、`--chart-1..5` 图表调色板令牌、`.num`/金额单元格 `tabular-nums`、全局 `:focus-visible` 焦点环、`prefers-reduced-motion` 降级、`img/svg max-width`、**移动端强化**（`≤576px`：输入框 16px 防 iOS 聚焦缩放、按钮 ≥44px 触控目标、更宽边距、`overflow-x` 防溢出）。
- **已统一到系统**：`base.html`/`dashboard.html`/`product_list.html`（早期迁移）+ `customer_detail`/`customer_search`/`sales_records`/`ar_detail`/`ar_list`/`product_detail`（删除页内令牌重定义）+ `supplier_*`/`store_*`/`print_profile_form`/`sale_order_correction_form`/`employee_*`（字面重阴影/近黑色/`1.1rem` 圆角对齐到 `var(--app-*)`）+ **`inbound.html`/`outbound.html`（POS）**（删除 `:root --ds-*` 重定义，改由 `app.css` 的 `--ds-*` 别名统一供给，强调色 `#3b82f6→#2563eb`；重阴影→令牌）。`catalog.html`（面向顾客）**独立编辑式身份**——不并入内部 ERP 密集系统：瓷白画布 + 墨黑字 + 单一酒红强调 + `--font-display` 衬线（hero/品牌名/区标题）与系统 sans（价格/元信息）对比；产品为"标本卡"（瓶身白底 + `drop-shadow` 产品投影），按品牌（house）编排为排版索引。
- **出货/POS 视觉-工艺重设计（2026-07，impeccable `shape`）**：`outbound.html` 内联 `<style>` 全量硬编码色收敛到 `app.css` 令牌（`--app-accent*`/`--app-surface-2`/`--app-border*`/语义 `--app-warn/danger/good` + `-soft`），移除装饰渐变（`.review-stat.highlight` 渐变→纯 `--app-accent-soft`）；确认按钮 `btn-danger`→`btn-primary`（红仅留给破坏性删行）；移除 chrome emoji；Review 侧栏改收据式（label/value 单行 + `tabular-nums` + 放大 Total 锚点）；`.operation-card` 圆角对齐 `--app-radius`、字重从铺满 `900` 收敛为层级；购物车 `thead` 改小号大写数据表头；新增 `.pos-title`（`--font-display`）。**仅视觉层，交互/数据契约/校验/弹窗不变**。项目根新增 `PRODUCT.md`（impeccable `init`：register=product、品牌人格 sharp·data-confident、anti-ref、5 条设计原则、WCAG AA）作为设计决策锚点。
- **图表调色板令牌化**：`--chart-1..5` 定为易读分类色（`#2563eb/#10b981/#f59e0b/#7c3aed/#64748b`）；`base.html` 头部一次性读入 `window.CHART_PALETTE`（含硬编码回退），支付占比 doughnut（`sales_records`/`customer_detail`）改读之。
- **仍待**：部分页面其余 `border-radius` 字面值（`1rem`/`.85rem` 等，卡片外元素）、更多 Chart.js 单序列颜色改读 `window.CHART_PALETTE`、彻底消除内联 `<style>`（把共享片段收敛为 `app.css` 组件）。

### 6.1 整体布局：左侧固定侧边栏（经典 ERP 布局）
- `base.html` 采用左侧固定侧边栏（`.erp-sidebar`，深色，宽 `--sidebar-w:248px`）+ 右侧内容区（`.erp-content`，`margin-left:248px`）的经典 ERP 布局，替代原先的顶部水平导航栏。
- 侧边栏结构：顶部品牌 → 中部分组导航（Dashboard / Operations / Catalog / Sales & Clients / Admin，分组用大写小标签 `.erp-nav-group`，条目 `.erp-nav-link`，当前页高亮 `.active`）→ 底部用户信息 + 退出按钮。角色门控保持不变（Suppliers 仅经理；Admin 分组仅超管）。
- 响应式：`≤991.98px` 时侧边栏变为离屏抽屉（`transform:translateX(-100%)`），由顶部细条 `.erp-topbar` 的汉堡按钮通过切换 `body.erp-nav-open` 类滑入，并叠加半透明 `.erp-backdrop` 遮罩点击关闭（纯 CSS + 一行内联 JS，无新增依赖）。
- 因内容区被侧边栏占去 248px，仪表盘网格断点相应上移（snapshot 5→3 列断点 `1400→1650px`，summary/watch 单列断点 `1199→1450px`），避免侧栏存在时 KPI 列过窄。
- 当前页判定仍用 `request.resolver_match.url_name`，逐条 `.erp-nav-link` 各自判定 `.active`。

- 所有页面继承 `stock/templates/stock/base.html`：左侧固定侧边栏（深色品牌头部）、按角色动态显示菜单项、移动端离屏抽屉。
- 进货/出货页为"购物车式"交互：JS 维护本地商品列表，提交时序列化为 `items_json` 一并 POST。
- 扫码识别：`html5-qrcode.min.js` 调用摄像头扫码 → 调用 `check_barcode` API 联想填充商品信息。
- 自动补全：客户（`customers_autocomplete`）、产品（`products_autocomplete`）均为简单 JSON API + 前端下拉建议。
- 模板自定义过滤器：`math_extras.py` 提供 `mul`/`sum_list`/`map_attr`，用于模板中做轻量聚合计算（避免在模板里写复杂表达式）。
- **图表（Chart.js）**：可视化页面通过 CDN `chart.js` 渲染。视图把数据预聚合为纯 Python 列表（金额转 `float`），用 Django `{{ data|json_script:"id" }}` 安全嵌入页面，前端 `JSON.parse(...textContent)` 后 `new Chart(...)`。采用页面：`dashboard.html`（月度趋势）、`customer_detail.html`（月度消费 bar + 支付占比 doughnut）、`customer_search.html`（活跃度 doughnut）、`sales_records.html`（**合并了原 Sales Trend**：无区间时显示年度 12 月柱状图 + 月度明细表 + 支付占比；选定区间时显示每日 money in/out 柱 + 利润折线、支付 doughnut、Top Products 条）。含金额的图表受 `has_order_reconciliation_access`/`has_sales_sensitive_access`/`is_superuser` 等与页面金额一致的门控才输出数据与 `<canvas>`。

---

## 7. 测试架构

唯一测试文件 `stock/tests.py`（约 3100 行），基于 Django `TestCase` + 测试数据库（SQLite），共 **147 个测试**、28 个测试类：

| 领域 | 测试类 |
|---|---|
| 汇总/信号 | `SummaryRebuildTests` |
| 出货与库存 | `WorkflowRegressionTests`（出货、CSRF、调整权限）、`InventoryAdjustmentSyncTests`、`InboundOutboundPageTests` |
| 销售与客户 | `SalesRecordsViewTests`、`CustomerDetailViewTests` |
| 仪表盘 | `DashboardViewTests`、`DashboardEnhancementTests`（MoM、销售目标进度） |
| 产品 | `ProductArchitectureTests`（表单/品牌系列/Shopify CSV 导出）、`UploadPathTests` |
| 订单修正 | `SaleOrderCorrectionTests`（创建/编辑/删除 + 库存联动 + 审计）、`CorrectionStoreChange{Service,View}Tests`、`AffectsStock{Model,Service,Profit,View}Tests` |
| 多店铺 | `MultiStoreTests` |
| 供应商/HR | `SupplierManagementTests`、`EmployeeManagementTests`、`AttendanceManagementTests` |
| Shopify | `ShopifySyncTests`、`ShopifyCsvCloudinaryImageTests` |
| Cloudinary | `Cloudinary{Client,Sync,Signal,Command,ImageUrl}Tests` |

约定：

- 外部集成（Shopify / Cloudinary）**全部 mock**，测试不发真实网络请求、不读密钥。
- 涉及 Cloudinary 的测试用 `@override_settings` 钉死 `CLOUDINARY_CLOUD_NAME`/`CLOUDINARY_AUTO_SYNC`，**不依赖本机 `.env`**（否则结果会因机器而异）。
- 大量使用 `self.captureOnCommitCallbacks(execute=True)` 同步触发 `transaction.on_commit` 回调（验证信号驱动的汇总重算）。
- 跑测试用**系统 Python**或便携版 Python，`.venv` 没装 Django（见 [STATUS.md](./STATUS.md) 第 3 节）。

---

## 8. 模块化单体路线图（Modular Monolith Roadmap）

目标：在**不拆分为多个 Django app**（保持单一迁移历史、单一 `INSTALLED_APPS` 条目、`{% url %}` 名称全部不变）的前提下，把 `views/forms/admin/urls/templates/tests` 按与模型相同的域边界重组为子包，使代码结构与下方依赖图对齐。

**域依赖图（无环 DAG）**：
```
core         — permissions.py, PrintProfile, Store, StoreProfile, stores.py, context_processors.py, base.html, 模板标签, 认证路由（所有域依赖它）
catalog      — Category, Brand, ProductSeries, Product, ProductImage
partners     — Customer, Supplier（→ catalog：Supplier.product_types）
inventory    — Purchase, InboundOrder, InboundPendingItem, StockAdjustmentLog, stock_ops.py（→ catalog, partners）
sales        — SaleOrder, Sale, SaleOrderChangeLog, order_corrections.py（→ catalog, partners, inventory）
finance      — ARInvoice, ARItem, ARPayment（→ partners）
hr           — AttendanceRecord（→ core）
reporting    — DailySalesSummary, dashboard.py, profit.py, signals.py（→ sales, inventory, finance, catalog）
```

**分阶段实施**（每阶段独立提交，前置条件：52/52 测试通过 + `makemigrations --check --dry-run` 无变化）：

| 阶段 | 内容 | 风险 | 状态 |
|---|---|---|---|
| 1 | `models/` 拆包（跨域外键改字符串引用） | 低 | ✅ 已完成 |
| 2 | `admin/` 拆包（域子模块 + `__init__` 汇总注册） | 低 | ⬜ |
| 3 | `forms/` 拆包（同 re-export 模式） | 低 | ⬜ |
| 4 | `hr` 视图/模板/测试抽出（最独立，先行） | 低 | ⬜ |
| 5 | `finance`（AR）视图/模板/测试抽出 | 低 | ⬜ |
| 6 | `partners`（客户 + 供应商）抽出 | 中 | ⬜ |
| 7 | `catalog`（hub，含 Shopify/标签等共享 helper 下沉 `services/catalog.py`） | 中 | ⬜ |
| 8 | `inventory`（进货/批次/库存调整） | 中 | ⬜ |
| 9 | `sales`（出货/订单修正/订单详情） | 中 | ⬜ |
| 10 | `reporting`（dashboard / record_view / signals，牵涉面最广，最后做） | 中 | ⬜ |
| 11 | `tests.py` 按域拆为 `tests/test_<domain>.py`（可随各阶段并行） | 低 | ⬜ |

`urls.py` 最终形态为各域 `urlpatterns` 列表拼接（**不引入 namespace**），因此模板中的 `{% url '...' %}` 标签无需改动。该路线图不阻断未来若需升级为真正的多 app 拆分——届时这些子包边界即为天然的 app 边界。
