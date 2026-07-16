# 数据备份

> 目标：U 盘（`F:`）是 db.sqlite3 + media/ 的唯一活动副本，U 盘损坏/丢失即数据全失。
> 本方案在**这台电脑**留一份每日副本，挡住最常见的 U 盘故障。

## 现状

**已启用：本地每日备份（Downloads 文件夹）。**

| 项 | 值 |
|---|---|
| 备份位置 | `C:\Users\maoru\Downloads\InventoryApp-Backups\`（`db\` 快照 + `media\` 镜像 + `backup.log` + 脚本本身） |
| 脚本 | 同目录下 `backup_inventory.py` |
| 调度 | Windows 计划任务 `InventoryDailyBackup`，每天 21:00（`pythonw` 静默跑），关机错过则开机补跑 |
| 数据库 | 每天一个带时间戳的**一致性快照**（SQLite 在线备份 API，边写边备也安全），保留 30 天 |
| 图片 | robocopy `/MIR` 镜像，始终等于当前 media/（单份，非逐日复制） |
| 防呆 | U 盘没插 / 源为空时**跳过、绝不动**已有备份（避免把好备份反向清空） |

⚠ **别清空这个文件夹**：Downloads 是常被整理清空的地方，若你全选清理会把备份一起删掉。
清理 Downloads 时绕开 `InventoryApp-Backups` 子文件夹即可。

## 手动跑一次

```
"C:\Users\maoru\AppData\Local\Programs\Python\Python313\python.exe" "C:\Users\maoru\Downloads\InventoryApp-Backups\backup_inventory.py"
```

## 恢复步骤（重要）

**恢复数据库：**
1. 关掉服务器（关 `start.bat` 的窗口）——不能在服务器运行时覆盖数据库。
2. 从 `...\Downloads\InventoryApp-Backups\db\` 选一个 `db-YYYY-MM-DD_HHMM.sqlite3`。
3. 复制它覆盖 `F:\APP\InventoryApp\db.sqlite3`（覆盖前建议把当前那个先改名留底）。
4. 重新双击 `start.bat`。

**恢复图片：**
- 把 `...\Downloads\InventoryApp-Backups\media\` 里的内容复制回 `F:\APP\InventoryApp\media\`。

**验证**（可选）：用备份库查条数是否合理
```
python -c "import sqlite3;c=sqlite3.connect(r'<备份库路径>');print(c.execute('PRAGMA integrity_check').fetchone(), c.execute('SELECT COUNT(*) FROM stock_saleorder').fetchone())"
```

## 调整

- **改时间**：任务计划程序 → `InventoryDailyBackup` → 触发器。
- **改保留天数**：脚本顶部 `KEEP_DAYS`（默认 30）。
- **换备份盘**：把整个 `InventoryApp-Backups` 文件夹移到别处，并在计划任务里改脚本路径即可（备份就落在脚本所在目录）。

## 局限与可选增强

- **只在本机**：备份和 U 盘在同一台电脑/同一地点，能防 U 盘坏，但防不了这台电脑失火/被偷。
- **异地副本（可选）**：本机已装 Google Drive 桌面版。若把 Downloads 文件夹设为同步到
  Google Drive（Drive 设置 → 「我的电脑」→ 添加 Downloads 文件夹），备份就会自动上云 = 本地 + 异地。
  ⚠ 云端**未加密**，库里含客户姓名/电话/NIF，那个云盘请保持私有、不分享。要加密可用 rclone crypt。
