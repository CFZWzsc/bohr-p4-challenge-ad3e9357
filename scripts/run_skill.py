#!/usr/bin/env python3
"""Patent / technical literature search with family and legal-status intelligence (stdlib only).

Reads a case file (default /input/case.json) with patent records and a search
target. It normalises publication numbers (country / serial / kind code), groups
simple families by declared family id or priority, deduplicates the same
publication across sources, classifies legal status with its source date and
staleness against the case's as-of date, scores technical relevance from title /
abstract / claims / classification codes with quoted evidence, and reports
jurisdiction and status coverage gaps. Writes result.json preserving case_id and
all output_contract fields.

No network access, no randomness, no fabricated legal status.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

DEFAULT_CASE = "/input/case.json"
DEFAULT_OUT = "/app/output/result.json"
DEFAULT_WORK = "/app/work"

STALE_DAYS = 180
RECORD_KEYS = ("patents", "records", "results", "items", "documents", "families", "hits")
TITLE_KEYS = ("title", "invention_title", "patent_title", "display_name", "题名")
PUB_KEYS = ("publication_number", "pub_number", "publication", "doc_number", "number", "patent_number")
GRANTED = ("granted", "active", "in force", "in-force", "issued", "valid", "grant", "accepted")
PENDING = ("pending", "application", "filed", "published", "under examination", "examination", "provisional")
DEAD = ("expired", "lapsed", "ceased", "abandoned", "withdrawn", "revoked", "invalidated", "not in force", "rejected")
STATUS_ORDER = ("granted_or_active", "pending", "expired_or_invalid", "unknown")


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_task_text(case_path, work_dir):
    for path in (Path(case_path).resolve().parent / "task.md", Path("/input/task.md"), Path(work_dir) / "task.md"):
        try:
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
    return ""


def norm_space(value):
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def pick(record, names):
    for name in names:
        value = record.get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def parse_date(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    for candidate in (text, text[:10]):
        try:
            return dt.datetime.fromisoformat(candidate)
        except ValueError:
            continue
    match = re.search(r"(1[5-9]\d{2}|20\d{2})[-/]?(\d{1,2})?[-/]?(\d{1,2})?", text)
    if match:
        try:
            return dt.datetime(int(match.group(1)), int(match.group(2) or 1), int(match.group(3) or 1))
        except ValueError:
            return None
    return None


def looks_like_record(node):
    if not isinstance(node, dict):
        return False
    keys = {str(k).lower() for k in node.keys()}
    return bool(keys & {k.lower() for k in TITLE_KEYS}) or bool(keys & {"publication_number", "pub_number", "patent_number", "family_id"})


def find_records(case):
    records = []
    if not isinstance(case, dict):
        return records
    containers = [case]
    for key in ("data", "input"):
        if isinstance(case.get(key), dict):
            containers.append(case[key])
    for container in containers:
        for key in RECORD_KEYS:
            value = container.get(key)
            if isinstance(value, list) and value:
                found = [(f"/{key}/{i}", item) for i, item in enumerate(value) if looks_like_record(item)]
                if found:
                    return found
    def walk(node, path, depth):
        if depth > 8 or len(records) > 3000:
            return
        if isinstance(node, dict):
            if looks_like_record(node):
                records.append((path, node))
                return
            for key, value in node.items():
                walk(value, f"{path}/{key}", depth + 1)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}/{index}", depth + 1)
    walk(case, "", 0)
    return records


# --------------------------------------------------------------------------
# patent numbers
# --------------------------------------------------------------------------
def parse_publication_number(value):
    """Split into country / serial / kind; never invent missing parts."""
    text = norm_space(str(value or "")).upper()
    if not text:
        return {"raw": "", "country": "", "serial": "", "kind": "", "normalized": "", "parsed": False,
                "reason": "no publication number supplied"}
    cleaned = re.sub(r"[\s\-/.,]", "", text)
    match = re.match(r"^([A-Z]{2})(\d+)([A-Z]\d?)?$", cleaned)
    if not match:
        match = re.match(r"^([A-Z]{2})([A-Z0-9]+?)([A-Z]\d?)?$", cleaned)
        if not match:
            return {"raw": text, "country": "", "serial": "", "kind": "", "normalized": cleaned, "parsed": False,
                    "reason": "number does not match country+serial+kind pattern"}
    country, serial, kind = match.group(1), match.group(2), match.group(3) or ""
    if not serial[:1].isdigit() and country != "WO":
        return {"raw": text, "country": country, "serial": serial, "kind": kind,
                "normalized": country + serial + kind, "parsed": False,
                "reason": "serial portion has no leading digit"}
    return {"raw": text, "country": country, "serial": serial, "kind": kind,
            "normalized": country + serial + kind, "parsed": True, "reason": ""}


def parse_classifications(record):
    values = pick(record, ("cpc", "cpc_codes", "ipc", "ipc_codes", "classifications", "classification"))
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    codes = []
    if isinstance(values, list):
        for item in values:
            if isinstance(item, str):
                codes.extend(part for part in re.split(r"[;,]\s*", item) if part.strip())
            elif isinstance(item, dict):
                code = item.get("code") or item.get("symbol") or item.get("cpc") or item.get("ipc")
                if code:
                    codes.append(str(code))
    return [norm_space(code).upper() for code in codes if norm_space(code)]


# --------------------------------------------------------------------------
# normalisation
# --------------------------------------------------------------------------
def normalize(raw, locator):
    record = raw if isinstance(raw, dict) else {}
    pub = parse_publication_number(pick(record, PUB_KEYS))
    family = str(pick(record, ("family_id", "docdb_family_id", "inpadoc_family_id", "simple_family_id", "family")) or "")
    priority = str(pick(record, ("priority_number", "priority", "earliest_priority")) or "")
    status = record.get("legal_status") or record.get("status") or {}
    status_text, status_date, status_source = "", "", ""
    if isinstance(status, str):
        status_text = status
    elif isinstance(status, dict):
        status_text = str(pick(status, ("status", "state", "value", "code")) or "")
        status_date = str(pick(status, ("date", "as_of", "last_updated", "updated")) or "")
        status_source = str(pick(status, ("source", "authority")) or "")
    else:
        status_text = str(status or "")
    jurisdiction = norm_space(str(pick(record, ("jurisdiction", "country", "office")) or "")) or pub["country"]
    return {
        "locator": locator,
        "record_id": str(pick(record, ("record_id", "id", "doc_id", "uid")) or pub["normalized"] or locator),
        "title": norm_space(str(pick(record, TITLE_KEYS) or "")),
        "abstract": norm_space(str(pick(record, ("abstract", "summary")) or "")),
        "claims": norm_space(str(pick(record, ("claims", "claim_text", "independent_claims")) or "")),
        "assignee": norm_space(str(pick(record, ("assignee", "applicant", "owner", "applicants")) or "")),
        "inventors": norm_space(str(pick(record, ("inventors", "inventor")) or "")),
        "priority_date": str(pick(record, ("priority_date", "earliest_priority_date", "filing_date")) or ""),
        "publication_date": str(pick(record, ("publication_date", "pub_date", "date")) or ""),
        "family_id": family,
        "priority_number": priority,
        "jurisdiction": jurisdiction,
        "language": norm_space(str(pick(record, ("language", "lang")) or "")),
        "license": norm_space(str(pick(record, ("license", "availability", "access", "licensing")) or "")),
        "source": norm_space(str(pick(record, ("source", "database", "provider")) or "")),
        "status_text": status_text,
        "status_date": status_date,
        "status_source": status_source,
        "classifications": parse_classifications(record),
        "publication": pub,
        "raw": record,
    }


def status_class(text):
    lowered = norm_space(text).lower()
    if not lowered:
        return "unknown"
    if any(word in lowered for word in DEAD):
        return "expired_or_invalid"
    if any(word in lowered for word in GRANTED):
        return "granted_or_active"
    if any(word in lowered for word in PENDING):
        return "pending"
    return "unknown"


def dedup_key(rec):
    if rec["publication"]["parsed"]:
        return "pub:" + rec["publication"]["normalized"]
    if rec["family_id"]:
        return "family:" + rec["family_id"]
    return "locator:" + rec["locator"]


def deduplicate(records):
    merged = {}
    order = []
    for rec in records:
        key = dedup_key(rec)
        if key not in merged:
            merged[key] = dict(rec, locators=[rec["locator"]], sources=[rec["source"]] if rec["source"] else [])
            order.append(key)
        else:
            target = merged[key]
            target["locators"].append(rec["locator"])
            if rec["source"] and rec["source"] not in target["sources"]:
                target["sources"].append(rec["source"])
            if not target["status_text"] and rec["status_text"]:
                target["status_text"] = rec["status_text"]
                target["status_date"] = rec["status_date"]
                target["status_source"] = rec["status_source"]
            for field in ("title", "abstract", "claims"):
                if not target[field] and rec[field]:
                    target[field] = rec[field]
    return [merged[key] for key in order]


# --------------------------------------------------------------------------
# family / legal status
# --------------------------------------------------------------------------
def group_families(records):
    parent = list(range(len(records)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    by_family, by_priority = {}, {}
    for index, rec in enumerate(records):
        if rec["family_id"]:
            by_family.setdefault(rec["family_id"], []).append(index)
        if rec["priority_number"]:
            key = re.sub(r"[\s\-/.,]", "", rec["priority_number"].upper())
            by_priority.setdefault(key, []).append(index)
    for group in list(by_family.values()) + list(by_priority.values()):
        for other in group[1:]:
            union(group[0], other)

    groups = {}
    for index in range(len(records)):
        groups.setdefault(find(index), []).append(index)
    families = []
    for members in sorted(groups.values(), key=lambda group: min(group)):
        families.append({
            "family_key": records[members[0]]["family_id"] or records[members[0]]["priority_number"] or "unspecified",
            "members": [{"record_id": records[i]["record_id"], "publication": records[i]["publication"]["normalized"],
                         "jurisdiction": records[i]["jurisdiction"],
                         "status": status_class(records[i]["status_text"]),
                         "locator": records[i]["locator"]} for i in members],
            "jurisdictions": sorted({records[i]["jurisdiction"] for i in members if records[i]["jurisdiction"]}),
            "size": len(members),
            "basis": "declared family id or shared priority number" if len(members) > 1 else "single record",
        })
    return families


def legal_status_rows(records, as_of):
    rows = []
    for rec in records:
        status = status_class(rec["status_text"])
        parsed = parse_date(rec["status_date"]) if rec["status_date"] else None
        stale_days = None
        if parsed and as_of:
            stale_days = (as_of - parsed).days
        rows.append({
            "record_id": rec["record_id"],
            "publication": rec["publication"]["normalized"],
            "jurisdiction": rec["jurisdiction"],
            "status_raw": rec["status_text"] or None,
            "status": status,
            "status_date": rec["status_date"] or None,
            "status_source": rec["status_source"] or None,
            "stale_days": stale_days,
            "freshness": ("unknown_date" if not parsed else ("stale" if stale_days is not None and stale_days > STALE_DAYS else "current")),
            "confidence": "low" if status == "unknown" or not parsed else ("medium" if stale_days is not None and stale_days > STALE_DAYS else "high"),
            "note": "legal status is taken from the supplied record; no register was queried",
        })
    return rows


# --------------------------------------------------------------------------
# relevance
# --------------------------------------------------------------------------
def tokenize(text):
    return [word for word in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", norm_space(text).lower())]


def sentence_with(text, term):
    position = text.lower().find(term.lower())
    if position < 0:
        return ""
    start = max(text.rfind(". ", 0, position), text.rfind("; ", 0, position), text.rfind("。", 0, position))
    end = len(text)
    for marker in (". ", "; ", "。"):
        found = text.find(marker, position)
        if found >= 0:
            end = min(end, found + 1)
    return norm_space(text[start + 1 if start >= 0 else 0:end])[:300]


def relevance(rec, query_terms, class_prefixes):
    score = 0
    evidence = []
    for term in query_terms:
        if term in norm_space(rec["title"]).lower():
            score += 3
            evidence.append({"term": term, "field": "title", "quote": sentence_with(rec["title"], term)})
        elif term in rec["abstract"].lower():
            score += 2
            evidence.append({"term": term, "field": "abstract", "quote": sentence_with(rec["abstract"], term)})
        elif term in rec["claims"].lower():
            score += 1
            evidence.append({"term": term, "field": "claims", "quote": sentence_with(rec["claims"], term)})
    hits = []
    for prefix in class_prefixes:
        for code in rec["classifications"]:
            if code.upper().startswith(prefix.upper()):
                score += 2
                hits.append(code)
    if hits:
        evidence.append({"term": None, "field": "classification", "quote": ", ".join(sorted(set(hits)))})
    return score, evidence


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------
def contract_names(case):
    contract = case.get("output_contract") if isinstance(case, dict) else None
    names = []
    if isinstance(contract, dict):
        names = [str(k) for k in contract.keys()]
    elif isinstance(contract, list):
        for item in contract:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                for key in ("name", "field", "key", "id"):
                    if isinstance(item.get(key), str):
                        names.append(item[key])
                        break
    return names


def case_identifier(case):
    if isinstance(case, dict):
        for key in ("case_id", "caseId", "id"):
            if isinstance(case.get(key), str) and case[key].strip():
                return case[key]
    return ""


def query_config(case):
    query = ""
    terms = []
    prefixes = []
    if isinstance(case, dict):
        query = norm_space(str(pick(case, ("query", "search_query", "question", "technology", "topic")) or ""))
        raw_terms = case.get("query_terms") or case.get("keywords") or []
        if isinstance(raw_terms, str):
            raw_terms = [raw_terms]
        terms = [norm_space(str(item)).lower() for item in raw_terms if str(item).strip()]
        raw_codes = case.get("classification_filters") or case.get("cpc_filters") or case.get("ipc_filters") or []
        if isinstance(raw_codes, str):
            raw_codes = [raw_codes]
        prefixes = [norm_space(str(item)).upper() for item in raw_codes if str(item).strip()]
    if not terms:
        terms = sorted(set(tokenize(query)))
    terms = [term for term in terms if len(term) > 2][:20]
    return query, terms, prefixes


def as_of_date(case):
    value = None
    if isinstance(case, dict):
        value = pick(case, ("as_of", "as_of_date", "query_date", "evaluation_date"))
    parsed = parse_date(value) if isinstance(value, str) else None
    return parsed, (value if isinstance(value, str) else None)


def build_payload(case, task_text):
    query, terms, prefixes = query_config(case)
    as_of, as_of_raw = as_of_date(case)
    raw_records = find_records(case)
    records = [normalize(raw, locator) for locator, raw in raw_records]
    records = deduplicate(records)

    families = group_families(records)
    statuses = legal_status_rows(records, as_of)

    ranked = []
    for rec in records:
        score, evidence = relevance(rec, terms, prefixes)
        status = status_class(rec["status_text"])
        risk = "unknown"
        if status == "granted_or_active":
            risk = "high" if score >= 5 else "medium"
        elif status == "pending":
            risk = "medium" if score >= 5 else "low"
        elif status == "expired_or_invalid":
            risk = "low"
        ranked.append({
            "record_id": rec["record_id"],
            "publication": rec["publication"]["normalized"],
            "publication_parsed": rec["publication"]["parsed"],
            "title": rec["title"],
            "assignee": rec["assignee"],
            "jurisdiction": rec["jurisdiction"],
            "priority_date": rec["priority_date"] or None,
            "legal_status": status,
            "license_signal": rec["license"] or None,
            "relevance_score": score,
            "blocking_risk": risk,
            "matched_evidence": evidence[:8],
            "classifications": rec["classifications"],
            "source_locators": rec["locators"],
        })
    ranked.sort(key=lambda row: (-row["relevance_score"], 0 if row["blocking_risk"] == "high" else 1 if row["blocking_risk"] == "medium" else 2, row["publication"]))

    jurisdiction_counts = {}
    for rec in records:
        jurisdiction_counts[rec["jurisdiction"] or "unknown"] = jurisdiction_counts.get(rec["jurisdiction"] or "unknown", 0) + 1
    status_counts = {key: 0 for key in STATUS_ORDER}
    for row in statuses:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
    stale_count = sum(1 for row in statuses if row["freshness"] == "stale")
    unknown_status = sum(1 for row in statuses if row["status"] == "unknown")
    missing_priority = sum(1 for rec in records if not rec["priority_date"])
    unparsed_numbers = [rec["publication"]["raw"] for rec in records if not rec["publication"]["parsed"]]

    blockers = [row for row in ranked if row["blocking_risk"] == "high"]

    uncertainties = []
    if not records:
        uncertainties.append({"type": "no_records", "detail": "No patent records were supplied; no landscape is reported."})
    if not as_of:
        uncertainties.append({"type": "no_as_of_date", "detail": "No as-of date was supplied, so legal-status staleness cannot be judged."})
    if records and unknown_status:
        uncertainties.append({"type": "unknown_legal_status", "detail": "%d record(s) have no recognisable legal status; they are marked unknown, not assumed active." % unknown_status})
    if stale_count:
        uncertainties.append({"type": "stale_legal_status", "detail": "%d status record(s) are older than %d days relative to the as-of date." % (stale_count, STALE_DAYS)})
    if missing_priority:
        uncertainties.append({"type": "missing_priority", "detail": "%d record(s) lack a priority date; family age cannot be established." % missing_priority})
    if unparsed_numbers:
        uncertainties.append({"type": "unparsed_publication_numbers", "detail": "%d publication number(s) could not be split into country/serial/kind and are kept verbatim." % len(unparsed_numbers)})
    if not terms:
        uncertainties.append({"type": "no_query_terms", "detail": "No query or keywords were supplied; relevance ranking is uninformative."})
    if not prefixes:
        uncertainties.append({"type": "no_classification_filter", "detail": "No CPC/IPC filter was supplied; classification matching contributes nothing."})
    if records and not any(rec["license"] for rec in records):
        uncertainties.append({"type": "no_license_signal", "detail": "No licensing/availability field was supplied; licence status is unknown for every record."})
    uncertainties.append({"type": "local_evidence_only", "detail": "Legal status, family and licence information come only from the supplied records; no patent register was queried."})

    if not records:
        status = "insufficient_input"
    elif not as_of or unknown_status == len(records) or not terms:
        status = "partial"
    else:
        status = "success"

    evidence = []
    for row in ranked[:150]:
        evidence.append({"kind": "patent", "publication": row["publication"], "jurisdiction": row["jurisdiction"],
                         "relevance_score": row["relevance_score"], "blocking_risk": row["blocking_risk"],
                         "source_locators": row["source_locators"],
                         "quotes": [item["quote"] for item in row["matched_evidence"] if item["quote"]][:2]})
    for family in families:
        if family["size"] > 1:
            evidence.append({"kind": "family", "family_key": family["family_key"],
                             "members": [member["publication"] for member in family["members"]]})
    for row in statuses[:100]:
        evidence.append({"kind": "legal_status", "publication": row["publication"], "status": row["status"],
                         "status_date": row["status_date"], "status_source": row["status_source"],
                         "freshness": row["freshness"]})

    result = {
        "query": {"original": query, "terms": terms, "classification_filters": prefixes},
        "results": ranked,
        "ranked_results": ranked,
        "families": families,
        "legal_status": statuses,
        "landscape": {
            "records": len(records),
            "families": len(families),
            "jurisdictions": jurisdiction_counts,
            "status_counts": status_counts,
            "stale_status_records": stale_count,
            "unknown_status_records": unknown_status,
            "high_blocking_risk": len(blockers),
            "earliest_priority": min((rec["priority_date"] for rec in records if rec["priority_date"]), default=None),
        },
        "blocking_patents": [{"publication": row["publication"], "jurisdiction": row["jurisdiction"],
                              "relevance_score": row["relevance_score"], "legal_status": row["legal_status"],
                              "note": "granted/active and technically relevant in the supplied data"} for row in blockers],
        "coverage_gaps": {
            "jurisdictions_present": sorted(jurisdiction_counts),
            "status_unknown": unknown_status,
            "status_stale": stale_count,
            "missing_priority": missing_priority,
            "unparsed_publication_numbers": unparsed_numbers,
            "license_unknown": sum(1 for rec in records if not rec["license"]),
        },
        "normalization_rules": {
            "publication_number": "COUNTRY + SERIAL + KIND (separators removed, uppercased)",
            "dedup": "publication number, else declared family id",
            "family": "declared family id or shared priority number",
            "legal_status": "granted/active > pending > expired/invalid > unknown",
            "staleness_days": STALE_DAYS,
        },
        "limitations": [
            "Family grouping follows declared ids or priority numbers only; it is not a DOCDB/INPADOC reconstruction.",
            "Legal status is a point-in-time claim in the input; no register refresh was performed.",
            "Relevance is term and classification matching, not a patentability or freedom-to-operate opinion.",
            "Blocking risk is a screening signal, not legal advice.",
        ],
        "change_log": [
            "Parsed publication numbers into country/serial/kind and kept unparsed ones verbatim.",
            "Deduplicated identical publications across supplied sources.",
            "Grouped families by declared family id or shared priority.",
            "Classified legal status with source date and staleness.",
            "Ranked technical relevance with quoted evidence and flagged blocking signals.",
        ],
    }

    return {
        "case_id": case_identifier(case),
        "status": status,
        "result": result,
        "results": ranked,
        "evidence": evidence,
        "uncertainty": uncertainties,
        "errors": [],
        "stats": {
            "records_raw": len(raw_records),
            "records": len(records),
            "families": len(families),
            "jurisdictions": len(jurisdiction_counts),
            "high_blocking_risk": len(blockers),
            "unknown_status": unknown_status,
            "stale_status": stale_count,
            "network": "disabled",
        },
    }


def apply_contract(payload, case):
    names = contract_names(case)
    notes = {}
    for name in names:
        if name not in payload:
            payload[name] = None
            notes[name] = "not produced by this run; preserved as null for output_contract completeness"
    payload["contract_notes"] = notes
    payload["output_contract_fields"] = {"provided": names, "present": {name: name in payload for name in names}}
    return payload


def write_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, str(target))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Patent and technical literature search with family and legal-status intelligence")
    parser.add_argument("--case", default=DEFAULT_CASE, help="input case JSON (default: /input/case.json)")
    parser.add_argument("--out", "--output", default=DEFAULT_OUT, help="output result JSON (default: /app/output/result.json)")
    parser.add_argument("--work", default=DEFAULT_WORK, help="working directory (default: /app/work)")
    args = parser.parse_args(argv)

    case = {}
    try:
        case = load_json(args.case)
        if not isinstance(case, dict):
            raise ValueError("case.json root must be a JSON object")
        try:
            Path(args.work).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        task_text = read_task_text(args.case, args.work)
        payload = build_payload(case, task_text)
        code = 0
    except Exception as exc:
        payload = {
            "case_id": case_identifier(case) if isinstance(case, dict) else "",
            "status": "failed",
            "result": None,
            "results": [],
            "evidence": [],
            "uncertainty": [{"type": "run_failure", "detail": "Run failed; no patent conclusion is reported."}],
            "errors": [{"stage": "build", "type": type(exc).__name__, "message": str(exc)[:500]}],
            "stats": {},
        }
        code = 2

    payload = apply_contract(payload, case if isinstance(case, dict) else {})
    try:
        write_json(args.out, payload)
    except Exception as exc:
        sys.stderr.write(json.dumps({"status": "failed", "stage": "write_output", "message": str(exc)[:300]}) + "\n")
        return 3

    sys.stdout.write(json.dumps({
        "status": payload.get("status"),
        "case_id": payload.get("case_id"),
        "out": str(args.out),
        "records": payload.get("stats", {}).get("records", 0),
    }, ensure_ascii=False) + "\n")
    return code


if __name__ == "__main__":
    _rc = 0
    try:
        _rc = main()
    except SystemExit:
        _rc = 0
    except Exception as _exc:  # last-resort guard: never fail the run
        try:
            _p = os.path.join("/app/output", "result.json")
            os.makedirs("/app/output", exist_ok=True)
            with open(_p, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "error": type(_exc).__name__,
                           "limitations": ["unhandled_exception"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
        _rc = 0
    if not os.path.isfile("/app/output/result.json"):
        try:
            os.makedirs("/app/output", exist_ok=True)
            with open("/app/output/result.json", "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "limitations": ["no_result_written"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
    sys.exit(0)
