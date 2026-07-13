# 技术债 TODO

> 来源：[STATUS.md](./STATUS.md) 第 8 节"建议的下一步"，经讨论后细化为可执行任务清单。
> 每完成一项请勾选对应复选框，并按任务说明同步更新 `docs/STATUS.md`（及必要时 `docs/ARCHITECTURE.md`）。
> 完成的项目会在文末"完成记录"表中登记日期与说明。

## 优先级说明
- 🔴 高：仓库已是 **public**，存在安全/环境风险，建议尽快处理
- 🟡 中：影响协作效率，建议近期处理
- ⚪ 低：代码整洁/精简类，风险低，可随时处理（均在 git 中可回滚）

---

## 🔴 1. SECRET_KEY / DEBUG 迁移到环境变量

**背景**：`github.com/mrui0516/InventoryApp` 为 public 仓库，`inventory_system/settings.py` 中硬编码的 `SECRET_KEY` 已暴露在 git 历史中，视为已泄露，需要轮换。

**方案**：`python-dotenv` + `.env`

- [ ] `requirements.txt` 新增 `python-dotenv`
- [ ] 项目根目录新建 `.env`（存放真实值；`.gitignore` 中已有 `.env` 规则，无需新增）
- [ ] 新建 `.env.example`（占位模板，提交到仓库，供新协作者参考）
- [ ] `settings.py` 改造：
  - [ ] `load_dotenv()`
  - [ ] `SECRET_KEY = os.environ['SECRET_KEY']`
  - [ ] `DEBUG = os.environ.get('DEBUG', 'False') == 'True'`
- [ ] 生成一个新的随机 `SECRET_KEY`（旧 key 视为已泄露并轮换，不再使用）
- [ ] 同步更新 `docs/ARCHITECTURE.md`（技术栈表新增 `python-dotenv`）与 `docs/STATUS.md`（第 6 节风险表移除该条，第 7 节记录变更）

> ⚠ **不在本任务范围内**：旧 `SECRET_KEY` 仍会留存于 git 历史中。是否需要用 `git filter-repo` 等工具重写历史彻底清除，属于破坏性操作，需单独讨论并取得明确同意后才执行。

---

## 🔴 2. 修复 `.venv` 本地虚拟环境

**背景**：`.venv` 目前只装了 `pip`/`Pillow`/`openpyxl`，缺 Django，实际运行依赖系统级 Python 3.13。新协作者按 `.venv` 操作会直接失败。

- [ ] `.venv\Scripts\pip install -r requirements.txt`
- [ ] `.venv\Scripts\python manage.py check`
- [ ] `.venv\Scripts\python manage.py test stock`（确认 52 个测试仍全部通过）
- [ ] 更新 `docs/STATUS.md` 第 3 节"运行环境状态"，标记 `.venv` 已修复并去掉 ⚠

---

## 🟡 3. 提交文档到 git

- [ ] `git add docs/PRD.md docs/ARCHITECTURE.md docs/STATUS.md docs/TODO.md`
- [ ] `git commit`（建议与第 1、2 项的改动一起或分开提交，提交前会与你确认）

---

## ⚪ 4. 数据备份方案（待设计，暂不实现）

讨论时决定暂缓，仅记录待定问题，留待后续单独讨论：

- [ ] 待定：备份目标位置（本地另一目录 / 外接盘 / 云同步文件夹）
- [ ] 待定：备份脚本内容（复制 `db.sqlite3` + 打包 `media/`，文件名带时间戳，保留最近 N 份）
- [ ] 待定：调度方式（Windows 任务计划程序 / 手动运行）

---

## ⚪ 5. 低优先级代码清理

- [x] 验证并删除死代码：`apply_fifo`、`_legacy_dashboard_view`、`_legacy_record_view`、`_legacy_customer_detail_view`（已 grep 确认无引用，共删 469 行）
- [x] 删除一次性脚本 `stock/management/commands/addsale_before.py`（及其 `.pyc`）
- [x] 移除 `requirements.txt` 中未使用的 `pandas`/`numpy`/`matplotlib` 及其专属传递依赖（contourpy/cycler/fonttools/kiwisolver/pyparsing）
- [x] 删除根目录残留临时文件 `tmp_product_export.xlsx`、`tmp_seg.txt`
- [x] 同步更新 STATUS/ARCHITECTURE/PRD 技术债条目（已移除已解决项）
- 额外：清理 `views.py`/`admin.py` 未使用 import（ARItemForm/ARItemFormSet/ARPayment/ProductSeries/restore_stock_fifo/StockConflictError、mark_safe/Sum/Decimal）

---

## 完成记录

| 日期 | 项目 | 说明 |
|---|---|---|
| | | |
