# 数据备份（云上线后）

> **系统已上线到 PythonAnywhere,PA 上的 `db.sqlite3` 是唯一的准数据(source of truth)。**
> 备份目标:PA 每天自动生成一致性快照;你的电脑再把最新快照拉到 **U 盘**。
> 达成 **3-2-1**:PA 实时库 + PA 每日快照 + U 盘离线副本。

> ⚠️ 旧的"本地 app + Downloads 每日备份"方案**已退役**(那是本地 USB 当主库时代的)。现在数据在 PA,只在网站上录入,别再用本地 app 记数据。

## 一键手动备份(仪表盘按钮)

除了下面的自动备份,仪表盘顶部有个 **"Download DB backup"** 按钮(仅经理可见):点一下就下载**当前数据库的一致性快照**(文件名带日期)。想直接落 U 盘,把浏览器下载目录设成 U 盘那个文件夹即可。适合"现在就想抓一份 / 做危险操作前手动存一下"。恢复就是把这个文件放回 PA 覆盖 `db.sqlite3` 再 Reload(见下方恢复步骤)。

## 架构(自动)

| 层 | 在哪 | 做什么 | 频率/保留 |
|---|---|---|---|
| 1. 云端快照 | PythonAnywhere | `manage.py backup_db` → `~/InventoryApp/backups/db-<时间戳>.sqlite3`(SQLite 在线备份 API,边写边备也一致;自带完整性校验) | 每天,保留 30 份 |
| 2. U 盘拉取 | 你的 Windows 电脑 | `scripts/pull_backup_from_pa.py` → 用 PA API 把**最新快照**下载到 U 盘 | 每天,保留 30 份;**没开机就下次开机补跑** |

## 一、PA 端:每日快照(一次性设置)

先在 Bash 控制台把最新代码拉下来(含 `backup_db` 命令):
```bash
cd ~/InventoryApp && git pull
```
手动测一次:
```bash
/home/scentory/.virtualenvs/inventory/bin/python manage.py backup_db --keep 30
```
应看到 `Backup OK: .../backups/db-YYYYMMDD-HHMMSS.sqlite3`。

然后 PA 顶部 **Tasks** → 新建一个 **Scheduled task**,每天某个时间(如 UTC 时间,换算好你的当地时间),命令填:
```
cd /home/scentory/InventoryApp && /home/scentory/.virtualenvs/inventory/bin/python manage.py backup_db --keep 30
```

## 二、本地端:把最新快照拉到 U 盘(一次性设置)

1. 先 `git pull` 一下你本地仓库(拿到 `scripts/` 里的脚本)。
2. **拿 PA API token**:PythonAnywhere → **Account** → **API token** 标签 → 创建并复制。
3. **配置**:把 `scripts/.pa_backup.ini.example` 复制成 `scripts/.pa_backup.ini`,填入:
   - `token` = 上面那个 API token
   - `dest` = 你的 U 盘目录,例如 `F:\InventoryApp-Backups\db`
   （`.pa_backup.ini` 已被 git 忽略,token 不会进仓库。）
4. **手动测一次**(用你系统的 Python):
   ```
   "C:\Users\maoru\AppData\Local\Programs\Python\Python313\python.exe" "F:\APP\InventoryApp\scripts\pull_backup_from_pa.py"
   ```
   应打印 `Downloaded db-... -> F:\...`。
5. **挂到 Windows 任务计划程序**:
   - 任务计划程序 → 创建任务 → 触发器:每天(你选个时间);
   - 操作:程序 = 上面那个 `python.exe`,参数 = `"F:\APP\InventoryApp\scripts\pull_backup_from_pa.py"`;
   - ⚠️ 常规选项里勾上 **"错过计划的开始时间后,尽快启动任务"(Run task as soon as possible after a scheduled start is missed)** → 那天没开机,下次开机自动补跑;
   - 可勾"使用最高权限运行",避免 U 盘权限问题。

> U 盘没插 / 目标盘不可用时,脚本会**跳过、绝不删已有备份**,不会报错破坏。

## 三、恢复(重要)

**从某个快照恢复 PA:**
1. 从 U 盘(或 PA 的 `backups/`)选一个 `db-YYYYMMDD-HHMMSS.sqlite3`。
2. PA → Files → `/home/scentory/InventoryApp/` → 删掉当前 `db.sqlite3`(先改名留底更稳)→ 上传选中的快照 → **改名成 `db.sqlite3`**。
3. Web 标签 → **Reload**。

**验证快照是否完好**(可选):
```
python -c "import sqlite3;c=sqlite3.connect(r'<快照路径>');print(c.execute('PRAGMA integrity_check').fetchone(), c.execute('SELECT COUNT(*) FROM stock_saleorder').fetchone())"
```

## 调整
- **改保留份数**:两处的 `--keep 30` / `.pa_backup.ini` 的 `keep`。
- **改时间**:PA 的 Scheduled task 触发时间 / Windows 任务计划触发器。

## 局限 / 可选增强
- **图片(media)** 目前不在此备份内(体积大、且大多能重获)。以后需要可扩展脚本一并拉 media,或让新图上传时镜像到 Cloudinary 作二次副本。
- **异地加密副本(可选)**:U 盘那个目录可再同步到 Backblaze B2 / 加密云盘,防电脑失窃/失火。库里含客户姓名/电话/NIF,云端务必加密(如 rclone crypt)且不分享。
