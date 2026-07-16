# 技术债 TODO

> 来源：[STATUS.md](./STATUS.md) 第 8 节"建议的下一步"。
> 完成一项后请勾选，并同步更新 `docs/STATUS.md`（必要时 `docs/ARCHITECTURE.md`）。

## 优先级
- 🔴 高：安全/数据丢失风险
- 🟡 中：影响协作或易踩坑
- ⚪ 低：整洁类，随时可做

---

## 🔴 1. SECRET_KEY / DEBUG 迁移到 .env

**背景**：`github.com/mrui0516/InventoryApp` 是 **public** 仓库，`settings.py` 里硬编码的 `SECRET_KEY` 已在 git 历史中，视为**已泄露，必须轮换**。

**现状**：`settings.py` 顶部**已有手写的 `.env` 加载器**（读 `BASE_DIR/.env`，真实环境变量优先），Cloudinary 的密钥已经走这条路。所以不需要引入 `python-dotenv`，只需把这两个值搬进去。

- [ ] 生成一个新的随机 `SECRET_KEY`（旧的视为已泄露，不再使用）
- [ ] 新 key 写入 `.env`；`settings.py` 改为 `SECRET_KEY = os.environ['SECRET_KEY']`
- [ ] `DEBUG = os.environ.get('DEBUG', 'False') == 'True'`，`.env` 里本机设 `DEBUG=True`
- [ ] 新建 `.env.example`（占位模板，提交到仓库，供新环境参考）
- [ ] 同步更新 `docs/STATUS.md`（第 6 节移除该风险、第 7 节记一行）

> ⚠ **不在本任务范围内**：旧 `SECRET_KEY` 仍留在 git 历史中。是否用 `git filter-repo` 重写历史彻底清除属破坏性操作，需单独讨论并取得明确同意。

---

## 🔴 2. 数据备份 + 推送到远程

**背景**：`db.sqlite3` + `media/`（79MB）此前**无任何备份**；且本地领先 `origin/master` **34 个提交**，GitHub 上没有近期工作 → 代码也没有异地副本。

- [x] **本地每日备份**已上线（C 盘，计划任务 `InventoryDailyBackup`，每天 21:00）。详见 [BACKUP.md](./BACKUP.md)。
- [ ] **异地副本**：装 Google Drive 桌面版并登录，把每日备份也落到云盘同步夹（防本机失火/被偷）。见 BACKUP.md 文末。
- [ ] `git push` 把 34 个提交推到 GitHub（先确认没有密钥被误提交）

---

## 🔴 3. 轮换 Cloudinary API Secret

**背景**：密钥明文存于 `F:\APP\InventoryApp\.env`（U 盘上，git-ignored）。U 盘丢失/被盗即泄露。

- [ ] 在 Cloudinary 控制台 Regenerate API Secret
- [ ] 更新 `.env` 中的 `CLOUDINARY_API_SECRET`
- [ ] 重启 `start.bat`，上传一张图验证自动同步仍正常

---

## 🟡 4. 统一运行环境（`.venv`）

**背景**：项目实际有三个 Python 环境（便携版 = 服务器在用；系统 Python = 跑测试；`.venv` = **只有 Pillow/openpyxl，没有 Django，不能用**）。按 `.venv` 操作会直接失败。

二选一：

- [ ] 修复：`.venv\Scripts\pip install -r requirements.txt`，然后 `.venv\Scripts\python manage.py test stock`（应 147/147 通过）
- [ ] 或删除：直接删掉 `.venv/` 目录以免误导（已在 `.gitignore`，不影响仓库）
- [ ] 完成后更新 `docs/STATUS.md` 第 3 节
