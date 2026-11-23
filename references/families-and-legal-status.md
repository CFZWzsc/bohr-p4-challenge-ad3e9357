# 同族归并与法律状态核对

本文件说明 `family_groups`、`legal_status`、`conflicts` 的判定口径。实现位于 `group_families`、`legal_status_rows`、`find_conflicts`。

## 1. 号码规范化

公开号按 `国家码 + 序号 + 种类码` 解析：

- 去除空格、连字符、斜杠、点、括号；
- 统一大写；
- `US2020/0123456 A1` → `US20200123456A1`；
- 无法拆分的保留原文，`publication_parsed=false`，写入 `uncertainty.unparsed_publication_numbers`；
- 不推断缺失的国家码、序号或种类码；
- DOI 不当作专利公开号。

申请号仅去分隔符并大写；优先权号同样处理，用于同族比对。

## 2. 同族归并规则

使用并查集，按以下顺序合并：

1. 相同 `family_id`（大小写不敏感）；
2. 相同规范化优先权号；
3. 记录声明的 `family_members` 能在语料中定位到公开号时，交叉合并。

输出：

- `family_key`：优先 `family_id`，否则 `PRIORITY:<号>`，否则 `SINGLE:<标识符>`；
- `size`、`members`、`jurisdictions`、`statuses`；
- `basis`：说明归并依据（声明 family_id / 共享优先权 / 单条记录 / 多 family_id 交叉合并）；
- `truncated`：成员超过 `caps.max_family_members` 时为 true。

**口径边界**：这是“声明字段归并”，不是 DOCDB/INPADOC 简单族或扩展族重建；族规模可能低估或误并，必须写入不确定性与 `misses`。

## 3. 法律状态分类

| 分类 | 触发词（节选） |
|---|---|
| `granted_or_active` | granted / active / in force / issued / valid / 授权 / 有效 |
| `pending` | pending / under examination / filed / published / 审中 / 实审 |
| `expired_or_invalid` | expired / lapsed / ceased / abandoned / revoked / rejected / 失效 / 撤回 / 驳回 |
| `unknown` | 无法归类或字段缺失 |

每条 `legal_status` 保留：

- `status_raw`、`status`、`status_date`、`status_source`；
- `stale_days` = `as_of - status_date`；
- `freshness`：`current` / `stale`（>180 天）/ `unknown_date`；
- `confidence`：high / medium / low；
- `history`：输入提供多条状态记录时保留。

未知状态不得假定有效；无日期不得推定。

## 4. 冲突检测

| 类型 | 条件 | 严重度 |
|---|---|---|
| `legal_status_conflict` | 同一公开号在不同来源出现不同状态分类 | high |
| `family_status_conflict` | 同一 family_key 在同一辖区出现多个状态分类 | medium |
| `status_date_after_as_of` | 状态日期晚于基准日 | medium |

冲突对象保留全部来源值（状态、日期、来源、记录 ID、定位）与 `suggested_action`；不擅自选择“正确”状态。

## 5. 官方登记簿复核

本 Skill 不联网。正式使用前必须：

1. 按公开号回 USPTO Patent Center / EPO Register / CNIPA 等官方库核对；
2. 记录查询日期与库名版本；
3. 区分公开号与授权号，区分优先权日与申请日；
4. 不把转载页面、聚合站或二手摘要称为“官方著录项”；
5. 状态变更需保留变更前后值与生效日。

## 6. 演示案例

`examples/demo-case` 中 `FAM-1001` 有 3 个成员：

- `US20200123456A1`（US，granted）
- `EP3873456A1`（EP，pending，受限）
- `CN112345678A`（CN，expired）

另有一条聚合站重复记录声明 `CN112345678A` 为 active，脚本将其合并为同一条候选并生成 `legal_status_conflict`。
