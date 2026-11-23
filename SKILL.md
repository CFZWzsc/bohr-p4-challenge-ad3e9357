---
name: patent-technical-literature-search
description: "专利与技术文献检索：从实际输入语料构造可解释检索式，规范化公开号/申请号、归并同族、核对法律状态与时效、标注访问许可与展示边界，输出逐条带分数、理由和证据定位的候选集，并对零命中、冲突、缺元数据和权限问题给出显式说明。stdin/stdout 之外的产物写入 result.json 与 run-review.md。"
---

# 专利与技术文献检索

## 用途

围绕一个技术主题或一组专利号，在**已提供的**专利/技术文献语料中完成一次可复核检索：

1. 构造并记录检索式（关键词、CPC/IPC、申请人、日期、号码）；
2. 规范化公开号/申请号，去重并归并同族；
3. 对每条记录给出相关性分数、理由和原文/定位证据；
4. 核对法律状态（授权/审中/失效/未知）及其来源、日期、时效；
5. 标注数据源许可与可展示边界（绿/黄/红）；
6. 显式说明无结果、冲突、缺元数据与权限受限。

本 Skill 不联网、不查询官方登记簿、不绕过付费墙、不补造文献。

## 适用条件

- 输入目录（默认 `/input`）中存在 `case.json` 和/或 `task.md`，并至少包含一份专利或技术文献数据文件。
- 数据文件为 `json` / `jsonl` / `ndjson` / `xml` / `csv` / `tsv` / `txt`，可位于任意子目录。
- 记录中至少有标题、摘要、公开号、申请号、族字段、DOI 等任一可识别字段。
- 需要“候选集 + 排序/筛选依据 + 证据定位 + 缺口说明”，而不是法律意见或侵权结论。

## 不适用场景

- 专利撰写、审查意见答复、侵权判定、自由实施（FTO）结论、专利价值评估。
- 替代专利律师或出具法律意见。
- 需要实时官方登记簿状态、实时法律状态变更追踪或跨库实时检索。
- 需要下载/展示受限商业库原文，或需要绕过登录、付费墙、验证码。
- 输入只有图片/扫描件且未提供 OCR 文本时，不能凭领域标签补造内容。

## 输入形态

`--input` 可以是目录或单个文件，默认 `/input`。目录内推荐结构：

```
/input/
  case.json          # 案例配置：case_id、query/query_terms、分类号、申请人、as_of、target_publications、output_contract
  task.md            # 任务说明（可选，用于补充技术词与目标号码）
  patents.jsonl      # 专利记录（或 json/xml/csv）
  papers.jsonl       # 技术文献记录（或与专利混在同一文件）
  nested/data.json   # 允许任意子目录
```

支持嵌套包装：`resultList`、`items`、`message.items`、`records`、`results`、`patents`、`documents`、`papers`、`hits`、`data`、`docs`、`entries` 等会被递归展开；也可能直接是数组。

字段别名（大小写、下划线、连字符、中英文均可识别，节选）：

| 规范化字段 | 常见别名 |
|---|---|
| title | title / invention_title / patent_title / 标题 / 题名 |
| abstract | abstract / summary / 摘要 |
| claims | claims / claim_text / independent_claims / 权利要求 |
| publication_number | publication_number / pub_number / patent_number / 公开号 / 专利号 |
| application_number | application_number / app_number / 申请号 |
| priority_number / priority_date | priority_number / priority / 优先权号；priority_date / 优先权日 |
| assignee | assignee / applicant / owner / 申请人 / 专利权人 |
| inventors | inventors / inventor / 发明人 |
| cpc / ipc | cpc / cpc_codes / ipc / classifications / 分类号 |
| family_id / family_members | family_id / docdb_family_id / inpadoc_family_id / 同族 |
| legal_status | legal_status / status；可为字符串或 `{status,date,source}`，也可为状态历史数组 |
| source | source / database / provider / 来源 |
| license / access | license / access / availability / access_level / 许可 / 权限 |
| doi / journal / authors | 技术文献字段 |

记录类型判定：有 DOI/期刊/`doc_type=paper` → 技术文献；有权利要求/族字段/专利号 → 专利；否则按普通文档处理。

## 调用方式（唯一命令）

```bash
python /skills/weekly-skill/scripts/run_skill.py \
  --input /input \
  --work /app/work \
  --out /app/output/result.json
```

- `--input`：输入目录或案例文件，默认 `/input`。
- `--work`：工作目录，默认 `/app/work`；脚本会尝试创建。
- `--out`：结果 JSON 路径，默认 `/app/output/result.json`；同目录会写 `run-review.md`。
- 若 Skill 安装在别处，把 `/skills/weekly-skill` 换成实际安装目录；不要假定相对路径可用。
- 兼容 `--case`（等价 `--input`）与 `--output`（等价 `--out`）。
- `run_skill.py` 是永不崩溃的薄封装：任何异常都会写出结构化结果并以 0 退出。

## 检索式构造

按五类检索式分别记录，并合成主表达式：

| 类型 | 来源 | 示例 |
|---|---|---|
| keyword | `case.query`、`query_terms`/`keywords`，补充 `task.md` 中少量英文技术词 | `(nlrp3 OR inflammasome OR inhibitor OR gout)` |
| classification | `classification_filters`/`cpc_filters`/`ipc_filters`，以及文本中的 `A61K`、`C07D` 等 | `(A61K* OR C07D*)` |
| assignee | `assignee_filter`/`assignees`/`assignee`/`applicant` | `("Inflazome")` |
| number | `target_publications`/`publication_numbers`/`patent_numbers` + `task.md` 号码 | `(US20200123456A1 OR EP3873456A1)` |
| date | `date_from`/`date_to`/`date_range`/`as_of` | `PUBLICATION_DATE[2020-01-01 TO 2026-11-01]` |

规则：

- 关键词统一小写、NFKC 归一；英文按词边界匹配，中文按子串匹配。
- 结构词（family/id/doi/cpc/ipc/json 等）、分类号、申请人名不重复进入关键词检索式。
- 号码先去空格/连字符/斜杠/点并大写，再按 `国家码+序号+种类码` 解析；解析失败保留原文并说明。
- 每个检索式都写入 `queries`，含 `expression`、`hits`、`hit_identifiers`；`hits=0` 时必须有 `no_result_reason`。
- 调用方可提供 `extra_queries`（如负向对照），脚本会单独统计命中并说明零命中原因。

## 相关性判定与打分

权重定义在 `assets/policy.json`，脚本内置同值回退：

| 字段 | 权重 | 说明 |
|---|---|---|
| 标题 title | 3 | 每个查询词命中一次计 3；同一词不重复计分 |
| 摘要 abstract | 2 | 同一词已在标题命中则不再计摘要 |
| 权利要求 claims | 1 | 同一词按标题 > 摘要 > 权利要求取最高字段一次 |
| 分类号 CPC/IPC | 2 | 每个匹配的主组前缀计 2 |
| 申请人 assignee | 2 | 每个匹配的申请人限定计 2 |
| 号码 number | 6 | 目标公开号/申请号精确命中计 6 |
| 优先权 priority | 2 | 目标优先权号精确命中计 2 |

- `score` 为加权原始分；`score_pct` 按当前查询的理论满分归一化到 0–100（仅用于跨查询比较，不改变排序）。
- 排序：`score` 降序，再按标识符升序；`min_score` 默认 1，`top_k` 默认 50（见 `filters`）。
- `rationale` 必须写明命中的字段、词、权重与合计；未命中记录保留在 `excluded` 并给出 `below_min_score`。
- 受限/禁止展示记录只使用标题与元数据参与打分，不读取摘要/权利要求，`verification` 不得为 `text_checked`。

## 族关系与法律状态核对

**同族归并**（并查集）：

1. 相同 `family_id` 合并；
2. 相同规范化优先权号合并；
3. 记录声明的 `family_members` 若能在语料中定位到公开号，也合并；
4. 输出 `family_key`、`size`、`jurisdictions`、`statuses`、`members`、`basis`（依据说明）。
5. 仅做“声明字段归并”，不是 DOCDB/INPADOC 重建；族规模可能低估，写入 `uncertainty` 与 `misses`。

**法律状态分类**：`granted_or_active` / `pending` / `expired_or_invalid` / `unknown`。

- 保留原始状态文本、日期、来源；缺失日期标 `unknown_date`。
- 以 `as_of` 计算 `stale_days`；超过 180 天标 `stale`，置信度降为 `medium`。
- 未知状态不得假定有效；置信度 `low`。
- 同一公开号不同来源状态冲突 → `conflicts[].type=legal_status_conflict`（high）。
- 同族同辖区状态冲突 → `family_status_conflict`（medium）。
- 状态日期晚于基准日 → `status_date_after_as_of`（medium）。
- 所有状态均来自输入声明，**未查询官方登记簿**；正式使用前须回 USPTO Patent Center / EPO Register / CNIPA 等核对并记录查询日期。

## 权限与许可标注

按 `license`/`access`/`access_level` 归入：

| 级别 | 颜色 | 可展示范围 | 处置 |
|---|---|---|---|
| open | 绿 | 元数据 + 摘要/权利要求摘录 | 可展示，引用时标注库名、版本、查询日期 |
| restricted | 黄 | 仅元数据 | 摘要/权利要求原文不复制、不对外展示；需授权或人工登录核验 |
| prohibited | 红 | 仅标识符 | 不展示、不下载、不留存原文 |
| unknown | 黄 | 元数据 + 摘要摘录 | 按未核实处理，引用前确认权限 |

- 每条记录在 `access_notes` 中有 `access_level`、`color`、`allowed_display`、`retention`、`degrade`、`action`。
- 受限记录在候选中：`verification=metadata_only`、`verified=false`，摘要/权利要求显示为 `[受限：原文未展示]`，证据引文同样替换。
- 禁止展示记录：`verification=identifier_only`。

## 无结果、冲突、缺元数据的写法

必须使用显式字段，不得只写自然语言：

| 情形 | 字段 | 写法 |
|---|---|---|
| 整个输入无记录 | `status=insufficient_input`；`no_result_reasons[scope=global]` | “输入中没有解析到任何专利或技术文献记录；不生成候选，不补造文献。” |
| 有记录但无候选 | `status=partial`；`no_result_reasons[scope=global]` | 说明是阈值、主题或字段问题 |
| 单个检索式零命中 | `queries[].hits=0` + `queries[].no_result_reason` + `no_result_reasons[scope=query]` | 写明扫描记录数与可能原因 |
| 目标号码未找到 | `misses[]` | `type=publication_or_application_number`，不补造 |
| 同族成员缺失 | `misses[]` | `type=declared_family_member` |
| 状态冲突 | `conflicts[]` | 保留全部来源值与建议复核动作 |
| 缺优先权/日期/号码/许可 | `uncertainty[]` | 分类型说明，如 `missing_priority_date`、`unparsed_publication_numbers`、`no_license_signal` |
| 文件解析失败 | `errors[]` + `no_result_reasons[]` | 记录文件、阶段、原因；检索面标为不完整 |

禁止：补造文献、把未知状态写成有效、把聚合站数据称为“官方著录项”、把受限原文直接展示。

## 证据定位格式

每个候选必须有 `evidence_locator`，格式可直接定位回输入：

| 输入格式 | 定位格式 | 示例 |
|---|---|---|
| JSON | `相对路径#/JSON/Pointer` | `data.json#/resultList/items/3` |
| JSONL | `相对路径:行号` | `patents.jsonl:12` |
| CSV/TSV | `相对路径:row=行号` | `patents.csv:row=7` |
| TXT 键值块 | `相对路径#block=序号` | `notes.txt#block=2` |

- `evidence[]` 逐条给出 `field`、`term`、`weight`、`quote`、`locator`。
- `quote` 必须来自该定位处的原文；受限记录替换为 `[受限：原文未展示]` 并标 `withheld=true`。
- `all_locators` 保留同一条记录被多个来源命中时的全部定位，便于跨库复核。

## 输出契约

顶层必备字段（全部由脚本直接写出）：

`case_id`、`status`、`queries`、`query_expression`、`candidates`、`ranking`、`family_groups`、`legal_status`、`access_notes`、`filters`、`excluded`、`misses`、`no_result_reasons`、`conflicts`、`evidence`、`uncertainty`、`errors`、`stats`、`run_log`、`summary`、`input`、`normalization_rules`、`output_contract_fields`、`contract_notes`、`contract_map`、`contract_extras`。

`case.output_contract` 的每个键都会出现在顶层：

- 直接匹配优先；
- 别名映射：`results/hits/records/documents → candidates`；`families → family_groups`；`status → legal_status`（顶层 `status` 仍是运行状态）；`sources → evidence`；`reasons → excluded`；`notes → uncertainty`；`summary → 一段话字符串`；
- 未知键尽力宽松匹配，仍无法产出时写 `null` 并在 `contract_extras` 说明。

`status` 取值：`success` / `partial` / `insufficient_input` / `failed`（不使用 `completed`）。

## 异常处理

| 情形 | 处理 | 退出码 |
|---|---|---|
| 输入目录/文件不存在 | `status=insufficient_input`，写 `no_result_reasons`，不崩溃 | 0 |
| `case.json` 损坏 | 记录 `errors`，继续尝试解析其他数据文件 | 0 |
| JSON/JSONL/XML/CSV 单文件解析失败 | 记录 `errors`，其他文件继续；检索面标为不完整 | 0 |
| 无查询词 | `uncertainty.type=no_query_terms`，仍输出候选与定位 | 0 |
| 无基准日 | `uncertainty.type=no_as_of_date`，不做时效判断 | 0 |
| 公开号无法解析 | 保留原文，`publication_parsed=false`，写入不确定性 | 0 |
| 无许可字段 | `access_level=unknown`，按未核实处理 | 0 |
| 输出目录不可写 | stderr 简短错误；封装层尝试回退写 `/app/output/result.json` | 3（封装层仍以 0 退出） |
| 任何未预期异常 | `run_skill.py` 捕获并写出失败/部分结果 | 0 |

## Agent 执行步骤（必须完成）

1. **读取实际输入**：查看 `/input/case.json`、`/input/task.md` 与所有数据文件，不按目录名猜内容。
2. **运行命令**（见上）。确认 stdout 的 `status`、`records`、`candidates`，并打开 `/app/output/result.json`。
3. **逐条核验候选**：对每个 `candidates[].evidence_locator` 打开对应文件/行/JSON Pointer，核对标题、公开号/DOI、申请人、分类号与分数证据；发现不一致时在 `result.json` 中修正或降级，并在 `uncertainty`/`run_log` 记录，不得静默改分。
4. **核对同族与状态**：检查 `family_groups` 的 `basis` 是否与输入声明一致；检查 `legal_status[].status_source` 与日期；冲突必须保留双来源。
5. **核对权限**：对 `access_level=restricted/prohibited` 的记录确认未展示摘要/权利要求原文；必要时补充 `access_notes`。
6. **写 `/app/output/run-review.md`**：脚本会自动生成基础复核记录；Agent 必须在此基础上补充实际核验结果（逐条 locator 核对结论、修正项、未完成项、人工复核建议），确保与 `result.json` 一致。若未做额外修正，也须保留自动生成内容并确认自检清单全部通过。
7. **自检后交付**：按下方清单逐项确认；部分结果必须标明限制，不得以流程说明替代成品。

## 自检清单

- [ ] `case_id` 与输入一致；`status` 为四个契约值之一。
- [ ] `output_contract_fields.present` 所有键为 `true`；缺失键在 `contract_notes`/`contract_extras` 说明。
- [ ] 每条候选都有 `score`、`rationale`、`evidence_locator`，且 locator 可打开。
- [ ] `score` 与 `rationale` 中的权重、词、合计一致；排序与 `ranking` 一致。
- [ ] 同族组 `size` 与成员数一致；3 成员演示族归并为 1 组（`FAM-1001`）。
- [ ] 每条 `legal_status` 有状态、日期、来源、时效、置信度；未知不写成有效。
- [ ] `restricted/prohibited` 记录 `verification != text_checked`，摘要/权利要求未展示。
- [ ] 每个 `hits=0` 查询都有 `no_result_reason`；全局无结果有 `no_result_reasons[scope=global]`。
- [ ] `conflicts` 保留冲突双方来源与建议动作。
- [ ] 无网络访问、无补造记录、无付费墙绕过。
- [ ] `python -m py_compile scripts/sk_entry.py scripts/run_skill.py` 通过。
- [ ] `run-review.md` 已写入 `/app/output/`，且与 `result.json` 一致。

## 最小调用示例（示例，非正式案例结果）

演示数据在 `examples/demo-case/`（全部为合成数据）：

```bash
python scripts/run_skill.py \
  --input examples/demo-case \
  --work /app/work \
  --out /app/output/result.json
```

预期特征（示意）：`status=success`；原始 7 条、去重 6 条；`FAM-1001` 归并 3 个成员（US/EP/CN）；1 条 `restricted` 仅元数据；`q-zero` 零命中并给出显式原因；`CN112345678A` 出现来源状态冲突。完整字段见 `examples/demo-case/quickstart.md`。以上仅为接口示例，不代表任何官方固定案例结果。

## 限制

- 同族归并仅依据声明字段，可能漏并或误并；不是官方同族数据。
- 法律状态是输入时点声明，未向登记簿刷新；陈旧或错误状态会直接传递。
- 相关性是词项 + 分类号匹配，不理解权利要求范围、等同原则或侵权边界。
- 覆盖范围只反映输入语料，不代表全球专利布局或数据库完整覆盖。
- 受限/禁止记录的元数据也可能不完整；`verification=metadata_only` 不等于官方核验。
