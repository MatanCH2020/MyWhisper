"""Synthetic live scan check. Reads own opt-in account, never dictation history."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from chatgpt_auth import ChatGPTAuth
from cloud_http import CloudHTTP
from config import load_config
from history_scanner import ScanEditor, validate_changes

DEMO = [
    {"id": "english", "text": "צריך להעלות את הקוד לגיטהאב ולעשות קומיט דרך פאוורשל."},
    {"id": "spelling", "text": "יש לנו שגיעות כתיב במסמך החדש."},
    {"id": "grammar", "text": "המסמך החדשה מוכנה לשליחה היום."},
    {"id": "protected", "text": "אני לא מאשר 298 שקלים, והפגישה ב-10:30. מאוד מאוד חשוב."},
]


def main():
    cfg = load_config()
    if cfg.get("chatgpt_enabled") is not True:
        sys.exit("Cloud editing is OFF; no request sent.")
    http = CloudHTTP()
    try:
        auth = ChatGPTAuth(http)
        auth.set_enabled(True)
        result = ScanEditor(auth, http, 45).edit(json.dumps({"records": DEMO, "glossary": ["GitHub", "commit", "PowerShell"]}, ensure_ascii=False), cfg.get("chatgpt_model", ""))
        print(f"Live synthetic scan: status={result.status} duration_ms={result.elapsed_ms}")
        if result.status in {"edited", "unchanged"}:
            changes = validate_changes(result.text, DEMO)
            # This output contains only the fixed synthetic examples above.
            print(json.dumps({"changes": changes}, ensure_ascii=True, indent=2))
        else:
            sys.exit(1)
    finally:
        http.close()


if __name__ == "__main__":
    main()
