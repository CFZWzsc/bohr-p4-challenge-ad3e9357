# Runtime contract

Bohrium Playground Period 4 submission — challenge `challenge-ad3e9357` (track: `literature-search`).

This repository holds the complete skill package for the challenge: the skill
definition, its executable pipeline and all supporting documents. No precomputed
answers, private data or credentials are included.

## Skill

- Name: `patent-technical-literature-search`
- Summary: "专利与技术文献检索：从实际输入语料构造可解释检索式，规范化公开号/申请号、归并同族、核对法律状态与时效、标注访问许可与展示边界，输出逐条带分数、理由和证据定位的候选集，并对零命中、冲突、缺元数据和权限问题给出显式说明。stdin/stdout 之外的产物写入 result.json 与 run-review.md。"

## Contents

| File |
|------|
| `SKILL.md` |
| `assets/policy.json` |
| `examples/demo-case/case.json` |
| `examples/demo-case/papers.jsonl` |
| `examples/demo-case/patents.jsonl` |
| `examples/demo-case/quickstart.md` |
| `examples/demo-case/task.md` |
| `references/evidence-and-limits.md` |
| `references/families-and-legal-status.md` |
| `references/search-strategy.md` |
| `scripts/run_skill.py` |
| `scripts/sk_entry.py` |
| `README.md` |

## How to run

```bash
python scripts/sk_entry.py --input /input --work /app/work --out /app/output
```

A single deterministic pass reads `/input`, computes the deliverable and writes a
contract-complete `/app/output/result.json` plus the human-readable review files
described in `SKILL.md`. The same input always produces byte-identical output; no
network access is used, and fields the materials cannot support are reported
explicitly instead of being left empty.

## Notes

- Pure Python standard library; no network access required.
- Every claim is anchored to paragraph-level evidence; unknown items are reported
  in `uncertainty` instead of being fabricated.
