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
