# 产品目录设置与 Mix 连接 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 店主在 Settings → Catalog 自己管理 Type、品牌、系列、分类、浓度和香调；添加产品先选 Type；Mix、Coffret、Body mist、Body spray、Air freshener 连接到产品，并在客户 PDF 里互相跳转。

**Architecture:** `ProductType` 表取代写死的 `PERFUME_TYPES`，规则变成每个 Type 的开关，名称显示走进程内短缓存，避免逐行查询。管理页集中在新文件 `stock/catalog_views.py`，改名和合并在 `services/catalog_admin.py`，连接在 `services/product_contents.py`。

**Tech Stack:** Django 5.2、SQLite、服务端模板、Bootstrap、Pillow、reportlab。

**Spec:** `docs/superpowers/specs/2026-11-05-catalog-settings-design.md`

## Global Constraints

- 界面文字用英文；客户 PDF 用欧洲葡萄牙语；回复和文档用中文。
- 模板不加介绍性段落：标题、标签、数据、警告。字号不小于 `.85rem`，列表用 Orders 的紧凑行样式，弹窗必须放得下屏幕。
- 不碰 Shopify 文件：`shopify_client.py`、`shopify_push.py`、`shopify_sync.py`，以及主工作区里另一个会话的未提交文件。已确认那些文件没有用 `perfume_type`。
- 在干净克隆 `merge_repo` 里工作，分支 `catalog-settings`。CSS 或 JS 放进 `stock/static` 时，部署命令要加 `collectstatic`；模板内联样式不用。
- 迁移编号从 0075 开始。每次写迁移前先 `git fetch`，确认没有新迁移。
- 破坏性数据改动要先备份。
- 提交信息结尾：`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。

**每个阶段结束都要做**（下文称“阶段收尾”）：

1. 更新 `docs/PRD.md`、`docs/ARCHITECTURE.md`、`docs/STATUS.md`（最新迁移、测试数、一行更新记录）。
2. 手机（390px）和桌面（1366px）都截图看过，右边缘用元素位置量，不看 `scrollWidth`。演示服务器要重启，先用 `netstat` 确认 8765 只有一个监听进程。
3. `git archive HEAD` 导出，复制 `.env`，跑 `manage.py test stock restock_alerts` 和 `makemigrations --check`。
4. 快进合并到 master 并推送，把 PythonAnywhere 命令发给店主：备份、`git pull --ff-only && migrate`、Reload。

## Review Focus

1. 品牌改名成已存在的名字（只差大小写也算）：不能报 500，提示用合并。→ Task 6
2. 上传改了扩展名的非图片，或超过 2 MB 的 logo：显示错误，不产生文件。→ Task 9
3. 删除 Type 时选自己当目标，或目标已被删除：拒绝，产品不动。→ Task 3
4. 粘贴款式含空行、重复名称、前后空格：去重，顺序不乱。→ Task 10
5. 连接的香水已归档，或不在这份 PDF：只显示文字，导出不失败。→ Task 13

---

## 阶段 1：Type 表

### Task 1: `ProductType` 表与产品外键

**Files:**
- Create: `stock/models/product_types.py`、`stock/migrations/0075_product_type_table.py`、`stock/test_product_type_table.py`
- Modify: `stock/models/catalog.py`、`stock/models/__init__.py`、`stock/forms.py`、`stock/views.py:1328-1333,1499-1503`、`stock/services/quick_import.py:212,254`、`stock/management/commands/tidy_product_names.py`、`stock/templates/stock/product_detail.html:93`、`stock/templates/stock/_product_fields.html:94-95,221-224`、`stock/tests.py:6616`、`stock/test_product_types.py`、`stock/test_product_kind.py`、`stock/test_catalog_prune.py`

**Interfaces:**
- Produces:
  - `ProductType`：`code`（slug，unique，max 16）、`name`、`name_word`、`customer_label`、`default_category`（FK Category，null，PROTECT）、`takes_concentration`、`takes_volume`、`wholesale_discount`、`is_gift`、`has_contents`、`shows_scent_details`、`sort_order`。排序 `['sort_order', 'name']`。
  - `Product.product_type`：`ForeignKey(ProductType, to_field='code', default='perfume', on_delete=PROTECT, related_name='products')`。原始值用 `product.product_type_id`，字符串筛选 `filter(product_type='coffret')` 仍然可用。
  - `ProductType.for_code(code: str) -> ProductType`、`ProductType.all_cached() -> list[ProductType]`、`clear_type_cache() -> None`。
  - `Product.type_info`（property）`-> ProductType`。
  - `Product` 上的常量 `PERFUME`、`BODY_SPRAY` 等保留；`PERFUME_TYPES`、`TAKES_CONCENTRATION` 删除。

- [ ] **Step 1: 写失败测试** `stock/test_product_type_table.py`

```python
class ProductTypeTableTests(TestCase):
    def test_the_seven_types_are_seeded_in_order(self):
        self.assertEqual([t.code for t in ProductType.objects.all()],
                         ["perfume", "mini", "oil_perfume", "body_mist", "body_spray", "air_freshener", "coffret"])

    def test_the_seeded_switches(self):
        rows = {t.code: t for t in ProductType.objects.all()}
        self.assertEqual({c for c, t in rows.items() if t.takes_concentration}, {"perfume", "mini"})
        self.assertEqual({c for c, t in rows.items() if t.wholesale_discount}, {"perfume"})
        self.assertEqual({c for c, t in rows.items() if t.is_gift}, {"coffret"})
        no_scent = {"body_mist", "body_spray", "air_freshener", "coffret"}
        self.assertEqual({c for c, t in rows.items() if t.has_contents}, no_scent)
        self.assertEqual({c for c, t in rows.items() if not t.shows_scent_details}, no_scent)
        self.assertTrue(all(t.takes_volume for t in rows.values()))
        self.assertEqual(rows["perfume"].name_word, "")
        self.assertEqual(rows["body_mist"].name_word, "Body mist")
        self.assertEqual(rows["air_freshener"].customer_label, "Ambientador")

    def test_names_read_the_table(self):
        product = make_product(product_type_id="coffret", name="Yara")
        self.assertEqual(product.type_label, "Coffret")
        self.assertIn("Coffret", product.display_name)
        ProductType.objects.filter(code="coffret").update(name_word="Gift set")
        clear_type_cache()
        self.addCleanup(clear_type_cache)
        product.refresh_from_db()
        self.assertEqual(product.type_label, "Gift set")

    def test_a_page_of_names_costs_one_query(self):
        for n in range(5):
            make_product(barcode=f"629000000770{n}", product_type_id="body_spray")
        clear_type_cache()
        products = list(Product.objects.all())
        with self.assertNumQueries(1):
            [p.display_name for p in products]

    def test_saving_a_type_refreshes_the_names_at_once(self):
        row = ProductType.objects.get(code="mini")
        self.addCleanup(clear_type_cache)
        ProductType.for_code("mini")
        row.name_word = "Miniature"
        row.save()
        self.assertEqual(ProductType.for_code("mini").name_word, "Miniature")
```

`make_product(**kw)` 是这个文件里的小工具，建 Perfumes 分类的产品。

- [ ] **Step 2: 运行，确认失败**

Run: `python manage.py test stock.test_product_type_table --noinput`
Expected: ImportError，`ProductType` 不存在。

- [ ] **Step 3: 实现模型和缓存**

`product_types.py` 里用模块级 `(过期时间, {code: row})`，用 `time.monotonic()` 判断，有效期 30 秒。`post_save`、`post_delete` 调 `clear_type_cache()`。缓存只保存行对象，不缓存 `default_category`。

`catalog.py`：`type_label` 改为 `type_info.name_word` 或浓度缩写；`display_name` 的 `kind` 改为 `type_info.name_word`；`list_name` 同样。

- [ ] **Step 4: 写迁移 0075**

顺序：`CreateModel` → `RunPython(seed, noop)` → `RenameField(perfume_type → product_type)` → `AlterField` 成外键。

seed 的值：

| code | name | name_word | customer_label |
|---|---|---|---|
| perfume | Perfume | 空 | Perfume |
| mini | Mini | Mini | Mini |
| oil_perfume | Oil perfume | Oil perfume | Óleo de perfume |
| body_mist | Body mist / splash | Body mist | Body mist |
| body_spray | Body spray / Deo | Body spray | Body spray |
| air_freshener | Air freshener | Air freshener | Ambientador |
| coffret | Coffret (gift set) | Coffret | Coffret |

开关按 spec 第 3 节的表，但这一阶段 Coffret 的 `takes_volume=True`。`default_category` 取 `Category.objects.filter(parent=None, name__iexact='Perfumes').first()`，没有就留空。`sort_order` 10、20、30…

- [ ] **Step 5: 替换所有用法**

- `forms.py`：字段改成 `product_type`，`ModelChoiceField(ProductType.objects.all(), to_field_name='code', required=False)`，为空时用 `ProductType.for_code('perfume')`。`clean()` 用 `not type.takes_concentration` 判断。
- `views.py`：`type_tabs` 遍历 `ProductType.all_cached()`，`label` 用 `t.name_word or t.name`；筛选判断 `perfume_type in codes`。URL 参数 `?type=` 不变。
- 产品表单传 `type_rules_json`：`{code: {word, conc, volume, scent}}`，用 `json_script` 输出 `pfTypeRules`，替换预览里的 `takesConc` 和 Deo 正则。
- `quick_import.py`：`.only(...)` 改 `product_type`，判断改 `p.type_info.is_gift`。
- `tidy_product_names.py`：`perfume_type` 改 `product_type_id`，类型名称从 `ProductType.for_code` 取。
- `product_detail.html:93`：显示 `product.type_info.name`，默认 Type 不显示。
- `stock/tests.py:6616`：类上加 `serialized_rollback = True`。
- 测试里的 `perfume_type=` 改 `product_type_id=`，POST 字段改 `product_type`。`test_the_list_offers_a_tab_per_new_type` 里 Body mist 的标签改成 `Body mist`。

- [ ] **Step 6: 确认没有遗漏**

Run: `grep -rn "perfume_type\|PERFUME_TYPES\|TAKES_CONCENTRATION" stock restock_alerts --include=*.py --include=*.html | grep -v migrations`
Expected: 只剩 `views.py` 里 `perfume_type` 这个局部变量和模板上下文名。

- [ ] **Step 7: 运行相关测试**

Run: `python manage.py test stock.test_product_type_table stock.test_product_types stock.test_product_kind stock.test_catalog_prune --noinput`
Expected: OK。

- [ ] **Step 8: 提交** `feat: product types live in a table`

### Task 2: 批发折扣读开关

**Files:**
- Modify: `stock/services/wholesale_discount.py:24-41`、`docs/ARCHITECTURE.md`（5.15b）
- Test: `stock/test_product_type_table.py`

**Interfaces:**
- Consumes: `Product.type_info`
- Produces: `discount_applies(product) -> bool` 签名不变，导出和 Pedido 自动生效。

- [ ] **Step 1: 写失败测试**

```python
class DiscountSwitchTests(TestCase):
    def test_only_perfume(self):
        cases = (("perfume", True), ("coffret", False), ("mini", False), ("oil_perfume", False),
                 ("body_mist", False), ("body_spray", False), ("air_freshener", False))
        for n, (code, expected) in enumerate(cases):
            product = make_product(barcode=f"629000007000{n}", product_type_id=code, volume_ml=100)
            self.assertEqual(discount_applies(product), expected, code)

    def test_a_perfume_still_needs_the_size(self):
        for volume, expected in ((50, False), (60, True), (120, True), (150, False), (None, False)):
            product = make_product(barcode=f"6290000071{volume or 0:03d}", volume_ml=volume)
            self.assertEqual(discount_applies(product), expected, volume)
```

- [ ] **Step 2: 运行，确认第一个失败**

Run: `python manage.py test stock.test_product_type_table.DiscountSwitchTests --noinput`
Expected: Mini 等断言失败。

- [ ] **Step 3: 实现**

规则按 spec 第 3 节：分类、`wholesale_discount`、容量 60–120ml。删除 body_mist 的判断。更新 ARCHITECTURE 5.15b 的规则描述。
`test_product_types.py` 里 `test_a_body_mist_gets_no_wholesale_discount_at_any_size` 对 Oil perfume 的断言改成 `assertFalse`。

- [ ] **Step 4: 运行折扣相关测试**

Run: `python manage.py test stock.test_product_type_table stock.test_product_types stock.tests.WholesaleDiscountTests --noinput`（类名用 `grep -n "discount_applies" stock/tests.py` 找到所属类）
Expected: OK。

- [ ] **Step 5: 提交** `feat: wholesale discount follows the type, perfume only`

### Task 3: Settings → Catalog 与 Types 标签

**Files:**
- Create: `stock/catalog_views.py`、`stock/services/catalog_admin.py`、`stock/templates/stock/catalog_base.html`、`stock/templates/stock/catalog_types.html`、`stock/test_catalog_settings.py`
- Modify: `stock/urls.py`、`stock/templates/stock/base.html:138-150`

**Interfaces:**
- Produces:
  - 路由：`settings/catalog/`（重定向到 types，名 `catalog_settings`）、`settings/catalog/types/`（`catalog_types`，GET 列表，POST `action=add|save|delete`）。全部 `@login_required @admin_required`。
  - `catalog_base.html`：标签导航，子页 `{% block catalog %}`。后续标签都继承它。
  - `catalog_admin.delete_type(row: ProductType, move_to: ProductType | None) -> int`：返回转移的产品数，不合法时抛 `ValueError`。
  - `catalog_admin.new_type_code(name: str) -> str`：`slugify` 转下划线，截到 16 位，重复时加数字。

- [ ] **Step 1: 写失败测试**

```python
class TypesTabTests(CatalogMixin):
    def test_only_an_admin_gets_in(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("catalog_types")).status_code, 302)

    def test_adding_creme_needs_no_code_and_starts_without_discount(self):
        self._post(action="add", name="Creme", name_word="Creme", default_category=self.perfumes.id)
        row = ProductType.objects.get(name="Creme")
        self.assertEqual(row.code, "creme")
        self.assertFalse(row.wholesale_discount)
        self.assertFalse(row.takes_concentration)
        self.assertTrue(row.takes_volume)
        self.assertIn("Creme", self.client.get(reverse("add_product")).content.decode())

    def test_a_new_type_needs_a_category(self):
        response = self._post(action="add", name="Creme", name_word="Creme")
        self.assertFalse(ProductType.objects.filter(name="Creme").exists())
        self.assertContains(response, "Choose a category")

    def test_renaming_keeps_the_code(self):
        row = ProductType.objects.get(code="body_spray")
        self._post(action="save", id=row.id, name="Deo", name_word="Deo", default_category=self.perfumes.id,
                   takes_volume="on", shows_scent_details="on")
        row.refresh_from_db()
        self.assertEqual((row.code, row.name), ("body_spray", "Deo"))

    def test_perfume_cannot_be_deleted(self):
        row = ProductType.objects.get(code="perfume")
        self._post(action="delete", id=row.id)
        self.assertTrue(ProductType.objects.filter(code="perfume").exists())

    def test_deleting_a_type_in_use_moves_live_and_archived_products(self):
        row = ProductType.objects.get(code="mini")
        live = make_product(barcode="6290000072001", product_type_id="mini")
        gone = make_product(barcode="6290000072002", product_type_id="mini", archived_at=timezone.now())
        self._post(action="delete", id=row.id, move_to=ProductType.objects.get(code="perfume").id)
        self.assertFalse(ProductType.objects.filter(code="mini").exists())
        self.assertEqual(Product.all_objects.get(pk=gone.pk).product_type_id, "perfume")
        self.assertEqual(Product.objects.get(pk=live.pk).product_type_id, "perfume")

    def test_a_type_in_use_cannot_go_without_a_target_or_to_itself(self):
        row = ProductType.objects.get(code="mini")
        make_product(product_type_id="mini")
        for target in (None, row.id):
            self._post(action="delete", id=row.id, move_to=target or "")
            self.assertTrue(ProductType.objects.filter(code="mini").exists())
```

`CatalogMixin` 建 `self.boss`（超级用户）、`self.manager`、`self.perfumes`，并 `addCleanup(clear_type_cache)`；`_post(**data)` 以 boss 身份 POST 到 `catalog_types`。

- [ ] **Step 2: 运行，确认失败**

Run: `python manage.py test stock.test_catalog_settings --noinput`
Expected: 路由不存在。

- [ ] **Step 3: 实现视图和 service**

新 Type 默认：`takes_concentration=False`、`takes_volume=True`、`wholesale_discount=False`、`shows_scent_details=True`，`sort_order` 为现有最大值加 10。排序用数字输入，`has_contents` 这个开关到 Task 11 才放进表单。

- [ ] **Step 4: 写模板**

每行：名称、产品数、四个开关的状态。点行打开侧边面板（`data-layer`），编辑放在面板里。分类选项下方写一行：`Perfumes: auto pricing and wholesale discount apply.`。删除有产品的 Type 时，面板显示 `move_to` 下拉。

`base.html` 超级用户块里，Shop settings 下面加 Catalog 链接；`current_url` 匹配 `catalog_` 开头时高亮，并让菜单默认展开。

- [ ] **Step 5: 运行测试**

Run: `python manage.py test stock.test_catalog_settings --noinput`
Expected: OK。

- [ ] **Step 6: 提交** `feat: Settings Catalog with types the shop edits itself`

**阶段 1 收尾。** 部署说明要告诉店主：从现在起只有 Perfume 打批发折扣，Coffret 也不打。

---

## 阶段 2：添加产品先选 Type

### Task 4: Type 卡片页和按 Type 显示的表单

**Files:**
- Create: `stock/templates/stock/add_product_type.html`
- Modify: `stock/views.py:2122-2143,145-153`、`stock/forms.py:125-135`、`stock/templates/stock/add_product.html`、`stock/templates/stock/_product_fields.html:97-110,132,188-193`
- Test: `stock/test_add_by_type.py`

**Interfaces:**
- Consumes: `ProductType.for_code`、`all_cached`、`pfTypeRules`
- Produces:
  - `/add-product/` 无参数或 `type` 无效时显示卡片；`?type=<code>` 显示表单，模板变量 `product_type`。
  - `ProductForm(..., add_type: ProductType | None = None)`：传入时把 `product_type` 固定下来，`category` 取 `default_category`。

- [ ] **Step 1: 写失败测试**

```python
class AddByTypeTests(CatalogMixin):
    def test_the_page_opens_on_the_types(self):
        html = self._get().content.decode()
        self.assertIn('href="/add-product/?type=oil_perfume"', html)
        self.assertNotIn('name="barcode"', html)

    def test_an_unknown_type_goes_back_to_the_cards(self):
        self.assertNotIn('name="barcode"', self._get(type="nope").content.decode())

    def test_the_form_says_the_type_and_keeps_it_on_a_failed_save(self):
        self.assertContains(self._get(type="body_spray"), "Add Body spray")
        response = self.client.post(reverse("add_product") + "?type=body_spray", {"barcode": ""})
        self.assertContains(response, "Add Body spray")

    def test_the_category_comes_from_the_type(self):
        other = Category.objects.create(name="Body care")
        ProductType.objects.filter(code="body_spray").update(default_category=other)
        clear_type_cache()
        self._save(type="body_spray", barcode="6290000073001")
        self.assertEqual(Product.objects.get(barcode="6290000073001").category, other)

    def test_a_type_in_a_new_category_still_offers_every_brand(self):
        other = Category.objects.create(name="Body care")
        ProductType.objects.filter(code="body_spray").update(default_category=other)
        clear_type_cache()
        self.assertContains(self._get(type="body_spray"), self.brand.name)

    def test_description_sits_outside_the_scent_section(self):
        html = self._get(type="air_freshener").content.decode()
        scent = html[html.index('id="pfScent"'):]
        self.assertNotIn('name="description"', scent[:scent.index("</section>")])
        self.assertIn('name="description"', html)

    def test_hidden_scent_details_stay_in_the_form_so_they_are_kept(self):
        product = make_product(notes_top="Rosa", product_type_id="air_freshener")
        html = self.client.get(reverse("edit_product", args=[product.pk])).content.decode()
        self.assertIn('value="Rosa"', html)
```

- [ ] **Step 2: 运行，确认失败**

Run: `python manage.py test stock.test_add_by_type --noinput`
Expected: 卡片断言失败。

- [ ] **Step 3: 实现**

- 卡片页用紧凑网格，手机两列。
- 表单页有 `Change type` 链接回卡片。
- 去掉品牌按分类过滤：`forms.py` 的 `brand_filter` 和 `fillBrands()`，因为分类现在由 Type 决定，新分类下品牌列表会是空的。`build_brand_catalog` 不再需要 `category_ids`。
- `_product_fields.html`：Description 移出 Scent 区块，放进自己的 `<section>`，所有 Type 都显示。Scent 区块加 `id="pfScent"`，预览脚本按 `pfTypeRules` 用 `d-none` 显示或隐藏容量、浓度、Scent 区块。性别字段不动。Scent 字段只隐藏不移除，表单照常提交，所以原值保留；容量和浓度由 `clean()` 清空。
- 编辑页仍显示 Type 下拉。

- [ ] **Step 4: 运行测试**

Run: `python manage.py test stock.test_add_by_type stock.test_product_types --noinput`
Expected: OK。`test_the_page_is_the_perfume_form` 等旧测试改成 `?type=perfume`。

- [ ] **Step 5: 提交** `feat: adding a product starts from its type`

### Task 5: Coffret 不要容量

**Files:**
- Create: `stock/migrations/0076_coffret_no_volume.py`
- Modify: `stock/forms.py`
- Test: `stock/test_add_by_type.py`

**Interfaces:**
- Consumes: `ProductType.takes_volume`

- [ ] **Step 1: 写失败测试**

```python
class CoffretVolumeTests(CatalogMixin):
    def test_the_migration_clears_coffrets_only(self):
        gift = make_product(barcode="6290000074001", product_type_id="coffret", volume_ml=100, spec="100ML")
        bottle = make_product(barcode="6290000074002", volume_ml=100)
        importlib.import_module("stock.migrations.0076_coffret_no_volume").forward(apps, None)
        clear_type_cache()
        gift = Product.all_objects.get(pk=gift.pk)
        bottle.refresh_from_db()
        self.assertEqual((gift.volume_ml, gift.spec), (None, ""))
        self.assertEqual(bottle.volume_ml, 100)
        self.assertFalse(ProductType.objects.get(code="coffret").takes_volume)
        self.assertNotIn("ml", gift.display_name)

    def test_saving_a_type_without_volume_clears_it(self):
        ProductType.objects.filter(code="coffret").update(takes_volume=False)
        clear_type_cache()
        self._save(type="coffret", barcode="6290000074003", volume_ml="100")
        self.assertIsNone(Product.objects.get(barcode="6290000074003").volume_ml)
```

- [ ] **Step 2: 运行，确认失败**，Expected: 迁移模块不存在。

同时把 Task 1 的 `self.assertTrue(all(t.takes_volume ...))` 改成 `self.assertEqual({c for c, t in rows.items() if not t.takes_volume}, {"coffret"})`。

- [ ] **Step 3: 实现**

迁移 `forward` 设 `takes_volume=False`，并把 coffret 产品的 `volume_ml` 设 None、`spec` 设空（包括已归档产品）。`reverse` 只恢复开关。`ProductForm.clean` 在 `takes_volume` 关闭时清空 `volume_ml` 和 `spec`。

- [ ] **Step 4: 运行测试**，Run: `python manage.py test stock.test_add_by_type --noinput`，Expected: OK。

- [ ] **Step 5: 提交** `feat: a coffret has no volume`

**阶段 2 收尾。** 部署命令第一步必须是备份，因为这次迁移清空 Coffret 容量。

---

## 阶段 3：其余标签和 logo

### Task 6: 改名和合并

**Files:**
- Modify: `stock/services/catalog_admin.py`、`stock/management/commands/tidy_product_names.py:62-112`
- Test: `stock/test_catalog_admin.py`

**Interfaces:**
- Produces:
  - `rename_brand(brand: Brand, name: str) -> None`、`rename_line(line: ProductSeries, name: str) -> None`：名称用 `title_case`；同一 `transaction.atomic` 更新 `Product.brand` 或 `Product.model`，包括已归档产品，且只更新外键指向它的产品；名称被占用时抛 `NameTaken(existing)`。
  - `merge_brands(keep: Brand, other: Brand) -> int`、`merge_lines(keep: ProductSeries, other: ProductSeries) -> int`：返回影响的产品数，同样同步文字。`keep == other` 抛 `ValueError`。
  - 保留的品牌有 logo 就用它的，否则接过被合并品牌的 logo。

- [ ] **Step 1: 写失败测试**

```python
class RenameTests(TestCase):
    def test_renaming_a_brand_updates_live_and_archived_text(self): ...
        # 断言 Product.objects 和 Product.all_objects 的 brand 都是新名字
    def test_renaming_a_line_updates_model_only_for_its_products(self): ...
        # 另一品牌同名系列的产品不变
    def test_a_name_in_use_is_refused_even_by_case(self):
        with self.assertRaises(NameTaken) as caught:
            rename_brand(self.armaf, "LATTAFA")
        self.assertEqual(caught.exception.existing, self.lattafa)
        self.armaf.refresh_from_db()
        self.assertEqual(self.armaf.name, "Armaf")
    def test_merging_moves_products_and_joins_same_named_lines(self): ...
        # 两个品牌都有 Khamrah：产品归到保留的系列，另一个系列删除；不同名系列移到保留品牌
    def test_merging_a_brand_into_itself_is_refused(self): ...
    def test_the_merge_rewrites_the_text_on_archived_products(self): ...
```

`NameTaken` 定义在 `catalog_admin.py`，有属性 `existing`。

- [ ] **Step 2: 运行，确认失败**，Expected: ImportError。
- [ ] **Step 3: 实现，并把 `tidy_product_names` 的合并分支改成调用这两个函数**
- [ ] **Step 4: 运行测试**，Run: `python manage.py test stock.test_catalog_admin stock.tests --noinput`，Expected: OK。
- [ ] **Step 5: 提交** `feat: rename and merge brands and lines, text included`

### Task 7: Brands 和 Lines 标签

**Files:**
- Create: `stock/templates/stock/catalog_brands.html`、`stock/templates/stock/catalog_lines.html`
- Modify: `stock/catalog_views.py`、`stock/urls.py`、`stock/templates/stock/catalog_base.html`
- Test: `stock/test_catalog_settings.py`

**Interfaces:**
- Consumes: Task 6 全部函数、`catalog_prune.empty_brands`
- Produces: 路由 `catalog_brands`（`settings/catalog/brands/`）、`catalog_lines`（`settings/catalog/lines/?brand=<id>`），POST `action=add|rename|merge|delete`。

- [ ] **Step 1: 写失败测试**：`test_a_taken_name_offers_the_merge`（响应 200，含 `Merge`，不是 500）、`test_merge_shows_how_many_products_move`、`test_a_brand_with_products_cannot_be_deleted`、`test_an_empty_brand_can_be_deleted`、`test_lines_filter_by_brand`、`test_the_counts_ignore_archived_products`。
- [ ] **Step 2: 运行，确认失败**
- [ ] **Step 3: 实现**：行上显示 logo 位、名称、产品数、系列数；上方有搜索框；合并在侧边面板里选目标，确认按钮写 `Merge into X (N products)`。品牌有 58 个以上，列表一次全部显示，用搜索过滤。
- [ ] **Step 4: 运行测试**，Run: `python manage.py test stock.test_catalog_settings --noinput`，Expected: OK。
- [ ] **Step 5: 提交** `feat: Catalog brands and lines`

### Task 8: Categories、Concentrations、Fragrance families 标签

**Files:**
- Create: `stock/templates/stock/catalog_categories.html`、`stock/templates/stock/catalog_strengths.html`、`stock/templates/stock/catalog_families.html`
- Modify: `stock/catalog_views.py`、`stock/urls.py`、`stock/templates/stock/catalog_base.html`
- Test: `stock/test_catalog_settings.py`

**Interfaces:**
- Produces: 路由 `catalog_categories`、`catalog_strengths`、`catalog_families`，POST `action=add|save|delete`。

- [ ] **Step 1: 写失败测试**：`test_a_category_in_use_stays`（有产品、有子分类、是某 Type 默认分类，三种都拒绝，并各自提示）、`test_an_unused_category_goes`、`test_the_shopify_switch_saves`、`test_a_strength_in_use_stays_and_says_how_many`、`test_a_family_in_use_stays`、`test_new_rows_appear_in_the_product_form`。
- [ ] **Step 2: 运行，确认失败**
- [ ] **Step 3: 实现**：新分类 `form_kind=PERFUME`，`sync_to_shopify` 由表单决定。`Concentration` 编辑 `name`、`short`、`shopify_tag`、`sort_order`；`FragranceFamily` 编辑 `name`、`shopify_tag`、`sort_order`。
- [ ] **Step 4: 运行测试**，Expected: OK。
- [ ] **Step 5: 提交** `feat: Catalog categories, concentrations and families`

### Task 9: 品牌 logo

**Files:**
- Create: `stock/migrations/0077_brand_logo.py`、`stock/services/brand_logo.py`、`stock/templatetags/brands.py`、`stock/templates/stock/_brand_logo.html`
- Modify: `stock/models/catalog.py`、`stock/services/catalog_prune.py`、`stock/catalog_views.py`、`stock/templates/stock/catalog_brands.html`、`stock/templates/stock/product_detail.html:89`、`stock/templates/stock/product_list.html`（品牌筛选）、`stock/services/catalog_pdf.py:124`、`stock/static/css/app.css`
- Test: `stock/test_brand_logo.py`

**Interfaces:**
- Produces:
  - `Brand.logo = ImageField(upload_to='brand_logos/', blank=True, default='')`
  - `clean_logo(upload) -> ContentFile`：不合法时抛 `ValueError`，消息 `The logo must be a JPG, PNG or WebP image.` 或 `The logo is over 2 MB.`。用 Pillow 真正打开校验，缩到最长边 256px，保留透明。
  - `{% load brands %}{% brand_logo brand size=28 fallback=product.brand %}`：`brand` 可为 None；没有 logo 显示 `fallback` 首字母。

- [ ] **Step 1: 写失败测试**

```python
class LogoTests(TestCase):
    def test_a_big_photo_is_shrunk(self): ...          # 800x400 PNG -> 最长边 256
    def test_a_text_file_named_png_is_refused(self):
        with self.assertRaisesMessage(ValueError, "must be a JPG, PNG or WebP"):
            clean_logo(SimpleUploadedFile("x.png", b"not an image", content_type="image/png"))
    def test_svg_is_refused(self): ...
    def test_over_two_megabytes_is_refused(self): ...
    def test_a_bad_upload_saves_nothing_and_shows_the_message(self): ...   # 走 catalog_brands POST
    def test_a_brand_with_a_logo_is_not_pruned(self):
        self.brand.logo = "brand_logos/x.png"; self.brand.save()
        self.product.delete()
        self.assertTrue(Brand.objects.filter(pk=self.brand.pk).exists())
    def test_the_tag_falls_back_to_the_first_letter(self): ...             # brand=None, fallback="Lattafa" -> "L"
    def test_the_pdf_prints_with_and_without_a_logo(self): ...
```

用 `@override_settings(MEDIA_ROOT=临时目录)`。

- [ ] **Step 2: 运行，确认失败**
- [ ] **Step 3: 实现模型、service、标签**
- [ ] **Step 4: `empty_brands` 加 `rows.filter(logo='')`，更新 `catalog_prune` 的文档字符串**
- [ ] **Step 5: 放到四个位置**：Brands 列表、产品详情页品牌名前、Products 的品牌筛选、PDF 品牌标题左侧（文件不存在时跳过）。CSS 放 `app.css`，这次部署要加 `collectstatic`。
- [ ] **Step 6: 运行测试**，Run: `python manage.py test stock.test_brand_logo stock.test_catalog_prune stock.test_catalog_settings --noinput`，Expected: OK。
- [ ] **Step 7: 提交** `feat: brand logos, used across the app`

**阶段 3 收尾。** 部署说明写清：logo 不在 `backup_db` 内；品牌改名不影响已上架的 Shopify 商品。

---

## 阶段 4：内部连接

### Task 10: `ProductContent` 和业务逻辑

**Files:**
- Create: `stock/migrations/0078_product_contents.py`、`stock/services/product_contents.py`、`stock/test_product_contents.py`
- Modify: `stock/models/catalog.py`、`stock/models/__init__.py`

**Interfaces:**
- Produces:
  - `Product.is_mix = BooleanField(default=False)`
  - `ProductContent`：`owner`（FK Product，CASCADE，`related_name='contents'`）、`name`（Char 120）、`linked`（FK Product，SET_NULL，null，`related_name='contained_in'`）、`sort_order`。
  - `Product.scent_name`（property）：系列加名称，系列和名称相同只说一次，不带品牌、浓度、容量。
  - `Product.lists_contents`（property）`-> bool`：`is_mix or type_info.has_contents`。
  - `content_label(content) -> str`：`linked` 存在且未归档时用 `linked.scent_name`，否则 `name`。
  - `save_contents(owner, rows: list[dict]) -> None`：每项 `{'name': str, 'linked_id': int | None}`；替换全部行，名称去空格，空行跳过，同名只留第一个，`linked` 是自己或 Mix 时丢弃连接、保留名称，写回 `name`。
  - `parse_pasted(brand_name: str, text: str) -> list[dict]`：按换行和逗号拆分，同品牌（`brand__iexact`）在售非 Mix 产品的 `scent_name` 完全相同（不分大小写）时自动连接。收品牌名是因为新产品保存前还没有 id。
  - `also_in(product) -> list[Product]`：包含它的在售产品中 `lists_contents` 为真的，按名称排序。
  - `contents_title(owner) -> str`：`is_gift` 的 Type 返回 `Contains`，其它返回 `Scents`。

- [ ] **Step 1: 写失败测试**

```python
class ContentsTests(TestCase):
    def test_a_linked_scent_follows_the_perfume_name(self): ...
    def test_an_archived_or_deleted_perfume_leaves_the_stored_name(self): ...
    def test_pasting_cleans_up(self):
        rows = parse_pasted("Lattafa", " Khamrah \n\nYara, Yara\nasad, Oud Novo")
        self.assertEqual([r["name"] for r in rows], ["Khamrah", "Yara", "Asad", "Oud Novo"])
        self.assertEqual([bool(r["linked_id"]) for r in rows], [True, True, True, False])
    def test_a_mix_cannot_link_to_itself_or_another_mix(self): ...
    def test_saving_replaces_the_list_and_keeps_the_order(self): ...
    def test_also_in_skips_archived_owners(self): ...
    def test_linking_moves_no_stock(self): ...      # 卖出 Coffret 后香水 remaining 不变
    def test_the_migration_ticks_products_on_the_mix_line(self): ...   # "mix"、"Mix" 都算，产品名含 Mix 但系列不是的不算
```

- [ ] **Step 2: 运行，确认失败**
- [ ] **Step 3: 实现模型、service 和迁移**（`forward`：`Product.objects.filter(model__iexact='mix').update(is_mix=True)`，迁移里的 manager 包括已归档产品）
- [ ] **Step 4: 运行测试**，Run: `python manage.py test stock.test_product_contents --noinput`，Expected: OK。
- [ ] **Step 5: 提交** `feat: mix and coffret contents`

### Task 11: 表单里的清单

**Files:**
- Create: `stock/templates/stock/_product_contents.html`
- Modify: `stock/forms.py`、`stock/views.py`（`add_product_view`、编辑视图、`products_autocomplete`）、`stock/templates/stock/_product_fields.html`、`stock/catalog_views.py`、`stock/templates/stock/catalog_types.html`
- Test: `stock/test_product_contents.py`

**Interfaces:**
- Consumes: `save_contents`、`parse_pasted`、`Product.lists_contents`
- Produces:
  - 表单字段 `is_mix`、隐藏字段 `contents_json`（`[{name, linked_id}]`）。
  - `products_autocomplete` 新参数 `exclude_mix=1`，返回项增加 `brand`（已有）和 `scent_name`。
  - `POST api/parse-contents/`（`parse_contents`，manager）：`{text, brand}` 返回 `parse_pasted` 的结果，给粘贴用。

- [ ] **Step 1: 写失败测试**：`test_ticking_mix_saves_the_list`、`test_a_coffret_needs_no_tick`、`test_a_single_body_spray_links_with_no_tick`、`test_an_ordinary_perfume_has_no_list`、`test_unticking_mix_keeps_the_rows_hidden`（下次勾选还在，PDF 和详情页不显示）、`test_broken_json_does_not_lose_the_product`（表单报错，不是 500）、`test_autocomplete_skips_mixes_on_request`。
- [ ] **Step 2: 运行，确认失败**
- [ ] **Step 3: 实现**：勾 `This is a mix` 或 Type 的 `has_contents` 打开时显示清单，标题用 `contents_title`。每行一个输入框，用 `products_autocomplete?scope=stock&exclude_mix=1` 提示，同品牌排前；选中后行上显示 `Linked`，可以取消。下方有 `Paste several`。行内用模板内联脚本和样式。`catalog_types.html` 的表单加 `has_contents` 开关。
- [ ] **Step 4: 运行测试**，Expected: OK。
- [ ] **Step 5: 提交** `feat: edit a mix or coffret's list on the product form`

### Task 12: 产品详情页

**Files:**
- Modify: `stock/views.py:2147-`（`product_detail_view`）、`stock/templates/stock/product_detail.html`
- Test: `stock/test_product_contents.py`

**Interfaces:**
- Consumes: `content_label`、`also_in`、`Product.lists_contents`

- [ ] **Step 1: 写失败测试**：`test_a_mix_page_lists_scents_and_links_the_linked_ones`、`test_a_perfume_page_says_also_in_with_stock`、`test_an_archived_mix_is_not_mentioned`、`test_a_product_with_neither_shows_no_section`。
- [ ] **Step 2: 运行，确认失败**
- [ ] **Step 3: 实现**：放在页头标签下方，紧凑一行，名称之间用 `·`；文字 `Also in:`，标签用 `type_info.name_word`，Mix 后加 `(Mix)`，后面显示库存数。
- [ ] **Step 4: 运行测试**，Expected: OK。
- [ ] **Step 5: 提交** `feat: mixes, coffrets and perfumes point at each other`

**阶段 4 收尾。** 部署后店主要自己填这些产品的清单。

---

## 阶段 5：PDF 跳转

### Task 13: 客户 PDF 内部链接

**Files:**
- Modify: `stock/services/catalog_pdf.py:44-60,87-160`、`stock/services/product_contents.py`
- Test: `stock/test_catalog_pdf_links.py`

**Interfaces:**
- Consumes: `content_label`、`also_in`
- Produces:
  - `pdf_links(products: list[Product]) -> dict[int, dict]`：`{id: {'contents': [(label, target_id|None)], 'also_in': [(label, target_id|None)]}}`，一次查询取齐，不逐行查，只算 `lists_contents` 为真的产品。`target_id` 只在目标也在 `products` 里时才有值；`also_in` 的 label 用 `type_info.customer_label or name`。
  - `_detail_line(prefix: str, items: list) -> str`：返回 Paragraph 标记，名称用 `xml.sax.saxutils.escape` 转义，链接 `<a href="#p123" color="#1d4ed8">`。

- [ ] **Step 1: 写失败测试**

```python
class PdfLinkTests(TestCase):
    def _pdf(self, *products): ...      # 走 build_catalog_pdf，返回 PdfReader
    def _links(self, reader): ...       # 收集 /Link 注释指向的页码

    def test_a_perfume_and_its_mix_link_both_ways(self): ...
    def test_the_text_is_there(self):
        text = self._text(self.perfume, self.mix, self.gift)
        self.assertIn("Também em:", text); self.assertIn("Fragrâncias:", text); self.assertIn("Contém:", text)
    def test_a_target_outside_the_pdf_stays_plain_text(self):
        reader = self._pdf(self.mix)
        self.assertEqual(self._links(reader), [])
        self.assertIn("Khamrah", reader.pages[0].extract_text())
    def test_an_archived_perfume_stays_plain_text(self): ...
    def test_a_name_with_an_ampersand_prints(self): ...   # "Oud & Rose"
    def test_a_long_list_wraps_and_builds(self): ...      # 40 款
    def test_an_empty_list_adds_no_line(self): ...
    def test_unticked_mix_shows_nothing(self): ...
    def test_links_cost_a_fixed_number_of_queries(self): ...
```

- [ ] **Step 2: 运行，确认失败**
- [ ] **Step 3: 实现**：加 `detail` 样式（7.5pt，灰色）；每行 `export_title` 前加 `<a name="p<id>"/>`；有内容时单元格变成 `[Paragraph(title), Paragraph(detail)]`。三种前缀：`Contém:`（`is_gift` 的 Type）、`Fragrâncias:`（其它有清单的产品）、`Também em:`（被连接的产品）。
- [ ] **Step 4: 运行测试**，Run: `python manage.py test stock.test_catalog_pdf_links --noinput`，Expected: OK。
- [ ] **Step 5: 渲染真实导出看一眼**：用演示数据导出 PDF，看长清单的行高和分页。
- [ ] **Step 6: 提交** `feat: the customer PDF links perfumes and the products tied to them`

**阶段 5 收尾。** 交付时请店主在手机上点一次链接，这是 spec 里唯一还没验证的点。
