# 首页“即将到货”自动更新

“Novidades a caminho”读取线上库存 App 的待收货订单。商品必须有正数在途数量、从未进货或销售、未归档，并属于允许同步的分类。收货、取消订单或删除待收货行后自动移出；老款补货继续属于原有 Restock 流程。

复用现有 `process_shopify_catalog --apply` 任务，不需要新增 PythonAnywhere API token，也不需要第二个定时任务。`SHOPIFY_INCOMING_SYNC` 默认继承已有 `SHOPIFY_CATALOG_AUTOMATION` 开关；需要单独关闭时才设置为 `0`。入库及商品变更提交后也会触发后台刷新，周期任务负责补偿进程重启和网络失败。

前台只读取 Shopify 店铺 JSON metafield `scentory.incoming_products`。内容仅包含公开商品名称、品牌、图片和公开商品链接，优先使用已核对的 Shopify 名称；没有公开商品页的新品显示预告卡片，不生成死链接。不会因此发布商品，不会修改价格、库存或商品描述。超出 48 小时未更新的数据自动隐藏。

## 按原 Git 流程部署

将本次功能提交合入线上使用的版本后，在 PythonAnywhere 原项目和虚拟环境中执行：

```bash
cd /home/scentory/InventoryApp
workon inventory
git pull
python manage.py check
python manage.py sync_shopify_incoming --apply
```

这次没有新增数据库迁移，也没有修改 App 静态文件。Web 页面执行 Reload，使订单变更回调生效；如果原任务是 `process_shopify_catalog --apply --watch` 常驻进程，在原 Always-on task 点击 Restart 一次以载入新代码。如果原任务是周期执行新进程，下次执行会自动使用新代码，保留原任务即可。

首页位于未发布主题 `208993616220`，按商家要求继续保留预览。本地旧数据库不会用于初始化线上数据。独立命令 `sync_shopify_incoming` 不带 `--apply` 时仅统计本地候选，不调用 Shopify；带 `--apply` 时只更新本栏目的数据。

## 验证

```bash
python manage.py test stock.test_shopify_incoming stock.test_shopify_arrivals stock.tests.SyncShopifyStorefrontTests --noinput
```

覆盖待收货新品、补货排除、收货/取消移除、重复行去重、归档和分类开关、事务提交后刷新、空数据清除、并发写入保护，以及复用现有后台任务。主题另有 H2/H3、空/过期数据、未发布链接和文本转义检查。
