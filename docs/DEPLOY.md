# 上线部署手册 — PythonAnywhere（欧盟区）

把这套 Django 库存系统上线,让你和员工在**任意设备的浏览器**里登录使用。
托管平台:**PythonAnywhere 欧盟区**(`eu.pythonanywhere.com`,法兰克福 / AWS 欧盟机房,GDPR 合规)。
访问地址:`https://gestao.scentory.pt`(`scentory.pt` 子域名,域名在 **Amen** 管理)。

> ⚠️ 一定注册 **`eu.pythonanywhere.com`**(欧盟区),不是 `www.pythonanywhere.com`(美国区)。有客户姓名等个人数据,GDPR 要求数据留在欧盟。

代码侧的生产安全加固(`inventory_system/settings.py` 的 `if not DEBUG` 块)已经就绪 —— 只在 `DEBUG=False`(线上)生效,本地开发不受影响。本手册是**平台侧**的分步操作。

---

## 阶段 0 · 准备
1. 注册 **eu.pythonanywhere.com** 账号,选 **Developer 档($10/月,当前最便宜的付费档)**。免费档不行:不能绑自定义域名、外网只给白名单(会挡掉 Shopify/Cloudinary 调用)、没有定时任务(备份)。可先用免费档试跑,确认能登录后再升级。不需要更贵的 Custom 档。
2. **给 PythonAnywhere 账号本身开 2FA**(Account → Security)。这个账号被盗比系统被攻破更致命。
3. 确认 **Amen 上的 `scentory.pt` 会自动续费**、绑好付款卡 —— 域名一旦被回收,店铺和后台一起没。

## 阶段 1 · 把代码放上去
PythonAnywhere → **Consoles** → 开一个 **Bash** 控制台:
```bash
git clone https://github.com/mrui0516/InventoryApp.git
cd InventoryApp
# 建虚拟环境（Python 3.11+，Django 5.2 需要 3.10 以上）
mkvirtualenv --python=/usr/bin/python3.11 inventory
pip install -r requirements.txt
```

## 阶段 2 · 配置环境变量（线上密钥,绝不进 git）
生成一个真正的 SECRET_KEY:
```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```
在项目根目录建 `.env`(已被 `.gitignore` 忽略):
```ini
DEBUG=False
SECRET_KEY=<上面生成的那串>
ALLOWED_HOSTS=gestao.scentory.pt
CSRF_TRUSTED_ORIGINS=https://gestao.scentory.pt
# 可选：HSTS 先保持默认 3600s 起步；稳定后再逐步调大到 31536000
```
> 注意:`DEBUG=False` 会自动开启 `settings.py` 里的全部生产安全项(强制 HTTPS、安全 Cookie、HSTS、防点击劫持等)。

## 阶段 3 · 数据库 & 静态文件
```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser        # 建你自己的管理员账号
```
**数据库选择**:小团队先继续用 **SQLite** 最省事(PythonAnywhere 文件系统持久,不会丢);等并发变大再考虑迁到 PA 的 **MySQL**(Postgres 在 PA 是付费附加项)。

## 阶段 4 · 配置 Web App
PythonAnywhere → **Web** → **Add a new web app** → 选 **Manual configuration**(不是自动 Django) → 选同一个 Python 版本。然后:
- **Virtualenv**:填 `inventory` 这个虚拟环境路径(`/home/<你的用户名>/.virtualenvs/inventory`)。
- **Source code / Working directory**:`/home/<你的用户名>/InventoryApp`。
- **WSGI 配置文件**(点它编辑),改成指向本项目:
  ```python
  import os, sys
  path = '/home/<你的用户名>/InventoryApp'
  if path not in sys.path:
      sys.path.insert(0, path)
  os.environ['DJANGO_SETTINGS_MODULE'] = 'inventory_system.settings'
  from django.core.wsgi import get_wsgi_application
  application = get_wsgi_application()
  ```
- **Static files** 映射:
  | URL | Directory |
  |-----|-----------|
  | `/static/` | `/home/<你的用户名>/InventoryApp/staticfiles` |
  | `/media/`  | `/home/<你的用户名>/InventoryApp/media` |
- 点 **Reload**。先用 `https://<你的用户名>.eu.pythonanywhere.com` 验证能打开、能登录。

## 阶段 5 · 绑定 `gestao.scentory.pt`
1. PythonAnywhere → **Web** → **Add a custom domain** 填 `gestao.scentory.pt`。PA 会给你一个 **CNAME 目标**(形如 `webapp-XXXX.eu.pythonanywhere.com`)。
2. 去 **Amen → scentory.pt → Gestão de DNS**,**新增**一条(别改现有 www/@ 的 Shopify 记录):
   ```
   Tipo: CNAME
   Nome: gestao
   Valor: webapp-XXXX.eu.pythonanywhere.com   ← PA 给的那个
   ```
3. 回 PA 的 Web 页,启用 **HTTPS certificate**(自动 Let's Encrypt)+ 打开 **Force HTTPS**。
4. 生效后打开 `https://gestao.scentory.pt` 确认锁图标正常。

## 阶段 6 · 员工账号
- 每个员工**独立账号**(禁止共用登录)。经理/员工权限分级用系统里已有的机制。
- 员工离职时能**立即停用**其账号。

## 阶段 7 · 备份（必做,平台再方便也省不掉）
遵循 **3-2-1**:≥3 份、2 种介质、1 份异地。PythonAnywhere → **Tasks** 加一个每日定时任务:
```bash
# SQLite：直接拷一份带日期的副本
cd /home/<你的用户名>/InventoryApp && cp db.sqlite3 backups/db-$(date +\%Y\%m\%d).sqlite3
# （或 MySQL：mysqldump ... > backups/db-$(date +\%Y\%m\%d).sql）
```
再**定期把 `backups/` 下载一份到你本地**(离线保险 —— 这刚好满足"手里留一份数据"的需求,角色是备份而非主库)。可进一步加密后传到 Backblaze B2 / S3。

## 阶段 8 · 认证加固（下一段代码,单独做）
公网登录页面必须比现在更硬。这一段会改动登录流程,单独作为一次带测试的改动:
- **django-axes**:登录失败次数限制 + 锁定,挡暴力破解。
- **django-two-factor-auth**:至少管理员账号强制 **2FA**(手机验证码)。

---

## 上线前自检
```bash
# 模拟生产跑 Django 部署自检（应只剩 HSTS 子域/preload 提示，属可接受的渐进项）
DEBUG=False python manage.py check --deploy
```
- ✅ `https://gestao.scentory.pt` 能打开、锁图标正常、http 自动跳 https。
- ✅ 登录后 12 小时无操作会自动登出(`SESSION_COOKIE_AGE`)。
- ✅ 定时备份任务已配、并已成功下载过一次到本地。
- ✅ PythonAnywhere 账号、Amen 域名都开了自动续费 / 2FA。

> `check --deploy` 剩下的 `security.W005`(HSTS 子域)、`W021`(preload)是**故意**先关的渐进项;`W009`(SECRET_KEY)只在你用了短测试 key 时出现,线上用生成的 50+ 位随机 key 就不会有。
