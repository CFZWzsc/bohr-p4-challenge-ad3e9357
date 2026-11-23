---
name: patent-technical-landscape-search
description: Use when a task needs patent or technical-literature searching with normalised publication numbers, jurisdiction and family grouping, point-in-time legal-status classification with staleness, classification-code and claim-text relevance evidence, blocking-risk screening, and explicit coverage gaps in a contract-preserving result.json.
---

# 专利与技术文献检索

## 用途与适用条件

当任务要求围绕一项技术做专利/技术文献检索，输出可比对的同族专利、法律状态、技术相关性与地域覆盖缺口时使用。适用于立项前的专利态势扫描、技术路线调研与自由实施（FTO）初筛。

**适用前提**：输入真实提供专利记录（公开号、标题、摘要/权利要求、法律状态、同族/优先权、分类号等）。状态缺失或陈旧时必须明确标注，不能默认有效。

**不适用**：出具法律意见、替代专利律师、预测授权前景、抓取未授权数据库。不联网、不查询任何专利登记簿。

## 输入

| 输入 | 说明 |
|---|---|
| `/input/case.json` | 必读。常见结构：`patents`/`records`、`query`/`query_terms`、`classification_filters`（CPC/IPC 前缀）、`as_of`（状态评估基准日）。记录字段：`publication_number`、`title`、`abstract`、`claims`、`assignee`、`jurisdiction`、`family_id`、`priority_number`/`priority_date`、`legal_status`（可为字符串或 `{"status","date","source"}`）、`cpc`/`ipc`、`license`。实际键名以文件为准。 |
| `task.md` | 可选，存在时读取用于核对目标技术、地域与时间范围。 |
| `--work` | 工作目录，默认 `/app/work`。 |

## 处理流程（对应评分维度）

1. **号码规范化**（任务完成度）：公开号按 `国家码 + 序号 + 种类码` 拆分，去除空格/斜杠/点并大写；无法拆分的保持原样并标注原因，不猜测缺失段。
2. **去重与同族**（正确性）：同一公开号跨库合并；同族按声明的 `family_id` 或共享优先权号归组，输出族规模与辖区集合；不做 DOCDB/INPADOC 重建。
3. **法律状态与时效**（正确性/可靠体验）：把状态文本归类为 `granted_or_active` / `pending` / `expired_or_invalid` / `unknown`，保留状态来源与日期；以 `as_of` 计算 `stale_days`，超过 180 天标 `stale` 并降低置信度；缺日期标 `unknown_date`。
4. **技术相关性**（可核验性）：查询词在标题（权重 3）/摘要（2）/权利要求（1）中匹配，CPC/IPC 前缀命中加权 2；每条命中保留字段与原句，得分 0 的记录仍列出但不计相关。
5. **阻断风险筛查**（完整性）：`granted_or_active` 且相关得分 ≥5 标记 `high`，待审且相关为 `medium`，失效为 `low`，状态未知为 `unknown`；输出 `blocking_patents`，并注明这是筛查信号而非法律结论。
6. **覆盖与缺口**（完整性）：统计辖区分布、状态分布、陈旧/未知状态数、缺优先权日数、无法解析的公开号、许可信号缺失数，逐项写入 `coverage_gaps`。

## 输出契约

写入 `--out`（默认 `/app/output/result.json`）。顶层字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 原样保留输入 `case_id` |
| `status` | string | `success` / `partial` / `insufficient_input` / `failed` |
| `result` | object | `query`、`results`（同时以 `ranked_results` 暴露）、`families`、`legal_status`、`landscape`、`blocking_patents`、`coverage_gaps`、`normalization_rules`、`limitations`、`change_log` |
| `results` | array | 顶层镜像，逐条含规范化公开号、相关性得分、匹配证据、阻断风险与来源定位 |
| `evidence` | array | 专利、同族、法律状态证据 |
| `uncertainty` | array | 无基准日、状态未知/陈旧、缺优先权、号码未解析、无许可信号 |
| `errors` | array | 运行错误；正常为空 |
| `stats` | object | 原始/去重记录数、同族数、辖区数、高风险数、未知/陈旧状态数 |
| `contract_notes` | object | 未产出契约字段说明 |
| `output_contract_fields` | object | `provided` 与 `present` |

输入 `output_contract` 的每个字段名都会出现在顶层；无法产出时写 `null` 并在 `contract_notes` 说明。

`status`：无记录 → `insufficient_input`；缺 `as_of`、全部状态未知或无查询词 → `partial`；记录、基准日、可识别状态与查询词齐备 → `success`；解析失败 → `failed`。

## 异常处理

| 情形 | 处理 | 输出标记 |
|---|---|---|
| 无专利记录 | 不生成态势结论 | `status=insufficient_input` |
| 状态文本无法归类 | 标 `unknown`，不假定有效 | `status_counts.unknown` |
| 无 `as_of` | 不做时效判断 | `uncertainty.type=no_as_of_date` |
| 状态日期超过 180 天 | 标 `stale`，置信度降为 medium | `legal_status[].freshness` |
| 公开号格式异常 | 保留原文并写原因 | `coverage_gaps.unparsed_publication_numbers` |
| 缺优先权日 | 不推断族龄 | `coverage_gaps.missing_priority` |
| 无许可字段 | 许可状态记为 unknown | `uncertainty.type=no_license_signal` |
| 无 CPC/IPC 过滤 | 分类号不加分，说明原因 | `uncertainty.type=no_classification_filter` |
| 输出目录不可写 | stderr 简短错误，退出码 3 | 无静默成功 |

## 限制

- 同族归并只依据声明字段，可能漏并或误并，不能替代官方同族数据。
- 法律状态为输入时点的声明，未向登记簿刷新，陈旧或错误状态会直接传递。
- 相关性是词项与分类号匹配，不理解权利要求范围与等同原则。
- 阻断风险仅为筛查信号，不构成侵权判断或法律意见。
- 辖区覆盖只反映输入集合，不代表全球专利布局。

## 最小调用示例（示例，非正式案例结果）

```bash
python /skills/weekly-skill/scripts/run_skill.py \
  --case /input/case.json --out /app/output/result.json --work /app/work
```

输入片段（示例）：

```json
{"case_id": "demo-008", "query": "NLRP3 inhibitor", "query_terms": ["nlrp3", "inhibitor"],
 "classification_filters": ["A61K"], "as_of": "2026-11-01",
 "patents": [{"publication_number": "US20210123456A1", "title": "NLRP3 inhibitor compounds",
              "abstract": "Compounds inhibiting NLRP3.", "jurisdiction": "US",
              "legal_status": {"status": "granted", "date": "2026-05-01", "source": "USPTO"},
              "family_id": "FAM-1", "family_members": 2, "cpc": ["A61K31/00"]}]}
```

示意输出（示例，已截断）：

```json
{"case_id": "demo-008", "status": "success",
 "results": [{"publication": "US20210123456A1", "relevance_score": 10,
              "legal_status": "granted_or_active", "blocking_risk": "high"}],
 "result": {"landscape": {"families": 1, "status_counts": {"granted_or_active": 1}}}}
```

以上为接口示意，不代表任何官方固定案例结果。


## 交付保证与评分对齐

1. 在 Skill 安装目录执行：`cd /skills/weekly-skill && python scripts/run_skill.py --case /input/case.json --out /app/output/result.json --work /app/work`；路径不同时显式改 `--case` / `--out` / `--work`，不要假定相对路径可用。
2. 必须产出**完整成品** `result.json`：每条专利/技术文献都进入 `results` 并带规范化公开号、相关性得分、法律状态与来源定位；禁止 skeleton、`pending`、空占位或只写方法说明。
3. 脚本无法自动完成的实质判断（真实权利要求范围、同族关系与阻断风险）由调用 Agent 依据实际记录补全并写回 `result.json`，不得编造状态或专利。
4. 自检：`case_id` 与输入一致；`output_contract` 每个字段存在；`status` 取本契约值 `success` / `partial` / `insufficient_input` / `failed`（不得写 `completed`）；陈旧/未知状态已标注。
5. 输入缺失、JSON 损坏或字段异常时仍写出结构化结果并说明缺口，不崩溃、不静默成功。

## 最终交付强制要求

1. **调用 Agent 必须交付成品，不是流程说明**：脚本产出只是索引、核对或规格骨架；调用 Agent 必须在同一 `result.json` 中补齐本题要求的最终产物（正文/译文/审稿意见/综述/分析结论/图表源文件与导出件/幻灯片页内容与讲稿/实验方案等）。交付字段为空、只有标题骨架，或含 `pending`、`TODO` 之类占位，一律视为未完成。
2. **必须覆盖全部输入条目**：输入中的每个章节、问题、文献、图、页面、指标都要有对应条目，不得只处理示例或首条；确实缺失的条目写入 `uncertainty` / `limitations` 并说明原因，不得静默跳过。
3. **必须自检后再交付**：`case_id` 与输入一致；`output_contract` 每个字段都存在；状态值取本题契约规定的枚举；数字、单位、引用、来源可逐条回溯；结果内部无自相矛盾。
4. **参数等价**：`python scripts/run_skill.py --case <输入> --out <输出>` 与 `--output <输出>` 等价；默认 `/input/case.json` → `/app/output/result.json`。
5. **平台用 3 个固定案例分别运行**：逻辑要由输入驱动，不针对某一份示例硬编码；字段命名差异走别名匹配与显式降级，并在 `uncertainty` 记录假设。
