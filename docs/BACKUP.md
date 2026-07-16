# 数据备份

> 目标：U 盘（`F:`）是 db.sqlite3 + media/ 的唯一活动副本，U 盘损坏/丢失即数据全失。
> 本方案在**这台电脑的 C 盘**留一份每日副本，先挡住最常见的 U 盘故障。

## 现状

**已启用：本地每日备份（C 盘）。**

| 项 | 值 |
|---|---|
| 脚本 | `C:\Users\maoru\InventoryApp-Backups\backup_inventory.py` |
| 备份位置 | `C:\Users\maoru\InventoryApp-Backups\`（`db\` 快照 + `media\` 镜像 + `backup.log`） |
| 调度 | Windows 计划任务 `InventoryDailyBackup`，每天 21:00，关机错过则开机补跑 |
| 数据库 | 每天一个带时间戳的**一致性快照**（SQLite 在线备份 API，边写边备也安全），保留 30 天 |
| 图片 | robocopy `/MIR` 镜像，始终等于当前 media/（单份，非逐日复制） |
| 防呆 | U 盘没插 / 源为空时**跳过、绝不动**已有备份（避免把好备份反向清空） |

**未做：异地副本（Google 云盘）。** 这台电脑失火/被偷则本地备份同亡。需你装一次
Google Drive 桌面版并登录（浏览器授权只有你能做），之后把每日备份也落到云盘同步夹即可拿到异地保护。见文末。

## 手动跑一次

```
"C:\Users\maoru\AppData\Local\Programs\Python\Python313\python.exe" "C:\Users\maoru\InventoryApp-Backups\backup_inventory.py"
```

## 恢复步骤（重要）

**恢复数据库：**
1. 关掉服务器（关 `start.bat` 的窗口）——不能在服务器运行时覆盖数据库。
2. 从 `C:\Users\maoru\InventoryApp-Backups\db\` 选一个 `db-YYYY-MM-DD_HHMM.sqlite3`。
3. 复制它覆盖 `F:\APP\InventoryApp\db.sqlite3`（覆盖前建议把当前那个先改名留底）。
4. 重新双击 `start.bat`。

**恢复图片：**
- 把 `C:\Users\maoru\InventoryApp-Backups\media\` 里的内容复制回 `F:\APP\InventoryApp\media\`。

**验证**（可选）：用备份库查条数是否合理
```
python -c "import sqlite3;c=sqlite3.connect(r'<备份库路径>');print(c.execute('PRAGMA integrity_check').fetchone(), c.execute('SELECT COUNT(*) FROM stock_saleorder').fetchone())"
```

## 调整

- **改时间**：任务计划程序 → `InventoryDailyBackup` → 触发器。
- **改保留天数**：脚本顶部 `KEEP_DAYS`（默认 30）。
- **换备份盘**：脚本就放在备份目录里，把整个 `InventoryApp-Backups` 文件夹移到别处（如 D 盘），并在任务里改脚本路径即可。

## 待办：加异地（Google 云盘）

1. 装 [Google Drive 桌面版](https://www.google.com/drive/download/) 并登录（一次性）。
2. 告诉我同步文件夹路径，我把 `InventoryApp-Backups` 指到（或复制进）那个文件夹，
   每日备份就会自动同步上云 = 本地 + 异地两份。
3. ⚠ 云盘备份**未加密**（按你的选择：目的是防 U 盘坏，非防泄露）。里面含客户
   姓名/电话/NIF，请把该云盘文件夹保持**私有、不分享**。日后要加密可用 rclone crypt。
