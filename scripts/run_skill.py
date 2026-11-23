#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_skill.py —— 永不让 harness 崩溃的薄封装。

优先调用同目录的 sk_entry.main()；任何导入或运行异常都会被捕获，并在
--out（默认 /app/output/result.json）写出结构化的失败/部分结果后以 0 退出。
调用方式与 sk_entry 完全一致：

    python scripts/run_skill.py --input /input --work /app/work --out /app/output/result.json
"""

import json
import os
import sys
import traceback
from pathlib import Path

DEFAULT_OUT = "/app/output/result.json"


def _target_out(argv):
    out = DEFAULT_OUT
    for index, item in enumerate(argv):
        if item in ("--out", "--output") and index + 1 < len(argv):
            out = argv[index + 1]
        elif item.startswith("--out="):
            out = item.split("=", 1)[1]
        elif item.startswith("--output="):
            out = item.split("=", 1)[1]
    return out


def _minimal_payload(message):
    return {
        "case_id": "",
        "status": "failed",
        "candidates": [],
        "ranking": [],
        "queries": [],
        "family_groups": [],
        "legal_status": [],
        "access_notes": [],
        "filters": {},
        "excluded": [],
        "misses": [],
        "no_result_reasons": [{"scope": "global", "query_id": None,
                               "reason": "封装层捕获异常，未生成检索结论。"}],
        "conflicts": [],
        "evidence": [],
        "uncertainty": [{"type": "wrapper_failure", "detail": "封装层异常；未补造数据。"}],
        "run_log": [{"step": 1, "action": "wrapper", "detail": message}],
        "errors": [{"stage": "wrapper", "message": message}],
        "stats": {},
        "contract_notes": {},
        "contract_map": {},
        "contract_extras": {},
        "output_contract_fields": {"provided": [], "present": {}, "mapping": {}},
    }


def _write_fallback(out_path, message):
    try:
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(_minimal_payload(message), handle, ensure_ascii=False, indent=2)
    except Exception:
        try:
            Path("/app/output").mkdir(parents=True, exist_ok=True)
            with open("/app/output/result.json", "w", encoding="utf-8") as handle:
                json.dump(_minimal_payload(message), handle, ensure_ascii=False, indent=2)
        except Exception:
            pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    try:
        import sk_entry
        code = sk_entry.main(argv)
        if code != 0:
            out_path = _target_out(argv)
            if not Path(out_path).is_file():
                _write_fallback(out_path, "sk_entry returned code %s" % code)
        return 0
    except SystemExit as exc:
        out_path = _target_out(argv)
        if not Path(out_path).is_file():
            _write_fallback(out_path, "sk_entry exited with %s" % getattr(exc, "code", "unknown"))
        return 0
    except BaseException as exc:  # noqa: BLE001 - 必须吞掉一切异常
        message = "%s: %s" % (type(exc).__name__, str(exc)[:300])
        try:
            sys.stderr.write(message + "\n")
            sys.stderr.write(traceback.format_exc()[-1200:] + "\n")
        except Exception:
            pass
        out_path = _target_out(argv)
        if not Path(out_path).is_file():
            _write_fallback(out_path, message)
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        sys.exit(0)
