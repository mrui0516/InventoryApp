# Messaging 到货提醒：直接部署

代码分支：`codex/restock-app-20261026`。该分支独立于另一个 AI 的工作区；没有合并或推送 master。无需安装压缩包，无新增 Python 依赖。

## 1. PythonAnywhere 更新代码与建表

在 Bash console 逐条执行，一条报错就停下：

```bash
cd ~/InventoryApp
workon inventory
python manage.py backup_db
git status --short
git fetch origin
git merge --ff-only origin/codex/restock-app-20261026
python manage.py migrate restock_alerts
python manage.py check
python manage.py check_restock_messaging
```

`git status` 若显示服务器有本地代码改动，先不要合并；提供状态即可。`--ff-only` 若提示分支已分叉，也不要 reset 或强行覆盖。这种情况需要在干净分支整合其他 AI 的提交后再部署。此流程不覆盖数据库或服务器 `.env`。

`check_restock_messaging` 只读取线上现有 Shopify 令牌权限，不创建客户、不改营销状态、不加标签、不发邮件。需要库存 App 的令牌拥有 `write_customers` 和商品读取权限（`read_products` 或 `write_products`）。ChatGPT 连接器的权限不等于库存 App 令牌权限。权限不足需在库存 App 对应的 Shopify 应用配置中授权并更新服务器令牌，不要在聊天或 Git 中发送令牌。

## 2. 配置服务器环境

编辑服务器已有 `.env`，新增或更新以下键，保留其他配置。先保持两个启用开关为 0：

```ini
RESTOCK_PROVIDER=messaging
RESTOCK_MESSAGING_API_VERSION=2026-10
RESTOCK_MESSAGING_FLOW_READY=0
RESTOCK_NOTIFICATIONS_ENABLED=0
RESTOCK_PUBLIC_URL=https://gestao.scentory.pt/api/restock/
RESTOCK_ALLOWED_ORIGINS=https://www.scentory.pt,https://scentory.pt
```

`SHOPIFY_STORE_DOMAIN` 和 `SHOPIFY_ADMIN_TOKEN` 继续使用服务器现有值。Messaging 模式不依赖 SMTP；现有 SMTP 设置可以保留。

## 3. 配置正式 Flow（先关闭）

工作流：`Restock email when customer tags added (production)`。原 QA 流程继续保持关闭。

2026-10-26 当前进度：你已确认正式草稿的条件及下述 `slice: 0, 21` 回执写法通过 Flow 验证，仍关闭。邮件主题与正文尚未确认保存，因此尚不能启用正式通知。

1. 触发器：**Customer tags added**。
2. AND 条件：触发器**本次新增 Tags** 至少一个以 `scentory-restock-job|` 开头；客户邮件营销状态为 **SUBSCRIBED**。正式流程没有测试邮箱限制。
3. **Send marketing email**：在关联的 Messaging 邮件编辑器中设置主题 `O perfume que esperava está de volta`。在 Custom Liquid 区块中使用 `shopify/restock/messaging-email.liquid` 内容，保留默认退订页脚，然后保存。
4. 发送动作完成路径后添加 **Remove customer tags**。Tags 只设置一个列表项，值为下面完整 Liquid：

```liquid
{% for tag in tags %}{% assign prefix = tag | slice: 0, 21 %}{% if prefix == 'scentory-restock-job|' %}{{ tag }}{% endif %}{% endfor %}
```

这里的 `tags` 必须来自触发器的**本次新增列表**，不能用 `customer.tags`。应用每次只添加一个唯一任务标签；上面渲染结果必须是单个 `scentory-restock-job|UUID`，无逗号、无其他标签。不要改为固定的回执标签，也不要删除全部客户标签。旧流程延迟重试只能移除自己的 UUID，不能影响下一项任务。

不要添加 Wait 步骤或在同一流程添加客户标签；否则需要重新检查事件上下文。不要让其他自动化修改这三个保留前缀：`scentory-restock-job|`、`scentory-restock-ready|`、`scentory-restock-cancel|`。

## 4. 验证与开启

原 QA 测试已证明 Messaging 可以向 `info@scentory.pt` 发信，并正确显示商品、100ml 容量和链接。**它尚未证明正式回执动作和两款产品连续通知的完整链路。**

启用正式通知前，先完成只针对自有测试邮箱的受限验证：使用新的邮件正文及唯一任务回执，确认收到正确商品邮件、Flow 只移除本次任务 UUID，然后核对 App 清理标签和队列状态。再连续验证第二款产品，确保第一封邮件不会读取第二款的内容。验证时不调整真实库存，不让普通客户参与。连接器无法编辑或启用 Flow，这部分需在后台完成。

Shopify 文档未明确保证 Send marketing email 返回时客户标签内容已完成快照渲染；因此这一实际双产品验证是上线条件，不能以后台标签添加成功或离线测试替代。

受限验证完成、正式邮件已保存且正式流程已开启后，把 `.env` 中两个开关设为：

```ini
RESTOCK_MESSAGING_FLOW_READY=1
RESTOCK_NOTIFICATIONS_ENABLED=1
```

到 **Web → Reload**，再执行：

```bash
python manage.py check_restock_messaging
python manage.py process_restock_notifications
```

访问 `https://gestao.scentory.pt/api/restock/status/` 应看到 `enabled: true`、`provider: "messaging"`、`requires_marketing_consent: true`。此状态只表示启用条件满足，不能代替实际收信验证。

## 5. 持续处理队列

若账号有 **Tasks → Always-on tasks**，创建以下任务，启用后每 60 秒处理一次订阅、库存和 Flow 回执：

```bash
/home/scentory/.virtualenvs/inventory/bin/python -u /home/scentory/InventoryApp/manage.py process_restock_notifications --watch --interval 60
```

不要在每小时 Scheduled task 中启动无限 `--watch`。如果只有定时任务，使用不带 `--watch` 的命令单次执行；这时通知延迟取决于你的任务频率，不能承诺一分钟内发出。库存 App 推送库存后会额外唤起一次处理，但 Web 后台线程不能代替持久任务。

修改 `.env` 或升级代码后，需要重启 Always-on task；Web Reload 不会刷新已运行任务中的 Django 配置。

## 6. 发布与日常查看

草稿主题 `Scentory - SEO GEO 24-10-2026` 已更新订阅弹窗和中英文同意文案，仍未发布。只有后台配置及验证完成后，再在 Shopify 手动发布主题。

客户在 Messaging 模式必须主动勾选邮件营销同意，系统才可记录 SINGLE_OPT_IN 并建立该容量提醒。勾选默认关闭；同意文案版本及时间保存在 App。已订阅客户的确认等级不降级，后台任务不重新订阅已退订客户。既有 SMTP 申请不会自动转换。

库存 App：**Tools → Restock demand**；Django Admin 可查看订阅、邮件任务和 Messaging slots。

- `dispatched`：已添加任务标签，等待 Flow 完成。
- `sent`：SMTP 接受邮件，或 Messaging 的 Flow 发送动作已完成（不是收件箱送达证明）。
- `blocked` / `failed`：需检查 Flow 原始运行。接口超时后可能已经发信，系统不会自动删掉触发标签再添加，也不会盲目重发。先确定原流程终止或完成，再由管理员处理原任务；不要直接删除 slot。
- 营销退订会停止新的通知。已在发送中的提醒可能已被提交，取消不能撤回已发送邮件；旧流程尚未回执时会保留该邮箱的上下文锁。
- 每个邮箱同一时间仅一项任务；不同邮箱可独立推进。库存按 App 对应 SKU 和 Shopify 具体变体核对，发送前再检查有货；5ml 不会替代 100ml。

## 官方说明

- [Messaging 支持的 Liquid 变量](https://help.shopify.com/en/manual/promoting-marketing/create-marketing/shopify-messaging/email/create-email/custom-liquid)
- [Customer tags added：本次新增标签](https://help.shopify.com/en/manual/shopify-flow/reference/triggers/customer-tags-added)
- [Remove customer tags](https://help.shopify.com/en/manual/shopify-flow/reference/actions/remove-customer-tags)
- [PythonAnywhere Always-on tasks](https://help.pythonanywhere.com/pages/AlwaysOnTasks/)
