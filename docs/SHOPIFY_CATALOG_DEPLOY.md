# Shopify 自动上架、Novidades 与 Restock

2026-11-07。邮件到货提醒继续暂停；本功能不启用 Flow、不发送客户邮件。

## 最终行为

- 库存 App 新建/编辑商品、上传图片、收货和销售后，只在数据库排队，保存操作不等待 Shopify。
- 允许同步的分类中，具有条码、正数零售价和可读取本地图片的新品自动创建 Shopify 草稿。同步当前 Type、品牌、价格、库存，等待 Shopify 图片 READY 后发布 Online Store。已有人工草稿/归档商品保持原状态。
- Novidades 保留按 App 建档时间最新的 20 款已上架商品，再把这 20 款中的有货商品排前。缺货商品保持展示。
- Restock 仅收录之前有收货历史、实际库存从 0 经收货变为正数的商品；保留 30 天，当前再次为 0 时移出。首次收货、门店调拨、退货、手工调整不产生新的补货时间。
- 月度畅销及男女中性排序继续使用本月零售销量。这里只调整集合，不把缺货商品改为 DRAFT。
- 稳定 handle 防止重试重复创建；数据库租约阻止多个 worker 同时写入；失败退避重试，较新的编辑不会被旧任务覆盖。重复 SKU 保留错误并跳过，需人工处理。

## PythonAnywhere 部署

在现有 Bash 控制台执行。若 `git status --short` 有服务器代码改动，先保留改动，不运行强制覆盖命令。

```bash
cd ~/InventoryApp
workon inventory
git status --short
git fetch origin
git merge --ff-only origin/codex/shopify-arrivals-20261107
python manage.py backup_db
python manage.py migrate
python manage.py check
```

然后执行下面的命令更新项目现有 `.env`。只更新四个非密钥配置，不输出或覆盖其他值：

```bash
python - <<'PY'
from pathlib import Path
path = Path('.env')
values = {
    'SHOPIFY_CATALOG_AUTOMATION': '1',
    'SHOPIFY_CATALOG_API_VERSION': '2026-10',
    'SHOPIFY_RESTOCK_DAYS': '30',
    'SHOPIFY_ONLINE_STORE_PUBLICATION_ID': 'gid://shopify/Publication/340853948764',
}
lines = path.read_text(encoding='utf-8').splitlines() if path.exists() else []
lines = [line for line in lines if line.strip().split('=', 1)[0] not in values]
lines.extend(f'{key}={value}' for key, value in values.items())
path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
print('Catalogue configuration saved; other settings preserved.')
PY
python manage.py process_shopify_catalog --apply --all --backfill-restocks
```

首次运行会同步当前允许上网的商品的零售价和库存，并自动创建资料完整但未上架的商品；沿用系统既有试香库存保留规则。重复 SKU、缺照片、接口暂时失败的条目留在队列，不需要反复执行 `--all`。历史补货只回填账目可核对、没有人工调整且余额未曾为负的记录；不确定记录跳过。

Shopify App 的现有 Admin API token 需要商品及库存读写、Online Store 发布权限（`write_publications`）；连接器权限与库存 App token 的权限相互独立。如日志出现权限错误，补齐该 token 权限后重启任务即可重试。

PythonAnywhere → Web → Reload，然后在 Tasks → Always-on tasks 新建以下任务：

```bash
/home/scentory/.virtualenvs/inventory/bin/python -u /home/scentory/InventoryApp/manage.py process_shopify_catalog --apply --watch --interval 60
```

实际用户名或项目目录不同，替换为现有部署路径；虚拟环境可在已 `workon inventory` 的 Bash 中用 `which python` 确认。项目会自行读取 `.env`，无需在任务命令中填写密钥。任务应显示 Running。默认每 60 秒检查队列和 30 天到期，图片处理、API 退避或较多任务会延长实际更新时间。

Always-on tasks 仅适用于付费账号，PythonAnywhere 会自动重启意外退出的任务：[官方说明](https://help.pythonanywhere.com/pages/AlwaysOnTasks/)。不要用普通 Bash 控制台长期挂住命令来替代它。修改 `.env` 后同时 Reload Web 和重启此任务。

原 `sync_shopify_storefront --apply` 已改为共用同一个租约 worker，可作为已有定时任务的保底。`SHOPIFY_CATALOG_AUTOMATION=0` 会阻止产品自动处理；已运行进程需要重启才读取新配置。

## 本次已完成的 Shopify 配置

- Novidades：`/collections/novidades`，20 款；按用户提供的 2026-11-07 备份初始化。
- Restock：`/collections/restock`，7 款可确认的历史补货；Online Store 已发布。
- Produtos 菜单中，在 Novidades 后加入 Restock；保留当前其他菜单内容。
- 数据库备份 SHA256：`b4173dbd228d3df4cfc9655228e62575837571e015bed6aa1d65d09e4681ac41`。
- Shopify 现有重复条码 `6294015190656` 对应两个 Armaf Odyssey White Edition 200ml。未删除或合并；自动产品同步拒绝更新歧义目标，集合刷新略过该 SKU。

上线后日常新增、补货不需要再次导出数据库或手动修改系列。服务器尚未部署前，这些页面为本次初始化结果，不代表后台自动化已经运行。
