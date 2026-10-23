"""Download the official source documents listed in knowledge_base/official_sources.json.

Usage (from backend/):  python -m scripts.fetch_official_kb

Files go to knowledge_base/official/ (git-ignored: official texts are ingested locally, not
redistributed in this repository). PDFs get a sidecar <name>.meta.json; web pages are reduced to
plain text with a front-matter header. Every file records its URL, access date and SHA-256.
"""
import hashlib
import html
import json
import re
import sys
from datetime import date
from pathlib import Path

import httpx

from app.core.config import get_settings

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CyberShieldAI-KB-fetch"}


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|nav|header|footer)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|li|h[1-6]|div|tr)>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n\n".join(ln for ln in lines if len(ln) > 30)  # drop menu fragments


def main() -> int:
    kb = Path(get_settings().knowledge_base_dir)
    out_dir = kb / "official"
    out_dir.mkdir(exist_ok=True)
    sources = json.loads((kb / "official_sources.json").read_text(encoding="utf-8"))
    failures = 0
    with httpx.Client(headers=UA, timeout=120, follow_redirects=True) as client:
        for src in sources:
            resp = client.get(src["url"])
            ctype = resp.headers.get("content-type", "")
            ok = resp.status_code == 200 and (
                resp.content.startswith(b"%PDF") if src["format"] == "pdf" else "html" in ctype
            )
            if not ok:
                print(f"FAILED {src['url']}: HTTP {resp.status_code} {ctype}")
                failures += 1
                continue
            sha = hashlib.sha256(resp.content).hexdigest()
            note = (f"Official document published by {src['organization']}, downloaded from {src['url']} on "
                    f"{date.today().isoformat()} (sha256 {sha[:16]}…). Text extracted automatically; "
                    "verify against the original.")
            meta = {k: src[k] for k in ("title", "organization", "url", "category", "date", "document_type")}
            meta["source_note"] = note
            meta["last_verified"] = date.today().isoformat()  # fetched from the official URL today
            target = out_dir / src["filename"]
            if src["format"] == "pdf":
                target.write_bytes(resp.content)
                target.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
            else:
                header = "---\n" + "\n".join(f"{k}: {v}" for k, v in meta.items()) + "\n---\n\n"
                target.write_text(header + html_to_text(resp.text), encoding="utf-8")
            print(f"saved {target.name}  {len(resp.content):,} bytes  sha256={sha}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
