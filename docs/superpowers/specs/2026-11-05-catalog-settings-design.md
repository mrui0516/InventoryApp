# 产品目录设置（Catalog）与 Mix 产品设计

> 日期：2026-11-05。状态：等待确认，尚未开始实现。

## 1. 为什么做

现在产品的基础设置大多要改代码或进 Django 后台：Type 写死了 7 个，增减都要改代码；品牌和系列没有管理页面；
只有浓度等少数表能在后台改。店主希望这些都能自己在 Settings 里设置。

同时，Mix 产品（比如 Lattafa Mix Body Spray）一个产品代表很多款，客人看 PDF 不知道有哪几款。
店主不想为每款单独建产品、传照片、算库存，只想列出名称，并能和对应香水互相跳转。

**成功的样子：** 新增一个 Type（比如 Creme）、换品牌 logo、合并重复品牌都不用找开发。
发给客人的 PDF 能看到 Body Spray 有哪几款，点一下就跳到对应香水。

## 2. 范围

### 要做

1. **Settings → Catalog**：管理 Types、Brands、Lines、Categories、Concentrations、Fragrance families。
2. **Type 由表驱动**，代码里写死的规则改成每个 Type 的开关。
3. **品牌 logo**：上传图片，全系统共用一个显示组件。
4. **添加产品先选 Type**，再填对应的表单。
5. **Mix 产品的款式清单**，库存仍然统一。
6. **客户 PDF 内部跳转**：香水和 Mix 互相跳转。

### 不做

- 多公司 SaaS 账号体系。
- 自定义产品字段，或可视化设计表单。
- 按款式记库存、收银时选款式、按款式统计销售。卖完了也不用管，下单时检查的是 Mix 的总库存。
- 单独的 Body spray（比如 Club De Nuit）和香水的连接，第一版只做 Mix。
- Excel 导出的改动，链接和款式只进 PDF。
- Sell 页面显示连接。
- 改动 Shopify 的同步逻辑（见第 9 节）。

## 3. Type

新表 `ProductType`，取代 `Product.PERFUME_TYPES`。

| 字段 | 含义 |
|---|---|
| `code` | 系统内部编号，创建时生成，之后不变，改名不影响它 |
| `name` | 显示名，比如 Body mist / splash |
| `name_word` | 产品名称里显示的词，比如 Body mist；留空就不显示 |
| `customer_label` | 客户 PDF 上的葡语名称，比如 Ambientador；留空用 `name` |
| `default_category` | 这个 Type 的产品进哪个分类，必填 |
| `takes_concentration` | 要不要选浓度 |
| `wholesale_discount` | 能不能打批发折扣 |
| `is_gift` | 是不是礼盒，Quick import 用来区分同名产品 |
| `shows_scent_details` | 表单是否显示香调、香调层次、灵感来源、性别这一块 |
| `sort_order` | 排序 |

**默认分类很重要。** 自动定价和批发折扣都看产品是否在 Perfumes 分类。
所以 Creme 放进 Perfumes 就会被自动调价；放进新分类就不会。Type 页面要把这一点写在分类选项旁边。

**初始数据**（保持现有行为不变）：

| Type | 浓度 | 批发折扣 | 礼盒 | 香调信息 |
|---|---|---|---|---|
| Perfume | 是 | 是 | 否 | 是 |
| Mini | 是 | 是 | 否 | 是 |
| Oil perfume | 否 | 是 | 否 | 是 |
| Body mist / splash | 否 | 否 | 否 | 是 |
| Body spray / Deo | 否 | 是 | 否 | 是 |
| Air freshener | 否 | 是 | 否 | 否 |
| Coffret | 否 | 是 | 是 | 是 |

批发折扣开关和现有的 60–120ml、Perfumes 分类规则是“并且”的关系，不取代它们。

新建 Type 时批发折扣默认关闭：漏打折一眼能看出来，多打折会悄悄损失利润。

**产品关联**：产品用外键指向 Type。
需要替换的地方：名称（`type_label`、`list_name`）、产品表单和预览脚本、列表标签和筛选、产品详情页、
`ProductForm.clean`、`quick_import`、`wholesale_discount`、`tidy_product_names`。

**删除 Type**：
- 有产品（包括已归档产品）时，必须先选另一个 Type 把产品转过去。
- Perfume 不能删，它是默认值，可以改名。

## 4. 添加产品

- `/add-product/` 先显示 Type 卡片，选中后进入 `/add-product/?type=<code>`。
- 表单按 Type 开关显示字段，标题是 “Add <Type>”，分类由 `default_category` 自动带上，不再让人选。
- 表单上可以返回重新选 Type。保存失败时留在原 Type，填的内容不丢。
- 编辑产品时可以改 Type。切到不需要浓度的 Type 时清空浓度；香调信息只隐藏，不清空。

## 5. Settings → Catalog

放在 Settings 菜单，是一个页面加几个标签。权限和 Stores、Shop settings 一样，只有管理员能用。
手机和桌面都要能用，列表用现有的紧凑表格样式。

| 标签 | 能做什么 | 删除规则 |
|---|---|---|
| **Types** | 添加、改名、排序、改开关，显示产品数 | 见第 3 节 |
| **Brands** | 添加、改名、合并、传 logo，显示产品数 | 没有产品才能删 |
| **Lines** | 按品牌查看，改名、合并 | 没有产品才能删 |
| **Categories** | 添加、改名、Shopify 开关，显示产品数 | 没有产品、没有子分类、不是任何 Type 的默认分类才能删 |
| **Concentrations** | 添加、改名、缩写、Shopify 标签、排序 | 在用时不能删，显示有多少产品在用 |
| **Fragrance families** | 添加、改名、排序 | 在用时不能删 |

### 改名和合并

产品上另外存了品牌和系列的文字（`Product.brand`、`Product.model`），报表和名称都读它，
所以改名要在同一个事务里把在售和已归档产品的文字一起更新。

合并时要二次确认，并显示会影响多少产品。合并逻辑复用 `tidy_product_names` 里已有的做法，
抽成共用的 service；同名系列要合并，不同名的移到保留的品牌下。

自动删除空品牌的规则（`catalog_prune`）增加一条例外：**有 logo 的品牌不自动删除**，否则 logo 会丢。
在 Brands 新增的品牌可以先传 logo、之后再加产品；没有 logo 也没有产品的品牌，下次跑 `prune_empty_brands` 时会被清理。

## 6. 品牌 logo

- `Brand.logo`：接受 JPG、PNG、WebP，不超过 2 MB，保存时缩到最长边 256px。不接受 SVG，它可以夹带脚本。
- 文件存在 `media/brand_logos/`。
- 共用模板组件 `{% brand_logo brand size %}`，没有 logo 时显示品牌首字母。
- 产品已归档时品牌外键可能为空，这时直接显示文字，不报错。
- 第一批显示位置：Settings Brands、产品详情页、Products 的品牌筛选、客户 PDF 的品牌标题。
  其它页面之后一行模板就能加。

## 7. Mix 产品

- `Product.is_mix`：表单上的 **This is a mix** 勾选项，所有 Type 都能用。
- 新表 `MixScent`：`mix`（所属产品）、`name`（款式名）、`linked`（连接的产品，可空）、`sort_order`。
- 勾选后表单显示款式清单：
  - 输入时用现有的 `products_autocomplete` 提示，同品牌排在前面，已是 Mix 的产品不提示。
  - 选中就连接，不选就只保存名称。
  - 可以一次粘贴多行或逗号分隔的名称，同品牌下名称完全相同的自动连接，其余只存名称。
- **显示名称**：有连接且对方在售时，用对方当前的系列和名称（不带品牌、浓度、容量）；否则用保存的 `name`。
  保存 Mix 时把名称写回 `name`。这样香水改名会跟着变，香水删除后款式名称还在。
- **库存、进货、销售、定价**：和普通产品完全一样，不看款式。
- **产品详情页**：Mix 页列出款式，连接的可以点进去；香水页显示 “Also in: Body Spray (Mix)”，并显示 Mix 的库存。
  已归档的 Mix 不显示。

**上线时**：系列名为 Mix 的产品（不分大小写）自动勾选 `is_mix`，款式清单留空，由店主自己填。

## 8. 客户 PDF

只改 `catalog_pdf.build_catalog_pdf`。可行性已用一次性测试验证：跨页双向链接能正确生成。

- 每个产品行加锚点 `p<id>`。
- 香水行下方小字：`Também em: Body spray · Ambientador`，每项跳到对应 Mix，名称用 `customer_label`。
- Mix 行下方小字：`Fragrâncias: Khamrah · Yara · Asad`，有连接的可点，其余是普通文字。款式很多时自动换行。
- 空的 Mix 清单不显示这一行。
- **对方不在这份 PDF 里就不加链接**，只显示文字。这是必须的：指向不存在的目标时 reportlab 会报错，导出直接失败。
  实现时先收集这份 PDF 里的全部产品 id，再决定每项要不要链接。
- 葡语文字要确认 PDF 字体能显示 `ã`、`â`、`ç`。

**待验证：** 手机上的 PDF 阅读器（包括 WhatsApp 内置预览）能不能点。不能点时只是一行普通文字，内容不受影响。

## 9. 约束

- **Shopify**：现有同步只更新描述和图片，不改标题和品牌名。
  所以品牌改名后，app 里全部更新，已上架的 Shopify 商品保持原样，和现在编辑产品名的结果一样。
  另一个工作会话正在修改 Shopify 相关文件，这次不碰。
- **备份**：`backup_db` 只备份数据库，logo 和产品图片一样不在备份内。
- **线上数据**：迁移要备份后运行。上线后不需要人工转换数据。
- **本地演示库是旧副本**，具体产品要以线上为准。

## 10. 分阶段

每阶段都能单独上线，并跑完整测试：

1. **Type 表**：`ProductType`、数据迁移、替换写死的规则、Settings 的 Types 标签。上线后行为不变，Creme 可以自己加。
2. **添加产品先选 Type**：卡片页、按开关显示表单。
3. **Catalog 其余标签和 logo**：Brands、Lines、Categories、Concentrations、Fragrance families、合并、logo 组件。
4. **Mix**：`is_mix`、`MixScent`、表单、详情页。
5. **PDF 跳转**。

## 11. 替店主做的决定（可以改）

- Catalog 只有管理员能用，普通经理和员工不能用。
- 新 Type 的批发折扣默认关闭。
- Air freshener 默认不显示香调信息。
- 有 logo 的品牌不自动删除。
- 现有 Mix 产品的识别：系列名为 Mix 的自动勾选，不看产品名。
- 第一版 logo 显示在第 6 节列的四处。

## 12. 留给实现者

- 外键的具体做法、迁移拆分、标签页的路由结构。
- 款式清单的前端写法。
- 排序用数字还是上下按钮。
- 每阶段的测试组织方式，但必须覆盖：迁移前后行为不变、改名和合并同时更新已归档产品、PDF 目标缺失时仍能生成。
