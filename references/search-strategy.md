# 检索式构造与排序策略

本文件说明 `scripts/sk_entry.py` 如何从案例输入构造检索式、打分与筛选。实现位于脚本中的 `build_query_config`、`score_record`、`build_candidates`。

## 1. 关键词

来源优先级：

1. `case.json` 的 `query_terms` / `keywords` / `terms` / `key_terms`；
2. `query` / `search_query` / `question` / `topic` / `technology` 中的英文词；
3. `task.md` 中补充的少量英文技术词（不从中抽取中文，避免断词噪声）。

处理规则：

- NFKC 归一、转小写、压缩空白；
- 英文按词边界匹配（`nlrp3` 不会命中 `nlrp30`）；中文按子串匹配；
- 剔除停用词、纯数字、日期、专利号片段、分类号、申请人名、结构字段名；
- 去重保序，最多 `caps.max_terms`（默认 24）。

## 2. CPC/IPC

- 从 `classification_filters` / `cpc_filters` / `ipc_filters` 读取；
- 从 `task.md` 中提取 `A61K`、`C07D`、`A61K31/00` 等模式；
- 统一取主组前缀（前 4 位，如 `A61K`），记录命中的原始分类号；
- 每个匹配前缀计 `weights.classification`（默认 2）分。

## 3. 申请人

- 从 `assignee_filter` / `assignees` / `assignee` / `applicant` 读取；
- 大小写不敏感的子串匹配；每个命中计 `weights.assignee`（默认 2）分；
- 申请人名不进入关键词检索式，避免重复计分。

## 4. 号码

- 目标号码来自 `target_publications` / `publication_numbers` / `patent_numbers` / `target_numbers`，以及 `task.md` 中的号码模式；
- 先去除空格、连字符、斜杠、点、括号并大写；
- 公开号、申请号精确命中计 `weights.number`（默认 6）分；
- 优先权号命中计 `weights.priority`（默认 2）分。

## 5. 日期

- `date_from` / `date_to` / `date_range` / `as_of`；
- 日期筛选只使用公开日，缺失时回退优先权日；
- 缺失日期的记录在日期筛选下单独说明，不用其他日期填补。

## 6. 打分与排序

| 字段 | 权重 | 计分方式 |
|---|---|---|
| 标题 | 3 | 每个查询词最多计一次 |
| 摘要 | 2 | 同一词已在标题命中则跳过 |
| 权利要求 | 1 | 同一词按标题 > 摘要 > 权利要求取最高一次 |
| 分类号 | 2 | 每个匹配前缀一次 |
| 申请人 | 2 | 每个匹配申请人一次 |
| 号码 | 6 | 精确命中一次 |
| 优先权 | 2 | 精确命中一次 |

- `score` = 加权原始分；`score_pct` = `round(100 * score / 理论满分)`，仅用于横向比较；
- 排序：`score` 降序 → 标识符升序；
- `min_score` 默认 1；低于阈值的记录进入 `excluded`，原因 `below_min_score`；
- `top_k` 默认 50，`caps.max_candidates` 默认 200。

理论满分 = 3×关键词数 + 2×分类前缀数 + 2×申请人数 + 6×目标号码数 + 2×目标优先权数。

## 7. 零命中与负向查询

每个检索式单独统计 `hits`。`hits=0` 时必须写 `no_result_reason`，并同步到顶层 `no_result_reasons`。示例：

- 负向对照 `biomarker panel` 在演示语料中零命中；
- 目标号码未出现时进入 `misses`，不补造记录。

## 8. 调参建议

- 主题过宽：提高 `min_score`、增加分类号或申请人限定；
- 主题过窄：检查 `query_terms` 是否含同义词，或从摘要补充英文术语；
- 号码优先：把 `target_publications` 放入案例，号码命中权重最高；
- 结果量过大：降低 `top_k`，但保留 `excluded` 与 `misses` 以便复核。
