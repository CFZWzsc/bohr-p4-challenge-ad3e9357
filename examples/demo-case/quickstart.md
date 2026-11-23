# 演示案例快速开始（合成数据，非正式案例）

本目录全部记录均为**合成演示数据**，不对应任何真实专利或论文；输出仅用于说明接口与完整流程，不得冒充官方固定案例结果。

## 文件

| 文件 | 内容 |
|---|---|
| `case.json` | 案例配置：查询词、分类号、申请人、基准日、目标号码、`extra_queries`、`output_contract`（9 个键） |
| `patents.jsonl` | 5 条专利记录：`FAM-1001` 的 3 个成员（US/EP/CN，其中 EP 受限、CN 失效）+ 1 条同公开号冲突重复 + 1 条无关疫苗专利 |
| `papers.jsonl` | 2 条技术文献（DOI 记录） |
| `task.md` | 演示任务说明，含号码、主题、分类号与负向查询要求 |

## 运行

在 Skill 根目录执行：

```bash
python scripts/run_skill.py \
  --input examples/demo-case \
  --work /app/work \
  --out /app/output/result.json
```

或使用绝对路径（安装到 `/skills/weekly-skill` 时）：

```bash
python /skills/weekly-skill/scripts/run_skill.py \
  --input /input \
  --work /app/work \
  --out /app/output/result.json
```

## 预期特征（实测，示例）

- 退出码 `0`，`status=success`；
- 原始 7 条 → 去重 6 条（`CN112345678A` 两条来源合并）；
- `FAM-1001` 归并 3 个成员，辖区 `CN/EP/US`；
- 候选 5 条；`EP3873456A1` 为 `restricted`，`verification=metadata_only`，摘要/权利要求显示 `[受限：原文未展示]`；
- `CN112345678A` 产生 `legal_status_conflict`（expired vs active），保留双方来源；
- `q-zero`（biomarker panel）命中 0，并写入 `no_result_reasons[scope=query]`；
- `US20190000001A1` 分数 0，进入 `excluded`，原因 `below_min_score`；
- `output_contract_fields.present` 9 个键全部为 `true`；
- 同目录生成 `run-review.md`。

示意输出片段（示例，已截断）：

```json
{
  "case_id": "demo-patent-001",
  "status": "success",
  "candidates": [
    {"rank": 1, "identifier": "US20200123456A1", "score": 26,
     "legal_status": "granted_or_active", "access_level": "open",
     "verification": "text_checked", "evidence_locator": "patents.jsonl:1",
     "rationale": "标题命中 nlrp3(+3)；标题命中 inflammasome(+3)；…"}
  ],
  "family_groups": [
    {"family_key": "FAM-1001", "size": 3, "jurisdictions": ["CN", "EP", "US"]}
  ],
  "no_result_reasons": [
    {"scope": "query", "query_id": "q-zero", "reason": "在已提供的 6 条记录中没有记录满足该查询表达式…"}
  ]
}
```

## 复核

1. 打开 `result.json`，逐条对照 `candidates[].evidence_locator`，例如 `patents.jsonl:1`。
2. 核对 `score` 与 `rationale` 的权重合计。
3. 确认受限记录未展示摘要/权利要求原文。
4. 打开同目录 `run-review.md`，按第 11 节步骤人工核验并补充结论。
5. 重复运行两次，`result.json` 的 SHA-256 应一致（确定性、无网络）。
