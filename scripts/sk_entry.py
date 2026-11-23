#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""专利与技术文献检索 Skill 的确定性执行入口（仅标准库，无网络）。

读取 --input 指定目录或文件中的案例配置与专利/技术文献语料，完成：
字段规范化、检索式构造、相关性打分、排序与筛选、同族归并、法律状态核对、
权限分级、证据定位、无结果/冲突/缺元数据/权限问题说明，并写出
result.json（覆盖案例 output_contract 的全部键）与 run-review.md。

设计约束：stdlib only、无网络、确定性、异常不外抛（main 捕获并写结构化结果）。
"""

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

DEFAULT_INPUT = "/input"
DEFAULT_WORK = "/app/work"
DEFAULT_OUT = "/app/output/result.json"

DATA_EXTS = {".json", ".jsonl", ".ndjson", ".xml", ".csv", ".tsv", ".txt", ".md"}
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_DEPTH = 10

# ---------------------------------------------------------------------------
# 默认策略（assets/policy.json 可覆盖）
# ---------------------------------------------------------------------------
DEFAULT_POLICY = {
    "version": "1.0.0",
    "weights": {
        "title": 3,
        "abstract": 2,
        "claims": 1,
        "classification": 2,
        "assignee": 2,
        "number": 6,
        "priority": 2,
    },
    "caps": {
        "max_records": 2000,
        "max_candidates": 200,
        "max_terms": 24,
        "excerpt_chars": 240,
        "max_quotes": 4,
        "max_excluded": 200,
        "max_family_members": 50,
    },
    "ranking": {"min_score": 1, "top_k": 50, "tie_break": "score_desc_then_identifier"},
    "stale_days": 180,
    "score_scale_max": 100,
    "access_levels": {
        "open": {
            "color": "green",
            "allowed_display": "metadata,abstract_excerpt,claims_excerpt",
            "retention": "unlimited",
            "degrade": "none",
            "note": "公开/开放许可来源；可展示元数据与摘录，引用时仍需标注来源与查询日期。",
        },
        "restricted": {
            "color": "yellow",
            "allowed_display": "metadata_only",
            "retention": "session_only",
            "degrade": "quote_withheld",
            "note": "订阅或登录受限来源；仅展示号码、标题等元数据，不复制摘要/权利要求原文。",
        },
        "prohibited": {
            "color": "red",
            "allowed_display": "identifier_only",
            "retention": "none",
            "degrade": "metadata_only",
            "note": "禁止展示或留存原文；仅保留可复核的标识符与来源线索。",
        },
        "unknown": {
            "color": "yellow",
            "allowed_display": "metadata,abstract_excerpt",
            "retention": "review_required",
            "degrade": "mark_unverified",
            "note": "许可字段缺失；按未核实处理，展示范围待人工确认。",
        },
    },
    "access_aliases": {
        "open": ["open", "public", "free", "cc-by", "cc by", "open-access", "open access", "public domain"],
        "restricted": ["restricted", "subscriber", "subscription", "paid", "login", "licensed", "licence", "license_required"],
        "prohibited": ["prohibited", "confidential", "embargo", "embargoed", "do not display", "internal only", "red"],
    },
    "stop_terms": [
        "the", "and", "for", "with", "from", "into", "using", "use", "uses", "used",
        "method", "methods", "study", "studies", "based", "novel", "new", "system",
        "systems", "approach", "approaches", "analysis", "result", "results", "paper",
        "papers", "patent", "patents", "search", "searching", "literature", "technical",
        "review", "article", "articles", "data", "report", "reports", "task", "case",
        "专利", "技术", "文献", "检索", "研究", "方法", "分析", "系统", "数据", "问题",
        "结果", "以及", "相关", "进行", "通过", "一种", "包括", "用于", "可以", "需要",
        "提供", "本文", "本节", "任务", "案例", "输出", "输入",
    ],
}

# ---------------------------------------------------------------------------
# 字段别名（归一化键：小写、仅保留字母数字与中文）
# ---------------------------------------------------------------------------
FIELD_ALIASES = {
    "title": ["title", "inventiontitle", "patenttitle", "displayname", "name", "标题", "题名", "名称"],
    "abstract": ["abstract", "summary", "descriptionexcerpt", "摘要", "提要"],
    "claims": ["claims", "claimtext", "claimstext", "independentclaims", "权利要求", "权利要求书"],
    "publication_number": [
        "publicationnumber", "pubnumber", "publication", "pubnum", "docnumber",
        "patentnumber", "publicationno", "公开号", "公告号", "专利号",
    ],
    "application_number": ["applicationnumber", "appnumber", "applicationno", "appnum", "申请号"],
    "priority_number": ["prioritynumber", "priority", "earliestpriority", "优先权号"],
    "priority_date": ["prioritydate", "earliestprioritydate", "优先权日"],
    "publication_date": ["publicationdate", "pubdate", "date", "公开日", "公告日"],
    "assignee": ["assignee", "applicant", "applicants", "owner", "assigneename", "申请人", "专利权人"],
    "inventors": ["inventors", "inventor", "发明人"],
    "classifications": ["cpc", "cpcodes", "ipc", "ipccodes", "classifications", "classification", "classcodes", "分类号"],
    "family_id": ["familyid", "docdbfamilyid", "inpadocfamilyid", "simplefamilyid", "family", "专利族", "同族"],
    "family_members": ["familymembers", "members", "familymemberpublications", "同族成员"],
    "legal_status": ["legalstatus", "status", "状态", "法律状态"],
    "source": ["source", "database", "provider", "来源"],
    "license": ["license", "licence", "access", "availability", "licensing", "accesslevel", "rights", "许可", "权限"],
    "jurisdiction": ["jurisdiction", "country", "office", "国家", "局"],
    "language": ["language", "lang", "语言"],
    "doc_type": ["doctype", "type", "kind", "documenttype", "文献类型"],
    "doi": ["doi"],
    "journal": ["journal", "venue", "publicationvenue", "期刊", "来源期刊"],
    "authors": ["authors", "author", "作者"],
    "keywords": ["keywords", "keyword", "indexterms", "关键词"],
}
ALIAS_INDEX = {}
for _canon, _names in FIELD_ALIASES.items():
    for _name in _names:
        ALIAS_INDEX[_name] = _canon

STATUS_GRANTED = ("granted", "active", "in force", "in-force", "issued", "valid", "grant", "accepted", "registered", "有效", "授权")
STATUS_PENDING = ("pending", "application", "filed", "published", "under examination", "examination",
                  "provisional", "审中", "实审", "公开")
STATUS_DEAD = ("expired", "lapsed", "ceased", "abandoned", "withdrawn", "revoked", "invalidated",
               "not in force", "rejected", "失效", "撤回", "驳回", "终止", "无效")
STATUS_ORDER = ("granted_or_active", "pending", "expired_or_invalid", "unknown")

RECORD_MARKERS = (
    "publicationnumber", "patentnumber", "abstract", "claims", "familyid",
    "doi", "assignee", "applicationnumber", "prioritynumber",
)
CONTAINER_KEYS = (
    "resultlist", "items", "messageitems", "records", "results", "patents", "documents",
    "papers", "hits", "data", "docs", "list", "entries", "payload", "response",
)

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
EN_RE = re.compile(r"[a-z0-9][a-z0-9\-]{1,}")
CLASS_RE = re.compile(r"\b([A-H]\d{2}[A-Z](?:\d{1,4}(?:/\d{1,6})?)?)\b")
PATENT_NO_RE = re.compile(r"\b((?:US|EP|CN|WO|JP|KR|DE|FR|GB|CA|AU|IN|RU|BR|TW|HK|SG)[\s\-]?\d[\d\s\-/]{5,}[A-Z]?\d?)\b")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def norm_space(value):
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def norm_key(value):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(value).lower())


def as_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return norm_space(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "; ".join(part for part in (as_text(item) for item in value) if part)
    if isinstance(value, dict):
        for key in ("text", "value", "name", "title", "code", "status"):
            if key in value and value[key] not in (None, "", [], {}):
                return as_text(value[key])
        try:
            return norm_space(json.dumps(value, ensure_ascii=False, sort_keys=True))[:600]
        except (TypeError, ValueError):
            return ""
    return norm_space(str(value))


def pick(record, canonical):
    if not isinstance(record, dict):
        return None
    for key, value in record.items():
        if ALIAS_INDEX.get(norm_key(key)) == canonical and value not in (None, "", [], {}):
            return value
    return None


def load_json(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return json.load(handle)


def load_policy():
    policy = json.loads(json.dumps(DEFAULT_POLICY))
    path = Path(__file__).resolve().parent.parent / "assets" / "policy.json"
    try:
        if path.is_file():
            overlay = load_json(path)
            if isinstance(overlay, dict):
                for key, value in overlay.items():
                    if isinstance(value, dict) and isinstance(policy.get(key), dict):
                        merged = dict(policy[key])
                        merged.update(value)
                        policy[key] = merged
                    else:
                        policy[key] = value
    except (OSError, ValueError):
        pass
    return policy


def parse_date(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        value = str(int(value))
    text = norm_space(str(value))
    if not text:
        return None
    match = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    match = re.match(r"^(\d{4})(\d{2})(\d{2})$", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    match = re.match(r"^(\d{4})$", text)
    if match:
        try:
            return date(int(match.group(1)), 1, 1)
        except ValueError:
            return None
    return None


def json_pointer(path_parts):
    escaped = [str(part).replace("~", "~0").replace("/", "~1") for part in path_parts]
    return "#/" + "/".join(escaped) if escaped else "#"


# ---------------------------------------------------------------------------
# 号码与分类号规范化
# ---------------------------------------------------------------------------
def clean_number(value):
    text = norm_space(str(value or "")).upper()
    return re.sub(r"[\s\-/.,()]", "", text)


def parse_publication_number(value, doc_type="patent"):
    raw = norm_space(str(value or "")).upper()
    if not raw:
        return {"raw": "", "normalized": "", "country": "", "serial": "", "kind": "",
                "parsed": False, "reason": "publication number missing"}
    if raw.startswith("10.") or "doi" in raw.lower():
        return {"raw": raw, "normalized": "", "country": "", "serial": "", "kind": "",
                "parsed": False, "reason": "value looks like a DOI, not a patent publication number"}
    cleaned = clean_number(raw)
    match = re.match(r"^([A-Z]{2})(\d+)([A-Z]\d?)?$", cleaned)
    if not match:
        match = re.match(r"^([A-Z]{2})([A-Z0-9]+?)([A-Z]\d?)?$", cleaned)
    if not match:
        return {"raw": raw, "normalized": cleaned, "country": "", "serial": "", "kind": "",
                "parsed": False, "reason": "number does not match COUNTRY+SERIAL+KIND pattern"}
    country, serial, kind = match.group(1), match.group(2), match.group(3) or ""
    if not serial[:1].isdigit() and country != "WO":
        return {"raw": raw, "normalized": country + serial + kind, "country": country,
                "serial": serial, "kind": kind, "parsed": False,
                "reason": "serial portion does not start with a digit"}
    normalized = country + serial + kind
    if doc_type == "paper":
        return {"raw": raw, "normalized": "", "country": "", "serial": "", "kind": "",
                "parsed": False, "reason": "technical literature uses DOI, not a patent publication number"}
    return {"raw": raw, "normalized": normalized, "country": country, "serial": serial,
            "kind": kind, "parsed": True, "reason": ""}


def parse_classifications(record):
    values = pick(record, "classifications")
    if values is None:
        return []
    if isinstance(values, (str, dict)):
        values = [values]
    codes = []
    if isinstance(values, list):
        for item in values:
            if isinstance(item, str):
                codes.extend(part for part in re.split(r"[;,\s|]+", item) if part.strip())
            elif isinstance(item, dict):
                code = item.get("code") or item.get("symbol") or item.get("value") or item.get("cpc") or item.get("ipc")
                if code:
                    codes.append(as_text(code))
    result = []
    for code in codes:
        token = norm_space(code).upper()
        if not token:
            continue
        match = CLASS_RE.search(token)
        if match:
            result.append(match.group(1))
        elif re.match(r"^[A-H]\d{2}[A-Z]$", token):
            result.append(token)
    seen = []
    for code in result:
        if code not in seen:
            seen.append(code)
    return seen


def class_prefix(code):
    match = re.match(r"^([A-H]\d{2}[A-Z])", str(code or "").upper())
    return match.group(1) if match else ""


# ---------------------------------------------------------------------------
# 输入扫描与多格式解析
# ---------------------------------------------------------------------------
def iter_files(root):
    root = Path(root)
    if root.is_file():
        return [root]
    files = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        if any(part in ("__pycache__", ".git", ".svn") for part in path.parts):
            continue
        if path.suffix.lower() not in DATA_EXTS:
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        files.append(path)
    return files


def looks_like_record(node):
    if not isinstance(node, dict):
        return False
    keys = {norm_key(key) for key in node.keys()}
    return any(marker in keys for marker in RECORD_MARKERS)


def walk_json(node, parts, base_locator, out, errors, depth=0):
    if depth > MAX_DEPTH or len(out) > 5000:
        return
    if isinstance(node, dict):
        if looks_like_record(node):
            out.append((base_locator + json_pointer(parts) if parts else base_locator, node))
            return
        for key, value in node.items():
            walk_json(value, parts + [key], base_locator, out, errors, depth + 1)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            walk_json(value, parts + [index], base_locator, out, errors, depth + 1)


def parse_json_text(text, locator, out, errors):
    stripped = text.lstrip("\ufeff \t\r\n")
    if not stripped:
        return
    try:
        node = json.loads(stripped)
    except ValueError as exc:
        errors.append({"file": locator, "stage": "json_parse", "detail": str(exc)[:200]})
        return
    walk_json(node, [], locator, out, errors)


def parse_jsonl_text(text, locator, out, errors):
    for index, line in enumerate(text.splitlines(), start=1):
        line = line.strip().lstrip("\ufeff")
        if not line:
            continue
        try:
            node = json.loads(line)
        except ValueError:
            errors.append({"file": "%s:%d" % (locator, index), "stage": "jsonl_parse",
                           "detail": "line is not valid JSON"})
            continue
        walk_json(node, [], "%s:%d" % (locator, index), out, errors)


def local_tag(tag):
    return str(tag).split("}")[-1]


def element_to_obj(element):
    children = list(element)
    attributes = {local_tag(key): value for key, value in element.attrib.items()}
    if not children:
        text = norm_space(element.text or "")
        if attributes:
            attributes.setdefault("value", text)
            return attributes
        return text
    obj = dict(attributes)
    for child in children:
        key = local_tag(child.tag)
        value = element_to_obj(child)
        if key in obj:
            if isinstance(obj[key], list):
                obj[key].append(value)
            else:
                obj[key] = [obj[key], value]
        else:
            obj[key] = value
    return obj


def parse_xml_text(text, locator, out, errors):
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        errors.append({"file": locator, "stage": "xml_parse", "detail": str(exc)[:200]})
        return
    walk_json(element_to_obj(root), [], locator, out, errors)


def parse_csv_text(text, locator, delimiter, out, errors):
    try:
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        if not reader.fieldnames:
            return
        for index, row in enumerate(reader, start=2):
            clean = {key: value for key, value in row.items() if key is not None}
            if looks_like_record(clean):
                out.append(("%s:row=%d" % (locator, index), clean))
    except csv.Error as exc:
        errors.append({"file": locator, "stage": "csv_parse", "detail": str(exc)[:200]})


def parse_kv_text(text, locator, out, errors):
    blocks = re.split(r"\n\s*\n", text)
    for index, block in enumerate(blocks, start=1):
        pairs = {}
        for line in block.splitlines():
            match = re.match(r"^\s*([A-Za-z\u4e00-\u9fff][\w \u4e00-\u9fff\-/]{0,40})\s*[:：]\s*(.+?)\s*$", line)
            if match:
                pairs[match.group(1).strip()] = match.group(2).strip()
        if len(pairs) >= 2 and looks_like_record(pairs):
            out.append(("%s#block=%d" % (locator, index), pairs))


def read_text(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ""


def collect_records(files, errors, root):
    records = []
    task_texts = []
    file_log = []
    for path in files:
        try:
            rel = path.relative_to(root).as_posix() if Path(root).is_dir() else path.name
        except ValueError:
            rel = path.name
        suffix = path.suffix.lower()
        text = read_text(path)
        before = len(records)
        if suffix == ".json":
            parse_json_text(text, rel, records, errors)
        elif suffix in (".jsonl", ".ndjson"):
            parse_jsonl_text(text, rel, records, errors)
        elif suffix == ".xml":
            parse_xml_text(text, rel, records, errors)
        elif suffix == ".csv":
            parse_csv_text(text, rel, ",", records, errors)
        elif suffix == ".tsv":
            parse_csv_text(text, rel, "\t", records, errors)
        elif suffix == ".txt":
            if text.lstrip().startswith(("{", "[")):
                parse_jsonl_text(text, rel, records, errors)
            parse_kv_text(text, rel, records, errors)
            task_texts.append(text)
        elif suffix == ".md":
            if path.name.lower() in ("task.md", "task.txt", "prompt.md", "brief.md",
                                     "statement.md", "instructions.md", "requirements.md"):
                task_texts.append(text)
        file_log.append({"file": rel, "format": suffix.lstrip("."), "records": len(records) - before})
    return records, task_texts, file_log


def find_case_file(input_path):
    root = Path(input_path)
    if root.is_file():
        return root if root.suffix.lower() == ".json" else None
    preferred = root / "case.json"
    if preferred.is_file():
        return preferred
    for path in sorted(root.glob("*.json"), key=lambda item: item.name):
        try:
            node = load_json(path)
        except (OSError, ValueError):
            continue
        if isinstance(node, dict) and any(key in node for key in ("case_id", "output_contract", "query_terms", "query")):
            return path
    return None


# ---------------------------------------------------------------------------
# 记录规范化
# ---------------------------------------------------------------------------
def parse_status(value):
    history = []
    if isinstance(value, list):
        for item in value:
            history.extend(parse_status(item))
    elif isinstance(value, dict):
        status_text = as_text(pick(value, "legal_status") or value.get("status") or value.get("state")
                              or value.get("value") or value.get("code"))
        status_date = as_text(value.get("date") or value.get("as_of") or value.get("effective_date")
                              or value.get("updated") or value.get("last_updated"))
        status_source = as_text(value.get("source") or value.get("authority") or value.get("register"))
        if status_text or status_date or status_source:
            history.append({"status": status_text, "date": status_date, "source": status_source})
    elif value not in (None, ""):
        history.append({"status": norm_space(str(value)), "date": "", "source": ""})
    history.sort(key=lambda item: (item.get("date") or "", item.get("status") or ""))
    if not history:
        return "", "", "", []
    latest = history[-1]
    return latest["status"], latest["date"], latest["source"], history


def status_class(text):
    lowered = norm_space(text).lower()
    if not lowered:
        return "unknown"
    if any(word in lowered for word in STATUS_DEAD):
        return "expired_or_invalid"
    if any(word in lowered for word in STATUS_GRANTED):
        return "granted_or_active"
    if any(word in lowered for word in STATUS_PENDING):
        return "pending"
    return "unknown"


def normalize_record(raw, locator, source_file, policy):
    record = raw if isinstance(raw, dict) else {}
    doc_type_raw = as_text(pick(record, "doc_type")).lower()
    doi = as_text(pick(record, "doi"))
    journal = as_text(pick(record, "journal"))
    claims = as_text(pick(record, "claims"))
    family = as_text(pick(record, "family_id"))
    pub_raw = as_text(pick(record, "publication_number"))
    if any(token in doc_type_raw for token in ("paper", "article", "journal", "review", "preprint", "conference", "论文", "文献")):
        doc_type = "paper"
    elif any(token in doc_type_raw for token in ("patent", "专利")):
        doc_type = "patent"
    elif doi or journal:
        doc_type = "paper"
    elif claims or family or re.match(r"^[A-Z]{2}\d", clean_number(pub_raw)):
        doc_type = "patent"
    else:
        doc_type = "document"

    pub = parse_publication_number(pub_raw, doc_type)
    status_text, status_date, status_source, status_history = parse_status(pick(record, "legal_status"))
    jurisdiction = as_text(pick(record, "jurisdiction")).upper()
    if not jurisdiction and pub["country"]:
        jurisdiction = pub["country"]

    members = pick(record, "family_members")
    member_numbers = []
    if isinstance(members, str):
        members = [members]
    if isinstance(members, list):
        for item in members:
            if isinstance(item, dict):
                number = as_text(pick(item, "publication_number") or item.get("number"))
            else:
                number = as_text(item)
            if number:
                member_numbers.append(number)

    record_id = as_text(pick(record, "publication_number") or record.get("record_id")
                         or record.get("id") or record.get("doc_id") or doi)
    if not record_id:
        record_id = "record-" + hashlib.sha1(locator.encode("utf-8")).hexdigest()[:10]

    return {
        "record_id": record_id,
        "doc_type": doc_type,
        "locator": locator,
        "source_file": source_file,
        "title": as_text(pick(record, "title")),
        "abstract": as_text(pick(record, "abstract")),
        "claims": claims,
        "publication_raw": pub_raw,
        "publication": pub["normalized"] if pub["parsed"] else "",
        "publication_parsed": pub["parsed"],
        "publication_reason": pub["reason"],
        "application_number": clean_number(as_text(pick(record, "application_number"))),
        "priority_number": clean_number(as_text(pick(record, "priority_number"))),
        "priority_date": as_text(pick(record, "priority_date")),
        "publication_date": as_text(pick(record, "publication_date")),
        "assignee": as_text(pick(record, "assignee")),
        "inventors": as_text(pick(record, "inventors")),
        "classifications": parse_classifications(record),
        "family_id": family,
        "family_members": member_numbers,
        "status_text": status_text,
        "status_date": status_date,
        "status_source": status_source,
        "status_history": status_history,
        "status_class": status_class(status_text),
        "source": as_text(pick(record, "source")),
        "license": as_text(pick(record, "license")),
        "doi": doi,
        "journal": journal,
        "authors": as_text(pick(record, "authors")),
        "keywords": as_text(pick(record, "keywords")),
        "jurisdiction": jurisdiction,
        "language": as_text(pick(record, "language")),
        "raw": record,
    }


def identifier_of(record):
    if record["publication"]:
        return record["publication"]
    if record["doi"]:
        return "DOI:" + record["doi"].upper()
    return record["record_id"]


def dedup_key(record):
    if record["publication"]:
        return "pub:" + record["publication"]
    if record["application_number"]:
        return "app:" + record["application_number"]
    if record["doi"]:
        return "doi:" + record["doi"].lower()
    return "loc:" + record["locator"]


def deduplicate(records):
    merged = {}
    order = []
    duplicates = []
    for record in records:
        key = dedup_key(record)
        if key not in merged:
            merged[key] = dict(record, locators=[record["locator"]],
                               sources=[record["source"]] if record["source"] else [],
                               status_variants=[{
                                   "status": record["status_text"],
                                   "status_class": record["status_class"],
                                   "date": record["status_date"],
                                   "source": record["status_source"],
                                   "record_id": record["record_id"],
                                   "locator": record["locator"],
                               }])
            order.append(key)
            continue
        target = merged[key]
        duplicates.append({
            "record_id": record["record_id"],
            "identifier": identifier_of(target),
            "kept_locator": target["locator"],
            "duplicate_locator": record["locator"],
            "reason": "同一标识符在多个来源重复出现，已合并为一条候选",
        })
        target["locators"].append(record["locator"])
        if record["source"] and record["source"] not in target["sources"]:
            target["sources"].append(record["source"])
        target["status_variants"].append({
            "status": record["status_text"],
            "status_class": record["status_class"],
            "date": record["status_date"],
            "source": record["status_source"],
            "record_id": record["record_id"],
            "locator": record["locator"],
        })
        for field in ("title", "abstract", "claims", "assignee", "priority_date", "publication_date",
                      "family_id", "priority_number", "doi", "journal", "authors", "keywords", "license"):
            if not target[field] and record[field]:
                target[field] = record[field]
        if not target["classifications"] and record["classifications"]:
            target["classifications"] = record["classifications"]
        if not target["status_text"] and record["status_text"]:
            target["status_text"] = record["status_text"]
            target["status_date"] = record["status_date"]
            target["status_source"] = record["status_source"]
            target["status_class"] = record["status_class"]
    return [merged[key] for key in order], duplicates


# ---------------------------------------------------------------------------
# 检索式构造
# ---------------------------------------------------------------------------
def tokenize_terms(text, stop_terms, limit, allow_cjk=True):
    text = norm_space(text)
    lowered = text.lower()
    terms = []
    seen = set()
    for match in EN_RE.finditer(lowered):
        token = match.group(0)
        if len(token) < 3 or token in stop_terms or token.isdigit():
            continue
        if token[0].isdigit() or re.match(r"^[a-z]{2}\d", token) or len(token) > 30:
            continue
        if CLASS_RE.fullmatch(token.upper()):
            continue
        if token not in seen:
            seen.add(token)
            terms.append(token)
        if len(terms) >= limit:
            return terms
    if not allow_cjk:
        return terms
    for match in CJK_RE.finditer(text):
        run = match.group(0)
        if len(run) < 2:
            continue
        chunks = [run] if len(run) <= 8 else [run[i:i + 4] for i in range(0, len(run), 4)]
        for chunk in chunks:
            if chunk in stop_terms or chunk in seen:
                continue
            seen.add(chunk)
            terms.append(chunk)
            if len(terms) >= limit:
                return terms
    return terms


def extract_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [norm_space(value)] if norm_space(value) else []
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(extract_list(item))
        return result
    if isinstance(value, dict):
        for key in ("text", "value", "name", "id", "term"):
            if key in value:
                return extract_list(value[key])
    return []


def first_present(case, names):
    for name in names:
        if name in case and case[name] not in (None, "", [], {}):
            return case[name]
    return None


def build_query_config(case, task_texts, policy):
    task_text = "\n".join(task_texts)
    stop = set(policy.get("stop_terms", []))
    stop |= {"family", "id", "doi", "cpc", "ipc", "json", "jsonl", "csv", "xml",
             "true", "false", "none", "null", "output", "contract"}
    limit = int(policy.get("caps", {}).get("max_terms", 24))

    raw_terms = []
    for key in ("query_terms", "keywords", "terms", "key_terms"):
        raw_terms.extend(extract_list(case.get(key)))
    query_text = as_text(first_present(case, ("query", "search_query", "question", "topic", "technology", "research_question")))
    raw_terms.extend(tokenize_terms(query_text, stop, limit))

    # task.md 只补充不在主查询中的英文技术词，避免中文断词噪声与通用词污染
    task_terms = tokenize_terms(task_text, stop, limit, allow_cjk=False)
    for term in task_terms:
        if len(raw_terms) >= limit:
            break
        if term not in raw_terms:
            raw_terms.append(term)

    terms = []
    seen = set()
    for term in raw_terms:
        cleaned = norm_space(term).lower()
        if len(cleaned) < 2 or cleaned in stop or cleaned in seen:
            continue
        seen.add(cleaned)
        terms.append(cleaned)
        if len(terms) >= limit:
            break

    prefixes = []
    for value in extract_list(case.get("classification_filters")) + extract_list(case.get("cpc_filters")) \
            + extract_list(case.get("ipc_filters")):
        prefix = class_prefix(value) or norm_space(value).upper()
        if prefix and prefix not in prefixes:
            prefixes.append(prefix)
    for match in CLASS_RE.finditer(task_text.upper()):
        prefix = class_prefix(match.group(1))
        if prefix and prefix not in prefixes:
            prefixes.append(prefix)
    prefixes = prefixes[:12]

    assignees = []
    for value in extract_list(case.get("assignee_filter")) + extract_list(case.get("assignees")) \
            + extract_list(case.get("assignee")) + extract_list(case.get("applicant")):
        cleaned = norm_space(value)
        if cleaned and cleaned.lower() not in [item.lower() for item in assignees]:
            assignees.append(cleaned)
    assignees = assignees[:8]

    target_numbers = []
    for value in extract_list(case.get("target_publications")) + extract_list(case.get("publication_numbers")) \
            + extract_list(case.get("patent_numbers")) + extract_list(case.get("target_numbers")):
        cleaned = clean_number(value)
        if cleaned and cleaned not in target_numbers:
            target_numbers.append(cleaned)
    for match in PATENT_NO_RE.finditer(task_text.upper()):
        cleaned = clean_number(match.group(1))
        if len(cleaned) >= 8 and cleaned not in target_numbers:
            target_numbers.append(cleaned)
    target_numbers = target_numbers[:20]

    target_priorities = [clean_number(value) for value in
                         extract_list(case.get("target_priorities")) + extract_list(case.get("target_priority"))
                         if clean_number(value)]
    target_priorities = target_priorities[:10]

    # 结构词、分类号与申请人已在独立检索式中处理，不再作为关键词重复计分
    assignee_lower = {item.lower() for item in assignees}
    filtered_terms = []
    for term in terms:
        if term in assignee_lower or CLASS_RE.fullmatch(term.upper()):
            continue
        if re.fullmatch(r"\d[\d\-/.]*", term):
            continue
        filtered_terms.append(term)
    terms = filtered_terms

    date_from = parse_date(first_present(case, ("date_from", "from_date")))
    date_to = parse_date(first_present(case, ("date_to", "to_date")))
    date_range = case.get("date_range")
    if isinstance(date_range, dict):
        date_from = date_from or parse_date(date_range.get("from") or date_range.get("start"))
        date_to = date_to or parse_date(date_range.get("to") or date_range.get("end"))
    elif isinstance(date_range, list) and len(date_range) == 2:
        date_from = date_from or parse_date(date_range[0])
        date_to = date_to or parse_date(date_range[1])

    queries = []
    expression_parts = []
    if terms:
        expression_parts.append("(" + " OR ".join(terms) + ")")
        queries.append({
            "id": "q-keyword", "type": "keyword", "terms": terms,
            "expression": "(" + " OR ".join(terms) + ")",
            "rationale": "来自案例 query/query_terms 与 task.md 的技术词；按标题>摘要>权利要求计分。",
        })
    if prefixes:
        expression_parts.append("(" + " OR ".join(prefix + "*" for prefix in prefixes) + ")")
        queries.append({
            "id": "q-classification", "type": "classification", "classification_prefixes": prefixes,
            "expression": "(" + " OR ".join(prefix + "*" for prefix in prefixes) + ")",
            "rationale": "IPC/CPC 主组前缀；命中分类号加固定权重。",
        })
    if assignees:
        expression_parts.append("(" + " OR ".join('"%s"' % item for item in assignees) + ")")
        queries.append({
            "id": "q-assignee", "type": "assignee", "assignees": assignees,
            "expression": "(" + " OR ".join('"%s"' % item for item in assignees) + ")",
            "rationale": "申请人/专利权人字段；用于主题检索的地域与主体限定。",
        })
    if target_numbers:
        expression_parts.append("(" + " OR ".join(target_numbers) + ")")
        queries.append({
            "id": "q-number", "type": "number", "numbers": target_numbers,
            "expression": "(" + " OR ".join(target_numbers) + ")",
            "rationale": "公开号/申请号/优先权号定位查询；先规范化再精确比对。",
        })
    if target_priorities:
        queries.append({
            "id": "q-priority", "type": "priority", "priorities": target_priorities,
            "expression": "(" + " OR ".join(target_priorities) + ")",
            "rationale": "优先权号定位查询，用于同族归并核对。",
        })
    if date_from or date_to:
        expression = "PUBLICATION_DATE[%s TO %s]" % (
            date_from.isoformat() if date_from else "*",
            date_to.isoformat() if date_to else "*")
        queries.append({
            "id": "q-date", "type": "date",
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "expression": expression,
            "rationale": "公开日/优先权日范围限定；缺失日期的记录在日期筛选下单独说明。",
        })

    extra = case.get("extra_queries")
    if isinstance(extra, list):
        for index, item in enumerate(extra, start=1):
            if not isinstance(item, dict):
                continue
            q_terms = [norm_space(term).lower() for term in extract_list(item.get("terms")) if norm_space(term)]
            q_type = as_text(item.get("type")) or "keyword"
            q_id = as_text(item.get("id")) or ("q-extra-%d" % index)
            queries.append({
                "id": q_id, "type": q_type, "terms": q_terms,
                "expression": as_text(item.get("expression")) or
                              ("(" + " OR ".join(q_terms) + ")" if q_terms else ""),
                "rationale": as_text(item.get("note")) or "调用方补充的对照/校验查询。",
            })

    combined_expression = " AND ".join(expression_parts) if expression_parts else (query_text or "")
    return {
        "terms": terms,
        "classification_prefixes": prefixes,
        "assignees": assignees,
        "target_numbers": target_numbers,
        "target_priorities": target_priorities,
        "date_from": date_from,
        "date_to": date_to,
        "queries": queries,
        "combined_expression": combined_expression,
        "query_text": query_text,
        "source_query": as_text(case.get("query")) or query_text,
    }


# ---------------------------------------------------------------------------
# 相关性打分
# ---------------------------------------------------------------------------
def text_tokens(text):
    return set(EN_RE.findall(norm_space(text).lower()))


def term_matches(term, text, tokens):
    term = term.lower()
    if not term:
        return False
    if all(ord(char) < 128 for char in term):
        if " " in term:
            return term in text
        return term in tokens or re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) is not None
    return term in text


def quote_for(term, text, limit):
    lowered = text.lower()
    position = lowered.find(term.lower())
    if position < 0:
        return norm_space(text)[:limit]
    start = 0
    for marker in (". ", "; ", "。", "；"):
        found = text.rfind(marker, 0, position)
        if found >= 0:
            start = max(start, found + len(marker))
    end = len(text)
    for marker in (". ", "; ", "。", "；"):
        found = text.find(marker, position)
        if found >= 0:
            end = min(end, found + len(marker))
    return norm_space(text[start:end])[:limit]


def accessible_fields(record):
    access = record.get("access_level", "unknown")
    fields = ["title"]
    if access in ("open", "unknown"):
        fields.extend(["abstract", "claims"])
    return fields


def score_record(record, config, policy):
    weights = policy.get("weights", {})
    limit = int(policy.get("caps", {}).get("excerpt_chars", 240))
    fields = accessible_fields(record)
    texts = {
        "title": norm_space(record["title"]).lower(),
        "abstract": norm_space(record["abstract"]).lower(),
        "claims": norm_space(record["claims"]).lower(),
    }
    tokens = {name: text_tokens(texts[name]) for name in texts}
    contributions = []
    evidence = []
    raw = 0

    for term in config["terms"]:
        for field in ("title", "abstract", "claims"):
            if field not in fields:
                continue
            if term_matches(term, texts[field], tokens[field]):
                weight = int(weights.get(field, 1))
                raw += weight
                contributions.append({"field": field, "term": term, "weight": weight})
                quote = quote_for(term, norm_space(record[field]), limit)
                evidence.append({
                    "field": field, "term": term, "weight": weight,
                    "quote": quote, "locator": record["locator"],
                })
                break

    matched_classes = []
    for prefix in config["classification_prefixes"]:
        for code in record["classifications"]:
            if code.upper().startswith(prefix.upper()):
                matched_classes.append(code)
                break
    if matched_classes:
        weight = int(weights.get("classification", 2))
        raw += weight * len(matched_classes)
        contributions.append({"field": "classification", "codes": sorted(set(matched_classes)), "weight": weight})
        evidence.append({
            "field": "classification", "term": None, "weight": weight,
            "quote": ", ".join(sorted(set(matched_classes))), "locator": record["locator"],
        })

    assignee_hits = []
    for name in config["assignees"]:
        if name.lower() in record["assignee"].lower():
            assignee_hits.append(name)
    if assignee_hits:
        weight = int(weights.get("assignee", 2))
        raw += weight * len(assignee_hits)
        contributions.append({"field": "assignee", "terms": assignee_hits, "weight": weight})
        evidence.append({
            "field": "assignee", "term": assignee_hits[0], "weight": weight,
            "quote": record["assignee"], "locator": record["locator"],
        })

    numbers = {record["publication"], record["application_number"]}
    number_hit = bool(numbers & set(config["target_numbers"]))
    if number_hit:
        weight = int(weights.get("number", 6))
        raw += weight
        contributions.append({"field": "number", "weight": weight})
        evidence.append({
            "field": "number", "term": record["publication"] or record["application_number"],
            "weight": weight, "quote": record["publication"] or record["application_number"],
            "locator": record["locator"],
        })
    if record["priority_number"] and record["priority_number"] in config["target_priorities"]:
        weight = int(weights.get("priority", 2))
        raw += weight
        contributions.append({"field": "priority", "weight": weight})
        evidence.append({
            "field": "priority", "term": record["priority_number"], "weight": weight,
            "quote": record["priority_number"], "locator": record["locator"],
        })

    max_possible = (int(weights.get("title", 3)) * len(config["terms"])
                    + int(weights.get("classification", 2)) * len(config["classification_prefixes"])
                    + int(weights.get("assignee", 2)) * len(config["assignees"])
                    + int(weights.get("number", 6)) * len(config["target_numbers"])
                    + int(weights.get("priority", 2)) * len(config["target_priorities"]))
    scale = int(policy.get("score_scale_max", 100))
    score_pct = int(round(scale * raw / max_possible)) if max_possible > 0 else 0
    score_pct = max(0, min(scale, score_pct))

    if contributions:
        parts = []
        for item in contributions[:8]:
            if item["field"] in ("title", "abstract", "claims"):
                parts.append("%s命中 %s(+%d)" % (
                    {"title": "标题", "abstract": "摘要", "claims": "权利要求"}[item["field"]],
                    item["term"], item["weight"]))
            elif item["field"] == "classification":
                parts.append("分类号 %s(+%d)" % (",".join(item["codes"]), item["weight"] * len(item["codes"])))
            elif item["field"] == "assignee":
                parts.append("申请人命中 %s(+%d)" % (",".join(item["terms"]), item["weight"] * len(item["terms"])))
            elif item["field"] == "number":
                parts.append("号码精确命中(+%d)" % item["weight"])
            elif item["field"] == "priority":
                parts.append("优先权号命中(+%d)" % item["weight"])
        rationale = "；".join(parts) + "；加权合计 %d，归一化 %d/%d。" % (raw, score_pct, scale)
    else:
        rationale = "未命中检索词、分类号或主体限定；保留为低相关记录，排序靠后或进入排除清单。"

    return raw, score_pct, contributions, evidence, rationale


# ---------------------------------------------------------------------------
# 权限分级
# ---------------------------------------------------------------------------
def classify_access(record, policy):
    access_levels = policy.get("access_levels", {})
    aliases = policy.get("access_aliases", {})
    blob = (record["license"] + " " + as_text(record["raw"].get("access_level")) + " "
            + as_text(record["raw"].get("access"))).lower()
    for level in ("prohibited", "restricted", "open"):
        for token in aliases.get(level, []):
            if token in blob:
                return level
    if record["license"] == "":
        return "unknown"
    for level in access_levels:
        if level in blob:
            return level
    return "unknown"


def access_note(record, level, policy):
    config = policy.get("access_levels", {}).get(level, policy.get("access_levels", {}).get("unknown", {}))
    note = {
        "identifier": identifier_of(record),
        "source": record["source"] or None,
        "license": record["license"] or None,
        "access_level": level,
        "color": config.get("color", "yellow"),
        "allowed_display": config.get("allowed_display", "metadata_only"),
        "retention": config.get("retention", "review_required"),
        "degrade": config.get("degrade", "mark_unverified"),
        "note": config.get("note", ""),
    }
    if level == "restricted":
        note["action"] = "仅展示元数据；摘要/权利要求原文不复制、不对外展示；如需全文须取得授权或人工登录核验。"
    elif level == "prohibited":
        note["action"] = "只保留标识符与来源线索；不展示、不下载、不留存原文。"
    elif level == "unknown":
        note["action"] = "许可未知；引用前须向数据源确认展示与留存权限。"
    else:
        note["action"] = "可展示元数据与摘录；引用时标注库名、版本与查询日期。"
    return note


# ---------------------------------------------------------------------------
# 同族归并
# ---------------------------------------------------------------------------
def group_families(records, policy):
    count = len(records)
    parent = list(range(count))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(a, b):
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[max(root_a, root_b)] = min(root_a, root_b)

    by_family, by_priority = {}, {}
    pub_index = {}
    for index, record in enumerate(records):
        if record["family_id"]:
            by_family.setdefault(record["family_id"].lower(), []).append(index)
        if record["priority_number"]:
            by_priority.setdefault(record["priority_number"], []).append(index)
        if record["publication"]:
            pub_index[record["publication"]] = index
    for group in list(by_family.values()) + list(by_priority.values()):
        for other in group[1:]:
            union(group[0], other)
    for index, record in enumerate(records):
        for member in record["family_members"]:
            target = pub_index.get(clean_number(member))
            if target is not None:
                union(index, target)

    groups = {}
    for index in range(count):
        groups.setdefault(find(index), []).append(index)
    families = []
    limit = int(policy.get("caps", {}).get("max_family_members", 50))
    for members in sorted(groups.values(), key=lambda group: min(group)):
        subset = [records[index] for index in members[:limit]]
        family_ids = sorted({records[index]["family_id"] for index in members if records[index]["family_id"]})
        priorities = sorted({records[index]["priority_number"] for index in members if records[index]["priority_number"]})
        if family_ids:
            family_key = family_ids[0]
            basis = "声明的 family_id" if len(members) > 1 else "单条记录（声明 family_id）"
        elif priorities:
            family_key = "PRIORITY:" + priorities[0]
            basis = "共享优先权号"
        else:
            family_key = "SINGLE:" + identifier_of(records[members[0]])
            basis = "单条记录（无族字段）"
        if len(family_ids) > 1:
            basis += "；检测到多个 family_id，已按优先权/成员交叉合并"
        families.append({
            "family_key": family_key,
            "family_ids": family_ids,
            "priority_numbers": priorities,
            "size": len(members),
            "members": [{
                "record_id": record["record_id"],
                "identifier": identifier_of(record),
                "publication_number": record["publication"] or None,
                "doc_type": record["doc_type"],
                "jurisdiction": record["jurisdiction"] or None,
                "legal_status": record["status_class"],
                "access_level": record["access_level"],
                "locator": record["locator"],
            } for record in subset],
            "jurisdictions": sorted({records[index]["jurisdiction"] for index in members if records[index]["jurisdiction"]}),
            "statuses": sorted({records[index]["status_class"] for index in members}),
            "basis": basis,
            "truncated": len(members) > limit,
        })
    return families


def family_of(record, families):
    for family in families:
        for member in family["members"]:
            if member["locator"] == record["locator"]:
                return family["family_key"]
    return None


# ---------------------------------------------------------------------------
# 法律状态与冲突
# ---------------------------------------------------------------------------
def legal_status_rows(records, as_of, stale_days):
    rows = []
    for record in records:
        status_date = parse_date(record["status_date"])
        days = (as_of - status_date).days if (as_of and status_date) else None
        freshness = "unknown_date" if not status_date else ("stale" if days is not None and days > stale_days else "current")
        if record["status_class"] == "unknown":
            confidence = "low"
        elif days is not None and days > stale_days:
            confidence = "medium"
        else:
            confidence = "high"
        rows.append({
            "record_id": record["record_id"],
            "identifier": identifier_of(record),
            "publication_number": record["publication"] or None,
            "jurisdiction": record["jurisdiction"] or None,
            "status_raw": record["status_text"] or None,
            "status": record["status_class"],
            "status_date": record["status_date"] or None,
            "status_source": record["status_source"] or None,
            "stale_days": days,
            "freshness": freshness,
            "confidence": confidence,
            "history": record["status_history"] if len(record["status_history"]) > 1 else [],
            "note": "状态取自输入记录的时间点声明；未查询官方登记簿。",
        })
    return rows


def find_conflicts(records, families, as_of):
    conflicts = []
    for record in records:
        variants = record.get("status_variants", [])
        classes = {item["status_class"] for item in variants if item["status_class"]}
        if len(classes) > 1:
            conflicts.append({
                "type": "legal_status_conflict",
                "identifier": identifier_of(record),
                "jurisdiction": record["jurisdiction"] or None,
                "severity": "high",
                "values": variants,
                "note": "同一公开号在不同来源给出不同法律状态；已保留主记录并列出全部来源，需回官方登记簿复核。",
                "suggested_action": "以官方登记簿（如 USPTO Patent Center、EPO Register、CNIPA）的当前状态为准，并记录查询日期。",
            })
    family_jurisdiction = {}
    for family in families:
        for member in family["members"]:
            if not member["jurisdiction"]:
                continue
            key = (family["family_key"], member["jurisdiction"])
            family_jurisdiction.setdefault(key, set()).add(member["legal_status"])
    for (family_key, jurisdiction), classes in sorted(family_jurisdiction.items()):
        if len(classes) > 1:
            conflicts.append({
                "type": "family_status_conflict",
                "identifier": family_key,
                "jurisdiction": jurisdiction,
                "severity": "medium",
                "values": sorted(classes),
                "note": "同一专利族在同一辖区出现多个状态归类；可能来自不同记录口径或状态更新。",
                "suggested_action": "核对同族成员的授权号与状态生效日，避免把公开号与授权号混用。",
            })
    if as_of:
        for record in records:
            status_date = parse_date(record["status_date"])
            if status_date and status_date > as_of:
                conflicts.append({
                    "type": "status_date_after_as_of",
                    "identifier": identifier_of(record),
                    "jurisdiction": record["jurisdiction"] or None,
                    "severity": "medium",
                    "values": [record["status_date"]],
                    "note": "状态日期晚于基准日，状态可能不适用于基准日时点。",
                    "suggested_action": "确认基准日口径，必要时改用早于基准日的状态记录。",
                })
    return conflicts


# ---------------------------------------------------------------------------
# 查询命中统计
# ---------------------------------------------------------------------------
def query_hit(record, query):
    q_type = query.get("type", "keyword")
    if q_type == "keyword":
        fields = accessible_fields(record)
        for term in query.get("terms", []):
            for field in fields:
                text = norm_space(record[field]).lower()
                if term_matches(term, text, text_tokens(text)):
                    return True
        return False
    if q_type == "classification":
        return any(code.upper().startswith(prefix.upper())
                   for prefix in query.get("classification_prefixes", [])
                   for code in record["classifications"])
    if q_type == "assignee":
        return any(name.lower() in record["assignee"].lower() for name in query.get("assignees", []))
    if q_type == "number":
        numbers = {record["publication"], record["application_number"]}
        return bool(numbers & set(query.get("numbers", [])))
    if q_type == "priority":
        return bool(record["priority_number"] and record["priority_number"] in query.get("priorities", []))
    if q_type == "date":
        candidate = parse_date(record["publication_date"]) or parse_date(record["priority_date"])
        if not candidate:
            return False
        start = parse_date(query.get("date_from"))
        end = parse_date(query.get("date_to"))
        return (not start or candidate >= start) and (not end or candidate <= end)
    return False


def annotate_queries(queries, records):
    output = []
    for query in queries:
        matched = [record for record in records if query_hit(record, query)]
        item = dict(query)
        item["hits"] = len(matched)
        item["hit_identifiers"] = [identifier_of(record) for record in matched[:20]]
        if not matched:
            item["no_result_reason"] = (
                "在已提供的 %d 条记录中没有记录满足该查询表达式；"
                "可能是语料未覆盖该主题/号码，或字段缺失导致无法匹配。" % len(records))
        output.append(item)
    return output


# ---------------------------------------------------------------------------
# 筛选、候选与输出组装
# ---------------------------------------------------------------------------
def build_candidates(records, config, policy, as_of):
    candidates = []
    excluded = []
    min_score = int(policy.get("ranking", {}).get("min_score", 1))
    top_k = int(policy.get("ranking", {}).get("top_k", 50))
    max_candidates = int(policy.get("caps", {}).get("max_candidates", 200))
    excerpt = int(policy.get("caps", {}).get("excerpt_chars", 240))
    max_quotes = int(policy.get("caps", {}).get("max_quotes", 4))
    scale = int(policy.get("score_scale_max", 100))

    for record in records:
        raw, pct, contributions, evidence, rationale = score_record(record, config, policy)
        access = record["access_level"]
        if access in ("restricted", "prohibited"):
            for item in evidence:
                item["quote"] = "[受限：原文未展示]"
                item["withheld"] = True
        record["_score"] = raw
        record["_score_pct"] = pct
        record["_contributions"] = contributions
        record["_evidence"] = evidence[:max_quotes]
        record["_rationale"] = rationale
        if raw < min_score:
            excluded.append({
                "record_id": record["record_id"],
                "identifier": identifier_of(record),
                "title": record["title"][:120] or None,
                "score": raw,
                "reason_code": "below_min_score",
                "reason": "相关性加权分 %d 低于阈值 %d；可能为不同技术主题或仅有分类号弱相关。" % (raw, min_score),
            })

    ranked = [record for record in records if record["_score"] >= min_score]
    ranked.sort(key=lambda record: (-record["_score"], identifier_of(record)))
    ranked = ranked[:min(top_k, max_candidates)]

    for rank, record in enumerate(ranked, start=1):
        access = record["access_level"]
        if access == "restricted":
            verification, verified = "metadata_only", False
        elif access == "prohibited":
            verification, verified = "identifier_only", False
        elif not record["title"] or (record["doc_type"] == "patent" and not record["publication"]):
            verification, verified = "incomplete_metadata", False
        else:
            verification, verified = "text_checked", True
        abstract = "[受限：原文未展示]" if access in ("restricted", "prohibited") else record["abstract"][:excerpt * 2]
        claims = "[受限：原文未展示]" if access in ("restricted", "prohibited") else record["claims"][:excerpt * 2]
        candidates.append({
            "rank": rank,
            "candidate_id": "C%03d" % rank,
            "record_id": record["record_id"],
            "identifier": identifier_of(record),
            "doc_type": record["doc_type"],
            "publication_number": record["publication"] or None,
            "publication_raw": record["publication_raw"] or None,
            "publication_parsed": record["publication_parsed"],
            "application_number": record["application_number"] or None,
            "doi": record["doi"] or None,
            "journal": record["journal"] or None,
            "title": record["title"] or None,
            "abstract_excerpt": abstract or None,
            "claims_excerpt": claims or None,
            "assignee": record["assignee"] or None,
            "inventors": record["inventors"] or None,
            "priority_number": record["priority_number"] or None,
            "priority_date": record["priority_date"] or None,
            "publication_date": record["publication_date"] or None,
            "jurisdiction": record["jurisdiction"] or None,
            "classifications": record["classifications"],
            "family_id": record["family_id"] or None,
            "legal_status": record["status_class"],
            "legal_status_raw": record["status_text"] or None,
            "legal_status_date": record["status_date"] or None,
            "access_level": access,
            "access_note": access_note(record, access, policy),
            "source": record["source"] or None,
            "verification": verification,
            "verified": verified,
            "score": record["_score"],
            "score_pct": record["_score_pct"],
            "score_max": scale,
            "rationale": record["_rationale"],
            "evidence_locator": record["locator"],
            "all_locators": record["locators"],
            "sources": record["sources"],
            "evidence": record["_evidence"],
        })
    return candidates, excluded, ranked


def build_filters(case, policy, config, total, kept, excluded_count):
    return {
        "min_score": int(policy.get("ranking", {}).get("min_score", 1)),
        "top_k": int(policy.get("ranking", {}).get("top_k", 50)),
        "tie_break": policy.get("ranking", {}).get("tie_break", "score_desc_then_identifier"),
        "classification_prefixes": config["classification_prefixes"],
        "assignees": config["assignees"],
        "date_from": config["date_from"].isoformat() if config["date_from"] else None,
        "date_to": config["date_to"].isoformat() if config["date_to"] else None,
        "doc_type_scope": "patent + technical literature (paper/document)",
        "dedup_rule": "公开号 → 申请号 → DOI → locator",
        "total_records": total,
        "candidates_kept": kept,
        "excluded": excluded_count,
    }


def build_misses(records, config):
    misses = []
    pub_set = {record["publication"] for record in records if record["publication"]}
    app_set = {record["application_number"] for record in records if record["application_number"]}
    pri_set = {record["priority_number"] for record in records if record["priority_number"]}
    for number in config["target_numbers"]:
        if number not in pub_set and number not in app_set:
            misses.append({
                "target": number, "type": "publication_or_application_number",
                "reason": "目标号码未出现在已提供的语料中；未补造记录，需人工在授权数据库补检。",
            })
    for number in config["target_priorities"]:
        if number not in pri_set:
            misses.append({
                "target": number, "type": "priority_number",
                "reason": "目标优先权号未出现；同族归并可能不完整。",
            })
    available = {record["publication"] for record in records if record["publication"]}
    for record in records:
        for member in record["family_members"]:
            cleaned = clean_number(member)
            if cleaned and cleaned not in available:
                misses.append({
                    "target": cleaned, "type": "declared_family_member",
                    "reason": "记录声明了该同族成员，但语料中无对应记录；族规模可能被低估。",
                })
    return misses


def build_no_result_reasons(queries, candidates, records, parse_errors):
    reasons = []
    for query in queries:
        if query.get("hits", 0) == 0 and query.get("no_result_reason"):
            reasons.append({
                "scope": "query", "query_id": query["id"], "expression": query.get("expression", ""),
                "reason": query["no_result_reason"],
            })
    if not records:
        reasons.append({"scope": "global", "query_id": None,
                        "reason": "输入中没有解析到任何专利或技术文献记录；不生成候选，不补造文献。"})
    elif not candidates:
        reasons.append({"scope": "global", "query_id": None,
                        "reason": "有记录但无记录达到相关性阈值，或查询词/过滤条件未覆盖语料；见 excluded 与 queries。"})
    if parse_errors:
        reasons.append({"scope": "global", "query_id": None,
                        "reason": "部分输入文件解析失败，检索面可能不完整；见 errors 与 run_log。"})
    return reasons


def build_uncertainty(records, candidates, parse_errors, config, as_of):
    items = []
    if not records:
        items.append({"type": "no_records", "detail": "输入中没有可解析的专利/技术文献记录。"})
    if not config["terms"]:
        items.append({"type": "no_query_terms", "detail": "未提供查询词，相关性排序不具区分力。"})
    if not config["classification_prefixes"]:
        items.append({"type": "no_classification_filter", "detail": "未提供 CPC/IPC 过滤，分类号不计分。"})
    if not as_of:
        items.append({"type": "no_as_of_date", "detail": "未提供基准日，法律状态时效无法判断。"})
    unknown = sum(1 for record in records if record["status_class"] == "unknown")
    if unknown:
        items.append({"type": "unknown_legal_status", "detail": "%d 条记录无可识别法律状态，按 unknown 处理，不假定有效。" % unknown})
    no_priority = sum(1 for record in records if not record["priority_date"])
    if no_priority:
        items.append({"type": "missing_priority_date", "detail": "%d 条记录缺优先权日，族龄与同族判断可能不完整。" % no_priority})
    unparsed = [record["publication_raw"] for record in records
                if record["doc_type"] == "patent" and not record["publication_parsed"]]
    if unparsed:
        items.append({"type": "unparsed_publication_numbers",
                      "detail": "%d 个公开号无法拆分为国家码+序号+种类码，已保留原文：%s" % (len(unparsed), ", ".join(unparsed[:10]))})
    restricted = [record for record in records if record["access_level"] in ("restricted", "prohibited")]
    if restricted:
        items.append({"type": "access_restricted_records",
                      "detail": "%d 条记录标记为受限/禁止展示，仅保留元数据，未作全文核验。" % len(restricted)})
    no_license = sum(1 for record in records if not record["license"])
    if no_license:
        items.append({"type": "no_license_signal", "detail": "%d 条记录无许可/权限字段，展示范围待确认。" % no_license})
    if parse_errors:
        items.append({"type": "parse_errors", "detail": "%d 个输入文件或行解析失败，检索面可能不完整。" % len(parse_errors)})
    items.append({"type": "local_evidence_only",
                  "detail": "同族、法律状态与许可均来自输入记录的时间点声明；本次未联网、未查询官方登记簿，也未绕过任何付费墙。"})
    if not candidates:
        items.append({"type": "no_candidates", "detail": "没有候选进入结果集；已完成与未完成部分见 no_result_reasons 与 excluded。"})
    return items


def build_summary(case_id, status, stats, conflicts, restricted, zero_queries):
    return ("案例 %s 运行状态 %s：解析 %d 条记录，去重后 %d 条，形成 %d 个同族组，"
            "产出 %d 条候选（其中受限 %d 条），排除 %d 条，冲突 %d 条，零命中查询 %d 个。"
            "所有结论仅基于输入语料，未联网核对官方登记簿。" % (
                case_id or "(未提供)", status, stats["records_raw"], stats["records"],
                stats["families"], stats["candidates"], restricted, stats["excluded"],
                conflicts, zero_queries))


def build_run_log(input_path, file_log, records, deduped, duplicates, queries, candidates,
                  excluded, families, conflicts, errors, as_of):
    return [
        {"step": 1, "action": "resolve_input", "input": str(input_path),
         "exists": Path(input_path).exists()},
        {"step": 2, "action": "scan_files", "files": file_log,
         "file_count": len(file_log)},
        {"step": 3, "action": "parse_records", "records_raw": len(records),
         "by_type": {
             "patent": sum(1 for record in records if record["doc_type"] == "patent"),
             "paper": sum(1 for record in records if record["doc_type"] == "paper"),
             "document": sum(1 for record in records if record["doc_type"] == "document"),
         }},
        {"step": 4, "action": "deduplicate", "records": len(deduped),
         "duplicates_merged": len(duplicates)},
        {"step": 5, "action": "build_queries", "queries": len(queries),
         "base_expression": queries[0].get("expression") if queries else ""},
        {"step": 6, "action": "score_and_rank", "scored": len(records),
         "candidates": len(candidates), "excluded": len(excluded)},
        {"step": 7, "action": "group_families", "families": len(families),
         "merged_families": sum(1 for family in families if family["size"] > 1)},
        {"step": 8, "action": "check_legal_status", "as_of": as_of.isoformat() if as_of else None,
         "conflicts": len(conflicts)},
        {"step": 9, "action": "classify_access",
         "restricted": sum(1 for candidate in candidates if candidate["access_level"] == "restricted"),
         "prohibited": sum(1 for candidate in candidates if candidate["access_level"] == "prohibited")},
        {"step": 10, "action": "report_errors", "parse_errors": len(errors)},
        {"step": 11, "action": "write_output", "note": "result.json 与 run-review.md 由确定性流程生成"},
    ]


# ---------------------------------------------------------------------------
# output_contract 映射
# ---------------------------------------------------------------------------
CONTRACT_ALIASES = {
    "results": "candidates", "hits": "candidates", "records": "candidates",
    "documents": "candidates", "candidates": "candidates", "literature": "candidates",
    "families": "family_groups", "family_groups": "family_groups", "patent_families": "family_groups",
    "status": "legal_status", "legal_status": "legal_status", "legalstatus": "legal_status",
    "sources": "evidence", "evidence": "evidence", "source_locators": "evidence",
    "reasons": "excluded", "excluded": "excluded", "exclusions": "excluded",
    "no_result_reasons": "no_result_reasons", "no_results": "no_result_reasons",
    "notes": "uncertainty", "uncertainty": "uncertainty", "limitations": "uncertainty",
    "queries": "queries", "query": "queries", "ranking": "ranking", "ranked_results": "ranking",
    "filters": "filters", "misses": "misses", "gaps": "misses", "conflicts": "conflicts",
    "access_notes": "access_notes", "licensing": "access_notes", "run_log": "run_log",
    "summary": "__summary__", "case_id": "case_id",
}


def contract_names(case):
    contract = None
    if isinstance(case, dict):
        contract = case.get("output_contract")
        if contract is None and isinstance(case.get("contract"), dict):
            contract = case["contract"].get("output_contract")
    names = []
    if isinstance(contract, dict):
        names = [str(key) for key in contract.keys()]
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                for key in ("name", "field", "key", "id"):
                    if isinstance(item.get(key), str):
                        names.append(item[key])
                        break
    return names, contract


def case_identifier(case):
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "id"):
            value = case.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def apply_contract(payload, case, summary_text):
    names, contract = contract_names(case)
    mapping = {}
    notes = {}
    extras = {}
    payload_keys = {norm_key(key): key for key in payload.keys()}

    for name in names:
        normalized = norm_key(name)
        if name in payload and normalized not in ("status",):
            mapping[name] = name
            continue
        target = CONTRACT_ALIASES.get(normalized)
        if normalized == "status":
            # 契约字段 status 在本题按法律状态交付；顶层 status 保留运行状态
            payload.setdefault("legal_status", [])
            mapping[name] = "legal_status"
            notes[name] = "契约字段 status 映射为 legal_status；顶层 status 仍是运行状态。"
            extras.setdefault("status", {
                "run_status": payload.get("status"),
                "legal_status_field": "legal_status",
                "note": "同一名称存在两种语义，已分别保留。",
            })
            continue
        if target == "__summary__":
            payload[name] = summary_text
            mapping[name] = "__summary__"
            continue
        if target and target in payload:
            payload[name] = payload[target]
            mapping[name] = target
            continue
        if name in payload_keys.values():
            mapping[name] = name
            continue
        # 最后尝试宽松匹配：契约键与已有键互相包含
        matched = None
        for key in payload.keys():
            if normalized and (normalized in norm_key(key) or norm_key(key) in normalized):
                matched = key
                break
        if matched:
            payload[name] = payload[matched]
            mapping[name] = matched
        else:
            payload[name] = None
            mapping[name] = None
            notes[name] = "未产出该字段；保留 null 以满足契约完整性，原因见 contract_extras。"
            extras[name] = {"value": None, "reason": "no producer for this field in the deterministic pipeline"}

    present = {name: (name in payload and payload[name] is not None) for name in names}
    payload["contract_notes"] = notes
    payload["contract_map"] = mapping
    payload["contract_extras"] = extras
    payload["output_contract_fields"] = {
        "provided": names,
        "present": present,
        "mapping": mapping,
        "contract_declared": contract if isinstance(contract, (dict, list)) else None,
    }
    return payload


# ---------------------------------------------------------------------------
# run-review.md
# ---------------------------------------------------------------------------
def md_cell(value):
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def build_review(payload):
    stats = payload.get("stats", {})
    lines = [
        "# 检索复核记录（run-review）",
        "",
        "> 本文件由 `scripts/sk_entry.py` 在本地确定性生成；不含网络查询，不替代官方登记簿核验。",
        "",
        "## 1. 运行标识",
        "",
        "| 字段 | 值 |",
        "|---|---|",
        "| case_id | %s |" % md_cell(payload.get("case_id") or "(未提供)"),
        "| status | %s |" % md_cell(payload.get("status")),
        "| 记录（原始/去重） | %s / %s |" % (stats.get("records_raw", 0), stats.get("records", 0)),
        "| 候选 / 排除 | %s / %s |" % (stats.get("candidates", 0), stats.get("excluded", 0)),
        "| 同族组 | %s |" % stats.get("families", 0),
        "| 冲突 | %s |" % stats.get("conflicts", 0),
        "",
        "## 2. 检索式",
        "",
        "| id | 类型 | 表达式 | 命中 | 无结果说明 |",
        "|---|---|---|---|---|",
    ]
    for query in payload.get("queries", []):
        lines.append("| %s | %s | `%s` | %s | %s |" % (
            md_cell(query.get("id")), md_cell(query.get("type")),
            md_cell(query.get("expression"))[:180], query.get("hits", 0),
            md_cell(query.get("no_result_reason") or "")))
    lines.extend(["", "## 3. 候选与定位", "",
                  "| # | 标识符 | 类型 | 分数 | 状态 | 权限 | 核验 | 定位 |",
                  "|---|---|---|---|---|---|---|---|"])
    for candidate in payload.get("candidates", [])[:30]:
        lines.append("| %s | %s | %s | %s/%s | %s | %s | %s | `%s` |" % (
            candidate.get("rank"), md_cell(candidate.get("identifier")),
            md_cell(candidate.get("doc_type")), candidate.get("score"), candidate.get("score_max"),
            md_cell(candidate.get("legal_status")), md_cell(candidate.get("access_level")),
            md_cell(candidate.get("verification")), md_cell(candidate.get("evidence_locator"))))
    lines.extend(["", "## 4. 同族", "",
                  "| family_key | 规模 | 辖区 | 状态 | 依据 |",
                  "|---|---|---|---|---|"])
    for family in payload.get("family_groups", [])[:30]:
        lines.append("| %s | %s | %s | %s | %s |" % (
            md_cell(family.get("family_key")), family.get("size"),
            md_cell(",".join(family.get("jurisdictions", []))),
            md_cell(",".join(family.get("statuses", []))), md_cell(family.get("basis"))))
    lines.extend(["", "## 5. 法律状态", "",
                  "| 标识符 | 状态 | 状态日期 | 来源 | 时效 | 置信度 |",
                  "|---|---|---|---|---|---|"])
    for row in payload.get("legal_status", [])[:40]:
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            md_cell(row.get("identifier")), md_cell(row.get("status")),
            md_cell(row.get("status_date")), md_cell(row.get("status_source")),
            md_cell(row.get("freshness")), md_cell(row.get("confidence"))))
    lines.extend(["", "## 6. 权限与许可", "",
                  "| 标识符 | 级别 | 颜色 | 可展示范围 | 处置 |",
                  "|---|---|---|---|---|"])
    for note in payload.get("access_notes", [])[:40]:
        lines.append("| %s | %s | %s | %s | %s |" % (
            md_cell(note.get("identifier")), md_cell(note.get("access_level")),
            md_cell(note.get("color")), md_cell(note.get("allowed_display")),
            md_cell(note.get("action"))))
    lines.extend(["", "## 7. 排除、遗漏与无结果", ""])
    lines.append("排除：")
    for item in payload.get("excluded", [])[:40]:
        lines.append("- `%s` %s（%s）" % (item.get("identifier"), item.get("reason"), item.get("reason_code")))
    if not payload.get("excluded"):
        lines.append("- 无")
    lines.append("")
    lines.append("遗漏：")
    for item in payload.get("misses", [])[:40]:
        lines.append("- `%s`（%s）：%s" % (item.get("target"), item.get("type"), item.get("reason")))
    if not payload.get("misses"):
        lines.append("- 无")
    lines.append("")
    lines.append("无结果说明：")
    for item in payload.get("no_result_reasons", [])[:40]:
        lines.append("- [%s] %s" % (item.get("scope"), item.get("reason")))
    if not payload.get("no_result_reasons"):
        lines.append("- 无")
    lines.extend(["", "## 8. 冲突", ""])
    for item in payload.get("conflicts", [])[:30]:
        lines.append("- %s `%s`：%s（建议：%s）" % (
            item.get("type"), item.get("identifier"), item.get("note"), item.get("suggested_action")))
    if not payload.get("conflicts"):
        lines.append("- 无")
    lines.extend(["", "## 9. 不确定性与限制", ""])
    for item in payload.get("uncertainty", []):
        lines.append("- **%s**：%s" % (item.get("type"), item.get("detail")))
    lines.extend(["", "## 10. 自检清单", ""])
    candidates = payload.get("candidates", [])
    complete = all(candidate.get("score") is not None and candidate.get("rationale")
                   and candidate.get("evidence_locator") for candidate in candidates)
    contract = payload.get("output_contract_fields", {})
    present = contract.get("present", {})
    restricted_ok = all(candidate.get("verification") != "text_checked"
                        for candidate in candidates
                        if candidate.get("access_level") in ("restricted", "prohibited"))
    zero_ok = all(query.get("no_result_reason") for query in payload.get("queries", [])
                  if query.get("hits", 0) == 0)
    lines.append("- [%s] 每条候选均有 score、rationale、evidence_locator" % ("x" if complete else " "))
    lines.append("- [%s] output_contract 全部字段存在：%s" % (
        "x" if all(present.values()) else " ", ", ".join("%s=%s" % (key, value) for key, value in present.items())))
    lines.append("- [%s] 受限/禁止记录未按全文核验呈现" % ("x" if restricted_ok else " "))
    lines.append("- [%s] 每个零命中查询均有显式 no_result_reason" % ("x" if zero_ok else " "))
    lines.append("- [x] 无网络、无付费墙绕过、无补造记录")
    lines.extend(["", "## 11. 人工定位复核步骤", "",
                  "1. 打开候选的 `evidence_locator`（`path#/json/pointer`、`path:line`、`path:row=N` 或 `path#block=N`）。",
                  "2. 核对该定位处的标题、公开号/DOI、申请人、分类号与候选字段一致。",
                  "3. 核对 `evidence` 中引用的原文片段确实出现在该字段。",
                  "4. 对 `verification=metadata_only/identifier_only` 的记录，不引用摘要或权利要求原文。",
                  "5. 法律状态与同族关系须回官方登记簿复核，并记录查询日期；本文件不作为登记依据。",
                  ""])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def failure_payload(input_path, exc):
    return {
        "case_id": "",
        "status": "failed",
        "candidates": [], "ranking": [], "queries": [], "family_groups": [],
        "legal_status": [], "access_notes": [], "filters": {}, "excluded": [],
        "misses": [], "no_result_reasons": [{"scope": "global", "query_id": None,
                                             "reason": "运行异常，未生成检索结论。"}],
        "conflicts": [], "evidence": [], "uncertainty": [{"type": "run_failure",
                                                           "detail": "运行异常；未补造数据。"}],
        "run_log": [{"step": 1, "action": "resolve_input", "input": str(input_path),
                     "exists": Path(input_path).exists()}],
        "errors": [{"stage": "pipeline", "type": type(exc).__name__, "message": str(exc)[:500]}],
        "stats": {"records_raw": 0, "records": 0, "candidates": 0, "excluded": 0,
                  "families": 0, "conflicts": 0},
        "contract_notes": {}, "contract_map": {}, "contract_extras": {},
        "output_contract_fields": {"provided": [], "present": {}, "mapping": {}},
    }


def build_payload(input_path, policy):
    input_path = Path(input_path)
    case = {}
    case_file = find_case_file(input_path)
    errors = []
    if case_file is not None:
        try:
            loaded = load_json(case_file)
            if isinstance(loaded, dict):
                case = loaded
            else:
                errors.append({"file": case_file.name, "stage": "case_parse",
                               "detail": "case root is not a JSON object"})
        except (OSError, ValueError) as exc:
            errors.append({"file": case_file.name, "stage": "case_parse", "detail": str(exc)[:200]})

    files = iter_files(input_path)
    records_raw, task_texts, file_log = collect_records(files, errors, input_path)
    if not task_texts:
        for candidate in (input_path / "task.md" if input_path.is_dir() else input_path.parent / "task.md",):
            if candidate.is_file():
                task_texts.append(read_text(candidate))
                break

    as_of = parse_date(first_present(case, ("as_of", "as_of_date", "query_date", "evaluation_date")))
    config = build_query_config(case, task_texts, policy)

    records = [normalize_record(raw, locator, source, policy) for locator, raw, source in
               ((locator, raw, locator.split(":")[0].split("#")[0]) for locator, raw in records_raw)]
    for record in records:
        record["access_level"] = classify_access(record, policy)

    deduped, duplicates = deduplicate(records)
    for record in deduped:
        record["access_level"] = classify_access(record, policy)

    families = group_families(deduped, policy)
    for record in deduped:
        record["family_key"] = family_of(record, families)

    queries = annotate_queries(config["queries"], deduped)
    candidates, excluded, _ = build_candidates(deduped, config, policy, as_of)

    stale_days = int(policy.get("stale_days", 180))
    status_rows = legal_status_rows(deduped, as_of, stale_days)
    conflicts = find_conflicts(deduped, families, as_of)

    access_notes = [access_note(record, record["access_level"], policy) for record in deduped]

    evidence = []
    for query in queries:
        evidence.append({"kind": "query", "id": query["id"], "expression": query.get("expression", ""),
                         "locator": str(case_file.name) if case_file else "input", "hits": query.get("hits", 0)})
    for candidate in candidates:
        evidence.append({"kind": "candidate", "identifier": candidate["identifier"],
                         "locator": candidate["evidence_locator"], "score": candidate["score"],
                         "quotes": [item.get("quote") for item in candidate.get("evidence", [])
                                    if item.get("quote")][:3]})
    for family in families:
        if family["size"] > 1:
            evidence.append({"kind": "family", "family_key": family["family_key"],
                             "members": [member["identifier"] for member in family["members"]],
                             "locator": family["members"][0]["locator"] if family["members"] else None})
    for row in status_rows:
        evidence.append({"kind": "legal_status", "identifier": row["identifier"], "status": row["status"],
                         "status_date": row["status_date"], "status_source": row["status_source"],
                         "freshness": row["freshness"]})
    for note in access_notes:
        if note["access_level"] != "open":
            evidence.append({"kind": "access", "identifier": note["identifier"],
                             "access_level": note["access_level"], "allowed_display": note["allowed_display"],
                             "note": note["note"]})

    misses = build_misses(deduped, config)
    no_result_reasons = build_no_result_reasons(queries, candidates, deduped, errors)

    stats = {
        "records_raw": len(records_raw),
        "records": len(deduped),
        "duplicates_merged": len(duplicates),
        "candidates": len(candidates),
        "excluded": len(excluded) + len(duplicates),
        "families": len(families),
        "merged_families": sum(1 for family in families if family["size"] > 1),
        "conflicts": len(conflicts),
        "restricted": sum(1 for record in deduped if record["access_level"] == "restricted"),
        "prohibited": sum(1 for record in deduped if record["access_level"] == "prohibited"),
        "parse_errors": len(errors),
    }

    if not deduped:
        status = "insufficient_input"
    elif errors or not candidates:
        status = "partial"
    else:
        status = "success"

    case_id = case_identifier(case)
    zero_queries = sum(1 for query in queries if query.get("hits", 0) == 0)
    summary_text = build_summary(case_id, status, stats, len(conflicts), stats["restricted"], zero_queries)

    payload = {
        "case_id": case_id,
        "status": status,
        "queries": queries,
        "query_expression": config["combined_expression"],
        "candidates": candidates,
        "ranking": [{"rank": candidate["rank"], "candidate_id": candidate["candidate_id"],
                     "identifier": candidate["identifier"], "score": candidate["score"],
                     "score_pct": candidate["score_pct"]} for candidate in candidates],
        "family_groups": families,
        "legal_status": status_rows,
        "access_notes": access_notes,
        "filters": build_filters(case, policy, config, len(deduped), len(candidates),
                                 len(excluded) + len(duplicates)),
        "excluded": excluded + [{
            "record_id": item["record_id"],
            "identifier": item["identifier"],
            "title": None,
            "score": 0,
            "reason_code": "duplicate_merged",
            "reason": item["reason"],
        } for item in duplicates],
        "misses": misses,
        "no_result_reasons": no_result_reasons,
        "conflicts": conflicts,
        "evidence": evidence,
        "uncertainty": build_uncertainty(deduped, candidates, errors, config, as_of),
        "errors": errors,
        "stats": stats,
        "run_log": build_run_log(input_path, file_log, records, deduped, duplicates, queries,
                                 candidates, excluded, families, conflicts, errors, as_of),
        "summary": summary_text,
        "input": {
            "path": str(input_path),
            "case_file": str(case_file) if case_file else None,
            "files": file_log,
            "as_of": as_of.isoformat() if as_of else None,
            "network": "disabled",
        },
        "normalization_rules": {
            "publication_number": "国家码 + 序号 + 种类码；去除空格/连字符/斜杠/点并大写；无法拆分时保留原文。",
            "application_number": "去除分隔符并大写；不推断缺失段。",
            "classification": "提取 IPC/CPC 主组前缀（如 A61K）。",
            "dedup": "公开号 → 申请号 → DOI → locator。",
            "family": "声明 family_id、共享优先权号或声明同族成员交叉合并。",
            "legal_status": "granted_or_active > pending > expired_or_invalid > unknown。",
        },
    }
    payload = apply_contract(payload, case, summary_text)
    return payload


def write_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        os.replace(tmp, str(target))
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def write_outputs(out_path, payload):
    write_json(out_path, payload)
    review_path = Path(out_path).parent / "run-review.md"
    review_path.write_text(build_review(payload), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="专利与技术文献检索（确定性、离线、标准库）")
    parser.add_argument("--input", "--case", dest="input", default=DEFAULT_INPUT,
                        help="输入目录或案例文件（默认 /input）")
    parser.add_argument("--work", default=DEFAULT_WORK, help="工作目录（默认 /app/work）")
    parser.add_argument("--out", "--output", dest="out", default=DEFAULT_OUT,
                        help="结果 JSON 路径（默认 /app/output/result.json）")
    args = parser.parse_args(argv)

    try:
        Path(args.work).mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    policy = load_policy()
    try:
        payload = build_payload(args.input, policy)
        code = 0
    except Exception as exc:  # 任何异常都写结构化失败结果，不崩溃
        payload = failure_payload(args.input, exc)
        code = 2
        try:
            payload = apply_contract(payload, {}, "")
        except Exception:
            pass

    out_path = Path(args.out)
    if out_path.suffix.lower() != ".json" or out_path.is_dir():
        out_path = out_path / "result.json"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    try:
        write_outputs(str(out_path), payload)
    except Exception as exc:
        sys.stderr.write(json.dumps({"status": "failed", "stage": "write_output",
                                     "message": str(exc)[:300]}, ensure_ascii=False) + "\n")
        return 3

    sys.stdout.write(json.dumps({
        "status": payload.get("status"),
        "case_id": payload.get("case_id"),
        "out": str(args.out),
        "records": payload.get("stats", {}).get("records", 0),
        "candidates": payload.get("stats", {}).get("candidates", 0),
    }, ensure_ascii=False) + "\n")
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
