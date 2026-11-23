# 证据定位、权限与能力边界

本文件说明如何复核每条候选、如何标注许可与展示边界，以及本 Skill 明确不做什么。

## 1. 证据定位

每个候选至少有一个 `evidence_locator`，可直接打开：

| 输入格式 | 格式 | 示例 |
|---|---|---|
| JSON | `相对路径#/JSON/Pointer` | `data.json#/resultList/items/3` |
| JSONL | `相对路径:行号` | `patents.jsonl:12` |
| CSV/TSV | `相对路径:row=行号` | `patents.csv:row=7` |
| TXT 键值块 | `相对路径#block=序号` | `notes.txt#block=2` |

`evidence[]` 逐条记录：

- `field`：title / abstract / claims / classification / assignee / number / priority；
- `term`：命中的查询词或分类号；
- `weight`：该项权重；
- `quote`：原文片段（受限记录替换为 `[受限：原文未展示]`，并标 `withheld=true`）；
- `locator`：该字段所在记录定位。

`all_locators` 保留同一条记录被多个来源命中时的全部定位。人工复核步骤：

1. 打开 locator；
2. 核对标题、号码/DOI、申请人、分类号；
3. 核对 quote 确实出现在该字段；
4. 核对 `score` 与 `rationale` 的权重合计；
5. 对受限记录不引用摘要/权利要求原文。

## 2. 权限分级

| 级别 | 颜色 | 可展示 | 留存 | 降级 |
|---|---|---|---|---|
| open | 绿 | 元数据 + 摘要/权利要求摘录 | 不限 | 无 |
| restricted | 黄 | 仅元数据 | 会话内 | 引文隐藏，`metadata_only` |
| prohibited | 红 | 仅标识符 | 不留存 | `identifier_only` |
| unknown | 黄 | 元数据 + 摘要摘录 | 待确认 | 标未核实 |

规则来源：`assets/policy.json` 的 `access_levels` 与 `access_aliases`；记录自身 `license` / `access` / `access_level` 优先。

受限记录在候选中：

- `verification=metadata_only`，`verified=false`；
- `abstract_excerpt` / `claims_excerpt` 为 `[受限：原文未展示]`；
- 不参与摘要/权利要求打分，只用标题与元数据。

## 3. 输出结构

- `candidates`：排序后的候选，含 score、score_pct、rationale、evidence_locator、access_note、verification；
- `ranking`：rank / candidate_id / identifier / score / score_pct；
- `family_groups`：族分组与依据；
- `legal_status`：逐条状态、日期、来源、时效、置信度；
- `access_notes`：逐条权限与处置；
- `filters`：实际使用的阈值、top_k、分类号、日期、去重规则；
- `excluded`：被排除记录及 `reason_code`；
- `misses`：目标号码、优先权号、声明同族成员未找到；
- `no_result_reasons`：全局与逐查询零命中原因；
- `conflicts`：状态/族/日期冲突；
- `evidence`：查询、候选、族、状态、权限证据；
- `uncertainty`：缺元数据、未知状态、受限、解析失败等；
- `run_log`：确定性步骤记录（无时间戳）；
- `output_contract_fields` / `contract_notes` / `contract_map` / `contract_extras`：契约覆盖情况。

## 4. 明确限制

- 不联网、不查询官方登记簿、不访问付费库、不绕过登录。
- 不补造缺失文献、号码、状态或日期。
- 同族归并只依据声明字段，不能替代官方同族数据。
- 相关性是词项与分类号匹配，不理解权利要求范围、等同原则或侵权边界。
- 阻断/风险结论仅为筛查信号，不构成法律意见。
- 覆盖范围只反映输入语料，不代表全球布局或数据库完整性。
- 受限记录的元数据也可能不完整；`metadata_only` 不等于官方核验。

## 5. 复跑与验收

```bash
python -m py_compile scripts/sk_entry.py scripts/run_skill.py
python scripts/run_skill.py --input examples/demo-case --work /app/work --out /app/output/result.json
```

验收检查：

- 退出码 0；
- `status=success`（演示）；
- `output_contract_fields.present` 全为 true；
- 每个候选有 score + rationale + evidence_locator；
- `FAM-1001` 归并 3 个成员；
- 受限记录 `verification=metadata_only`；
- `q-zero` 有显式 `no_result_reason`；
- 运行两次 `result.json` 哈希一致（确定性）。
