# 新客优惠与分装定价（2026-11-01）

## 已执行的 Shopify 调整

- 原 EUR5 自动优惠 `2365644439900` 已改为首单欢迎优惠；保留原有 EUR25 商品小计门槛，不与其他优惠叠加。
- 资格分群 `1192291402076`：`number_of_orders = 0 AND (email_subscription_status = 'SUBSCRIBED' OR customer_account_status = 'ENABLED')`。注册账户或订阅邮件均可；顾客在 checkout 使用相同邮箱，由 Shopify 判断资格并自动减 EUR5。无需欢迎优惠邮件或另一个发信服务。
- 用户确认停用旧 WELCOME10（`2382953546076`），API 回读状态 EXPIRED。
- 210 款产品的 420 个分装规格已更新并全部回读核对：10ml = 同款正装售价 × 10%；5ml = × 5% + EUR0.50；按 HALF_UP 四舍五入到 EUR0.01。正装售价未写入，未修改库存。
- 已公开 `/pages/decants` 和 `/pages/boas-vindas`，主导航和页脚均已加入 Decants；页脚新增首单优惠说明入口。Decant 页面区分原装香水内容与 Scentory 准备的分装瓶，不宣称它是品牌官方小样。
- UNPUBLISHED 草稿主题 `207200584028` 已加入欢迎横幅、注册/订阅说明及英文/葡文翻译；尚未发布主题。主题发布必须由商家在 Shopify 后台完成。

## 到货提醒保持暂停

本分支保留已部署的 restock_alerts 代码，没有启用 Flow 或服务器提醒开关。草稿设置中的 `scentory_restock_url` 已清空，隐藏未完成的提醒入口。恢复任务时再确认正式邮件、唯一任务标签回执和受限测试，然后把接口恢复为 `https://scentory.eu.pythonanywhere.com/api/restock/`。

## 库存 App 规则部署

线上分装价已更新；部署 App 代码后，新建商品和后续价格同步才会持续使用新公式。库存同步不会改价格；价格同步会同时校准同一 Shopify 产品已有的分装规格，不会写到另一商品。不要为本次更新从本机旧数据库推送库存，也无需重复全量同步价格。

PythonAnywhere Bash（保留现有 .env）：

```bash
cd ~/InventoryApp &&
workon inventory &&
python manage.py backup_db &&
git fetch origin &&
git merge --ff-only origin/codex/welcome-decants-20261101 &&
python manage.py migrate &&
python manage.py check
```

然后 Web → Reload。该分支以已部署的提醒分支为基础并合入已提交 origin/master，不会删除旧提醒模块。若快进合并失败或服务器有冲突，命令会停下；先处理版本差异，不重置线上目录。整个过程中保持提醒的两个启用开关为 0。

## 发布欢迎提示

Shopify → Online Store → Themes → 找到 `Scentory - SEO GEO 24-10-2026` → Preview → Publish。

折扣、分装价格、内容页和菜单通过原生数据 API 更新，已可用于当前主题；欢迎横幅及表单提示属于草稿主题，发布后才显示。

## 验证与备份

- 26 项定价、创建格式及分装库存相关测试通过；新比例、加价、分币舍入、已有正装价格未变时校准分装、dry-run 和幂等行为均有覆盖。
- 只读独立代码审查未发现 Critical / Important 问题。
- 10 个草稿变更文件通过 Liquid 校验并完成 API 回读，420 个分装价格无差异，正装价格与修改前一致。自有测试客户的欢迎分群资格读取为 true，未下测试订单或发送邮件。
- 原始商品、优惠和主题备份保存在本机 `.tmp/geo/campaign-20261101/`，不包含服务器密钥或数据库。
- 自动读取公开页面遇到 HTTP429 限流，因此未把浏览器视觉效果当作已实测通过；页面发布状态、内容和菜单已通过 Shopify API 核实。
