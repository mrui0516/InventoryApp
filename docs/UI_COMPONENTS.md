# UI 组件与设计约定（UI Components）

> 单一样式来源是 `stock/static/css/app.css`。**新页面一律用本文列出的共享类拼装，不要再写页面内联 `<style>`。**
> 配合 [ARCHITECTURE.md](./ARCHITECTURE.md) 阅读。改动 app.css 后部署需 `collectstatic`（见 [DEPLOY.md](./DEPLOY.md)）。

---

## 1. 四条硬标准（每个页面都必须满足）

1. **弹窗不得超出屏幕**：用 `modal-dialog-scrollable`（+ `modal-dialog-centered`），主操作按钮必须始终可见。超高媒体（摄像头预览等）限高 ≤38vh。自定义弹窗必须自带 `max-height` 且由内容区滚动。
2. **信息密度优先**：不用高卡片堆叠；不用"一个字段占一整行"的手机表格；删掉没有数据的说明性副标题。一条记录 = 一行紧凑行。
3. **按断点分别适配**：手机端通常是**换结构**（紧凑行），不是把字调小。**6 列以上的表格在手机上一律不可接受。**
4. **处理边界情况**：长文本截断而非撑破布局（注意 grid/flex 子项默认 `min-width:auto`，必须显式 `min-width:0`）、空状态、0 与超大数值仍要对齐、提示自动消失。

---

## 2. 页面骨架

```django
{% extends 'stock/base.html' %}
{% block title %}页面名{% endblock %}
{% block content %}
<div class="page-shell">

  <section class="page-card pad">
    <div class="page-head">
      <div><h1 class="page-title">标题</h1></div>
      <div class="dash-actions">…按钮…</div>
    </div>
  </section>

  <section class="page-card pad">
    …筛选 / 内容…
  </section>

</div>
{% endblock %}
```

- **页面不要自己放 Back 按钮**：base.html 的 Back 条已在每页顶部（规则见 ARCHITECTURE 6.2）。表单页的 Cancel 加 `data-nav-back`；保存/删除后要回到来处的表单加 `data-return="back"`（列表页内联的表单用 `"here"`），删除表单再加 `data-return-skip="<被删记录的路径前缀>"`；视图用 `_back_to()` 跳转。
- **列表页的搜索与筛选**：`{% include "stock/_fbar.html" with placeholder="…" panel="fb-panel" on=filters_on %}`（搜索框 + Filters 按钮；`{% keep_params %}` 让搜索保留其他筛选）；分类用 `nav.ftabs`；在用的筛选用 `.fchips` > `a.fchip`（链接即"去掉这一个"）+ `a.fchip-clear`；面板内容写在 `<template id="fb-panel">`，每组 `.fopts section` > `h6` + `.fopt-list` > `a.fopt(.on)`。链接一律用 Django 自带 `{% querystring key=value page=None %}` 生成。**筛选不要用实心按钮**：实心/描边按钮只留给操作（新增、保存）。
- **记录详情用侧边面板，不要再做居中弹窗**：链接或行加 `data-panel="{% url '..._panel' id %}"`（保留 `href` 指向完整页面），或 `data-layer="<template id>"`；片段只写 `data-part="title|body|foot"` 三段，用 `sp-kpis` / `sp-kv` / `sp-sec` + `sp-row` 排版（ARCHITECTURE 6.3）。
- `page-shell` 纵向栅格容器 · `page-card` 卡片面板 · `pad` 内边距
- `page-head` / `section-head` / `strip-head`：左标题右操作，自动换行
- 标题：`page-title`（h1）、`section-head h2/h3`；小标签用 `kicker`、`toolbar-label`

---

## 3. 共享组件速查

| 用途 | 类名 |
|---|---|
| 卡片面板 | `page-card` `dash-card` `panel` `operation-card`（进出库） |
| 页头/区头 | `page-head` `section-head` `section-top` `strip-head` `operation-card-head` |
| 指标块 | `snapshot-grid` + `snapshot-card`，内含 `metric-label` `metric-value` `metric-sub`；紧凑版 `today-kpis` + `today-kpi`（`.k` `.v` `.s`） |
| 统计小胶囊 | `note-pill` `pill` `chip` `status-badge` `summary-badge` `brand-pill` `stock-pill` |
| 表格 | 外层 `table-wrap` + `table-responsive`，表 `table`；**手机端加 `compact-rows`**（见下） |
| 表单/筛选 | `toolbar` `toolbar-grid` `toolbar-label` `check-row` `form-actions` |
| 产品行 | `inline-product` + `inline-product-thumb(-wrap/-empty)`、`product-block` `thumb` |
| 订单卡（今日/历史） | `order-card` `order-card-head/meta/items/actions` `order-item*` |
| **订单行明细** | `ord-items` + `{% include "stock/_daily_order_item.html" %}`，超过 3 条用 `ord-more` 折叠（/today 与销售记录页共用） |
| 空状态/弱化文字 | `tiny` `muted` `sub` |

**数字对齐**：金额/数量列加 `num`（或 `text-end`）即得等宽数字，不会跳动。

---

## 4. `compact-rows` —— 手机端表格的统一做法

宽表在手机上无法使用，旧的"卡片化表格"又太占高度。统一用这个：

```html
<table class="table compact-rows">
  <thead><tr><th>客户</th><th>时间</th><th>金额</th></tr></thead>
  <tbody>
    <tr>
      <td>Ana Silva</td>                          <!-- 第一格 = 行标题 -->
      <td data-label="">16:42</td>
      <td data-label="Total" class="num">EUR 54.00</td>
    </tr>
  </tbody>
</table>
```

手机上自动变成：第一格作粗体标题独占一行，其余单元格按 `data-label` 折成一排小字。桌面端保持正常表格。

**要点**：把最有辨识度的列放第一位（客户名、订单号、产品名），不要放时间戳。

---

## 4b. 高密度记录列表(Orders 版式)—— 列表页的参照(2026-10-16)

老板指定:**Orders 页的这种信息密度和表格排版,以后很多列表页都要用。** 新做或改造客户、商品、供应商、IOU、进货等列表时,从这套结构起步,而不是卡片。

| 部分 | 做法 |
|---|---|
| 查询栏 | 一个搜索框 + 日期 + 快捷范围(分段按钮);低频条件收进 "More filters";生效条件显示为可 × 的 chip |
| 汇总 | **指标卡**,不是一行文字:小号大写标签 + 大号数字,首张(主指标)深色填充;付款等构成用**按比例分段的色条** + 金额图例 |
| 电脑端记录 | 一条记录一行紧凑"表格行"(时间 · 编号 · 对象 · 件数 · 金额 · 状态),浅色表头行;日期作分组标题并带当天合计;最右一列窄操作链接 |
| 手机端记录 | 同一条记录变**两行**(编号 · 时间 · 金额 / 对象 · 件数 · 状态),**整行可点** |
| 详情 | 一个共享的 `modal-dialog-scrollable modal-fullscreen-sm-down` 详情层,内容来自每行自带的 `<template>`;手机全屏、页面不跳转,关闭后停在原位 |
| 分页 | "Load N more · M left" 追加,分组标题不重复 |

**字号下限**(老板反馈"有些字体过小"):行内正文约 .95rem,次要信息不低于 .85rem,大写标签不低于 .74rem;次要文字用 `--app-muted` / `--app-primary`,不要自造更浅的灰。

已采用:Orders(`sales_records.html` + `_orders_rows.html`)、Customers 手机端(`customer_search.html`)、Order Correction Center(`sale_order_correction_center.html`,整行直接进修正表单,不用详情层)。

> **待办**:这套样式目前还内联在 `sales_records.html` / `customer_search.html` 里(`op-*` / `cl-*`),与本文"不写页面内联 style"的约定不符。下一个页面采用它时,把行、指标卡、付款色条、详情层的样式提到 `app.css` 作为共享类,再让三处一起引用。

## 5. 设计令牌（不要写死颜色）

颜色/圆角/阴影全部走 `app.css` 的 `--app-*` 变量(2026-10-26 起为石色主题:`--app-bg:#fafaf9`、文字 `#0c0a09`、边框 `#e8e6e5`;**`--app-accent:#1677bd` 用于按钮/选中态等白字底色,`--app-accent-bright:#3ba6f1` 只用于图表、色条等数据色**,不要在亮青蓝上写白字):

```
--app-bg --app-surface --app-surface-2 --app-text --app-muted
--app-border --app-border-strong
--app-accent --app-accent-ink --app-accent-soft --app-accent-line
--app-good --app-warn --app-danger（各有 -soft 版本）
--app-radius --app-radius-sm --app-radius-xs --app-shadow --app-shadow-md
--sidebar-w --tabbar-h
```

`--tabbar-h` 很重要：它给页面底部留出标签栏的高度。**吸底元素要写 `bottom:calc(var(--tabbar-live) + .5rem)`**(2026-10-25 起):底栏在滚动/输入时会收起,`body.tabbar-away` 把 `--tabbar-live` 设为 0,吸底条随之贴底;`--tabbar-h` 本身不变,否则每次滚动页面都会跳。

**静态文件链接(2026-10-25)**:base.html 里的 CSS/JS 一律用 `{% load static_version %}{% static_v 'css/app.css' %}`,不要用裸的 `{% static %}`——线上不发 Cache-Control,不带版本号的话浏览器会一直用旧文件。

**共用部件(2026-10-25)**:`_add_customer_modal.html`(新建客户,`after='event'` 时只派发 `customer:created` 事件由页面处理,默认刷新)、`_low_stock_panel.html` + `#alertsPanel`(提醒侧栏,任意按钮加 `data-bs-toggle="offcanvas" data-bs-target="#alertsPanel"` 即可打开,加 `data-ls-band="low12"` 指定分组)、`_wa_icon.html` + `contact_tags.whatsapp_url` 过滤器(`.wa-link` / `.btn-wa`)。

`position:sticky` 只在它的祖先都不裁剪溢出时才生效:祖先上的 `overflow:hidden`/`auto`(哪怕只是 `overflow-x:hidden`)会把该祖先变成一个"永远不滚动的滚动容器",吸底条就停在原地不动。手机端 `.erp-content` 为此用 `overflow-x:clip`(2026-10-24 前是 `hidden`,Add/Edit Product 和 Correct Order 的吸底保存条在手机上一直没吸住)。防止横向溢出时用 `clip`,不要用 `hidden`。

旧页面里的 `--ds-*` / `--pl-*` / `--bg` 等都是指向同一套令牌的兼容别名，**新代码不要再用**。

---

## 6. 现状与遗留（2026-08-20）

- 共享层 `app.css` 约 36 KB / 180 个类；**页面内联 CSS 约 137 KB，分布在 30 个模板**。
- 与 app.css 冲突的选择器：**首轮清理前 57 个 → 现在 43 个**。已清掉：8 条与 app.css 逐字相同的重复规则，以及 6 个页面对核心元素的私自改写（`.btn` 圆角、`.btn-primary` 光晕、写死颜色的 `.page-title`、重复的 `.pos-title`）。
- 余下 43 个多为**有意的页面变体**（`.chip`/`.pill` 尺寸、`.table thead th` 配色、打印用 `.pos-title`），需逐页目视确认后再并入或改名收敛。
- 因此：**新页面按本文写即可**；旧页面要逐个把内联样式并入 app.css 才能真正统一，需逐页目视确认，属于渐进任务。

优先清理顺序（按内联体积）：`outbound` 22KB → `sales_records` 20KB → `customer_detail` 16KB → `inbound` 10KB → `customer_search` 9KB → `catalog` 9KB。
