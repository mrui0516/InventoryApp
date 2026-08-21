# 香水变量设计（Perfume Attributes）

> 面向阿拉伯香水零售：容量、浓度、香型、灵感来源。
> 配合 [ARCHITECTURE.md](./ARCHITECTURE.md)、[SHOPIFY_SYNC.md](./SHOPIFY_SYNC.md) 阅读。

## 1. 设计分三层

| 层 | 内容 | 为什么放这一层 |
|---|---|---|
| **1. 真实字段** | `Product.volume_ml`（数字）、`Product.concentration`（外键） | **代码依赖它们**：分装规则认「100ml 整瓶」、每毫升单价、Shopify 变体尺寸。必须是有类型的列，放进动态属性表会让查询又慢又脆 |
| **2. 可编辑分类表** | `Concentration`、`FragranceFamily`(多对多)、`Inspiration` | 取值会增长，但**结构固定**。做成表，店里自己就能加「Attar」「Body Mist」，不用改代码 |
| **3. 自定义属性**（待做） | 属性定义 + 属性值（留香、扩散度、季节、瓶型…） | 纯描述性、可筛选，结构不确定 → 交给用户自由定义 |

第 3 层尚未实现；1+2 层已上线。

## 2. 数据模型

```
Concentration      name / short(EDP) / shopify_tag / sort_order
FragranceFamily    name / shopify_tag / sort_order
Inspiration        house(Givenchy) + name(L'Interdit)   ← 唯一约束

Product.volume_ml           PositiveInteger, 可空, 有索引
Product.concentration       FK → Concentration (SET_NULL)
Product.fragrance_families  M2M → FragranceFamily
Product.inspired_by         FK → Inspiration (SET_NULL)
```

- **`volume_ml` 一旦填写就是权威**：`variant_label` 优先用它（显示 `100ml`），否则回退到旧的 `spec` free text（配件类产品仍用 `spec`，如 `20W`）。
- **`shopify_tag` 留空 = 不同步**。每个取值自己控制是否出现在网店。

## 3. 灵感来源：仅内部

`Inspiration` **不同步到 Shopify**，也不写进商品描述。用途是店内搜索和员工答客（「哪款是 Baccarat 那个味」）。
在欧盟公开标注他牌商标有法律风险，故刻意不外显。产品详情页也只对经理显示。

## 4. 回填命令

```
python manage.py backfill_perfume_attributes            # dry-run
python manage.py backfill_perfume_attributes --apply
```

从既有文本一次性解析（238 款实测）：

| 项目 | 结果 | 来源 |
|---|---|---|
| 容量 | 125 | `spec` / 名称 / 描述里的 `100ML` |
| 浓度 | 127 | 文本中的 EDP / EDT / Extrait / EDC |
| 香型 | 203 | 葡语描述关键词（floral、oud、amadeirado…） |
| 灵感来源 | 5 | **仅当出现已知品牌名时**（见下） |
| 名称清理 | 28 | 名字本身就是「EDP」的产品 → 用系列名替代 |

**为什么灵感来源只有 5 条**：描述是营销文案，「inspirado」多数是修辞用法（`inspirado no jogo de luz e sombra`）。
自由匹配抓出 17 条、其中 12 条是垃圾。改为**必须命中已知品牌白名单**（`KNOWN_HOUSES`，含 Dior/Givenchy/YSL/Xerjoff/Baccarat 等 40+ 家），
5 条全部正确，其余留人工录入。**宁可少而准**。

## 5. 待做

- 第 3 层：用户自定义属性（属性定义/属性值 + 管理界面）
- 产品列表按浓度 / 香型筛选
- 把 `Concentration.shopify_tag` / `FragranceFamily.shopify_tag` 接进 Shopify 标签（`gender` 已有先例：`gender_shopify_tag`）
- 分类表的增删改界面（目前需 Django admin）
