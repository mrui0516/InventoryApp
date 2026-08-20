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

## 5. 设计令牌（不要写死颜色）

颜色/圆角/阴影全部走 `app.css` 的 `--app-*` 变量：

```
--app-bg --app-surface --app-surface-2 --app-text --app-muted
--app-border --app-border-strong
--app-accent --app-accent-ink --app-accent-soft --app-accent-line
--app-good --app-warn --app-danger（各有 -soft 版本）
--app-radius --app-radius-sm --app-radius-xs --app-shadow --app-shadow-md
--sidebar-w --tabbar-h
```

`--tabbar-h` 很重要：任何吸底元素都要写 `bottom:calc(var(--tabbar-h) + .5rem)`，否则手机上会被底部标签栏挡住。

旧页面里的 `--ds-*` / `--pl-*` / `--bg` 等都是指向同一套令牌的兼容别名，**新代码不要再用**。

---

## 6. 现状与遗留（2026-08-20）

- 共享层 `app.css` 约 36 KB / 180 个类；**页面内联 CSS 约 137 KB，分布在 30 个模板**。
- 与 app.css 冲突的选择器：**首轮清理前 57 个 → 现在 43 个**。已清掉：8 条与 app.css 逐字相同的重复规则，以及 6 个页面对核心元素的私自改写（`.btn` 圆角、`.btn-primary` 光晕、写死颜色的 `.page-title`、重复的 `.pos-title`）。
- 余下 43 个多为**有意的页面变体**（`.chip`/`.pill` 尺寸、`.table thead th` 配色、打印用 `.pos-title`），需逐页目视确认后再并入或改名收敛。
- 因此：**新页面按本文写即可**；旧页面要逐个把内联样式并入 app.css 才能真正统一，需逐页目视确认，属于渐进任务。

优先清理顺序（按内联体积）：`outbound` 22KB → `sales_records` 20KB → `customer_detail` 16KB → `inbound` 10KB → `customer_search` 9KB → `catalog` 9KB。
