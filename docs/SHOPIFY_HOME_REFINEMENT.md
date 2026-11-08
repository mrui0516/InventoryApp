# 首页与商品列表优化（2026-11-08）

## 最终业务规则

- **Novidades**：以库存 App 的 `Product.created_at` 倒序、ID 倒序为准，只取 App 实际库存大于 0、Shopify 整瓶或合法分装可售、且已发布 Online Store 的最新 20 款。新品到货后加入并挤出最旧一款；缺货移出后由下一款可售商品补位。不得使用 Shopify 的历史建立／发布时间作为上新依据。
- **品牌／分类页**：默认整瓶／整件有货 → 仅分装有货 → 完全缺货。按整个系列排序，分页之后仍遵循顺序，保留系列成员和分类条件。月度销量在库存档位内排序。
- **不分装的 Type**：所有 Coffret、Oil perfume、Mini perfume 均只按整套／整件销售。禁止生成 5ml／10ml，禁止扣分装预留，禁止计算分装价格，禁止归入“仅分装有货”。已有错误分装库存强制为 0，并启用库存跟踪和禁止超卖；不删除旧订单记录。
- Mini 的不同香型属于正常款式选项，保留，不视为分装。
- 按用户选择，不创建管理集合，不接管 `/collections/all`。缺少 all 集合时，后台不得创建或发布它。
- 仍保留“旧的未上架商品不自动补建；重新启用后真正新建的商品才允许自动创建”的限制。

## 已准备的主题

草稿：**Scentory - Home refinado 08-11-2026**

- ID：`208327311708`
- 预览：https://www.scentory.pt/?preview_theme_id=208327311708
- 编辑：https://admin.shopify.com/store/66tcd5-su/themes/208327311708/editor
- 从当前线上主题 `207802925404` 复制；没有修改或发布当前 MAIN。
- Novidades 使用品牌列式的连续横向滚动，保留内容区两侧边距；去除暂停、计数器及大按钮，点击卡片直接进入商品。80 秒完成一整轮，支持鼠标／触摸拖动、松开继续、末尾无缝回到开头；尊重减少动态效果设置。
- 名称统一两行，图片比例和卡片高度对齐；柔和圆角和边框，缺货只淡化图片与背景，文字保留对比度。
- 首页增加 30 天 Restock 横向展示列；零售优先及 Revenda 最后保持。
- 产品页和列表价格排除三种不分装 Type 的遗留分装规格；普通香水的分装不变。

当前连接器禁止对 MAIN 写文件及发布主题。完成预览后，在 Shopify → Online Store → Themes 找到上述草稿并点击 **Publish**，视觉改动才会显示在正式首页。

## PythonAnywhere 更新

旧的同步 Always-on task 在本次修复期间已由用户暂停。不要启动未更新的旧进程，否则会把 Novidades 改回旧名单。

```bash
cd ~/InventoryApp
workon inventory
git fetch origin
git merge --ff-only origin/codex/home-refinement-20261108
python manage.py backup_db
python manage.py migrate
python manage.py check
```

只将三种不分装 Type 加入一次更新队列；不扫描／发布整个历史目录：

```bash
python manage.py shell -c "from stock.models import Product; from stock.services.shopify_catalog_queue import enqueue; [enqueue(p, wake_worker=False) for p in Product.objects.filter(product_type_id__in=['coffret','oil_perfume','mini'])]"
python manage.py process_shopify_catalog --apply --limit 100
```

这一步从服务器当前数据库同步整件库存，避免拿本机旧备份推高库存。已有错误的 Coffret 分装规格已先在 Shopify 停售；整件库存由当前 App 核实后恢复。

然后 **Web → Reload**，**重启**原 Always-on task，让进程加载新代码。不要用 Ctrl+Z 挂住任务，也不要再次运行 `--all`。

## 数据核对

原 Novidades 后台 20 款中有 19 款未发布 Online Store，因此前台只显示 1 款。Type 分类条件本身正确：Coffrets 原 5 款中只有 1 款已发布，Oil 原 2 款中只有 1 款已发布。用户已明确同意发布这 5 款，以及后续确认的库存 App 最近新增批次；批次以外的历史未发布商品保持不变。

备份中的额外 Mix／Gift Set／Roll Oil／Mini 记录部分没有 Shopify 条码对应项，未自动创建。重复条码 `6294015190656` 仍跳过，不随意选取其中一条。

本次 Native API 已核对并完成 20 个现有系列的库存优先排序。实际后台持续更新仍须完成上述 PythonAnywhere 部署。主题文件差异保存在 `shopify/home-refinement-20261108.patch`；不需要手动安装这个补丁，草稿已通过 API 更新。
