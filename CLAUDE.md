# InventoryApp — 工作约定

Django 5.2 + SQLite 的库存 / POS / B2B 系统，服务两家香水店：Khan Perfume（默认店）和 Scentory。
部署在 PythonAnywhere，用户 `scentory`，代码在 `~/InventoryApp`，虚拟环境 `inventory`。
功能规则看 `docs/PRD.md`，技术结构看 `docs/ARCHITECTURE.md`，上线状态和变更记录看 `docs/STATUS.md`。

## 语言
- 回复用户一律中文；代码、提交信息、员工界面用英文；客户看到的页面和 PDF 用欧洲葡语。

## 在哪里改代码
- 不要直接改 `F:\APP\InventoryApp`：另一个会话（codex）也在用这个目录。
- 在临时目录 `git clone https://github.com/mrui0516/InventoryApp.git` 一份干净的代码，并把 `F:\APP\InventoryApp\.env` 复制进去。
- 临时目录会被系统清空，没提交的东西随时可能丢。

## codex（并行会话）
- 它会推 `origin/codex/*` 分支，也会直接推到 master。
- 动手前和推送前都要 `git fetch --all`，再看 `git log HEAD..origin/master` 和每个 `origin/codex/*`。有新提交就 `merge --no-ff`，然后重跑测试。
- 迁移编号冲突时，改我自己还没部署的那个迁移的编号，并把它的依赖指向对方的迁移。
- 不改 Shopify 文件（`shopify_*.py`、`shopify/`），不执行它文档里的 Shopify 部署步骤，不用 `--all`。

## 测试和提交
- Python 路径：`/c/Users/maoru/AppData/Local/Programs/Python/Python313/python.exe`，运行前设置 `PYTHONIOENCODING=utf-8`。
- 全量测试：`manage.py test stock restock_alerts --noinput`，约 50 秒（测试时用 MD5 密码哈希）。日志写进文件，只看 `Ran` / `OK` / `FAILED` 这几行。
- 还要跑 `makemigrations --check --dry-run`。
- 测试全部通过才提交，用 shell 判断日志里有 `^OK` 再执行 commit。提交信息结尾加 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。
- 每次改代码都同步 PRD / ARCHITECTURE / STATUS（STATUS 里的测试数、最新迁移、变更记录）。

## 界面改动
- 用 Playwright 脚本实测 360、390、820、1366 四种宽度：量对齐、溢出、重叠，用最长的文字试。不能只看截图。
- 条件出现的信息（标签、提示）不能让行变形或错位。
- 界面上不写功能介绍性的段落，只放标题、标签、数据和警告。

## 部署（每次交付都附 PA 命令）
1. 先检查：`cd ~/InventoryApp && git fetch && git log --oneline origin/master..master`，应该没有输出。
2. 再执行：`cd ~/InventoryApp && workon inventory && git pull --ff-only && python manage.py migrate && python manage.py check`
   - 迁移会改数据时，在 `migrate` 前加 `python manage.py backup_db`。
   - `stock/static` 有改动时加 `python manage.py collectstatic --noinput`。
3. 最后在 Web 页面点 Reload。

## 安全
- 删改数据的命令先用 dry run，经用户确认、做过备份再执行。
- 密钥只放在 `.env`，绝不写进代码，也不贴进对话。

## 上下文
- 一个任务一个会话。
- 做出的决定当下就写进记忆或 docs，不只留在对话里。
- 少截图，命令输出只取需要的那几行。
