"""
Downloads the three event logs needed for the thesis evaluation.

Sources:
  BPIC 2017  — https://data.4tu.nl/articles/dataset/BPI_Challenge_2017/12696884
  BPIC 2012  — https://data.4tu.nl/articles/dataset/BPI_Challenge_2012/12689204
  Sepsis     — https://data.4tu.nl/articles/dataset/Sepsis_Cases_-_Event_Log/12707639

Usage (from Thesis/goal_to_parameters/):
    python ../evaluation/download_logs.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
RAW_DIR   = REPO_ROOT / "evaluation" / "logs" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

LOGS = [
    {
        "name":     "bpic2017",
        "filename": "bpic2017.xes.gz",
        "url":      "https://data.4tu.nl/file/34c3f44b-3101-4ea9-8281-e38905c68b8d/f3aec4f7-d52c-4217-82f4-57d719a8298c",
        "size_mb":  30,
    },
    {
        "name":     "bpic2012",
        "filename": "bpic2012.xes.gz",
        "url":      "https://data.4tu.nl/file/533f66a4-8911-4ac7-8612-1235d65d1f37/3276db7f-8bee-4f2b-88ee-92dbffb5a893",
        "size_mb":  4,
    },
    {
        "name":     "sepsis",
        "filename": "sepsis.xes.gz",
        "url":      "https://data.4tu.nl/file/33632f3c-5c48-40cf-8d8f-2db57f5a6ce7/643dccf2-985a-459e-835c-a82bce1c0339",
        "size_mb":  1,
    },
]


def download(entry: dict) -> bool:
    import requests

    dest = RAW_DIR / entry["filename"]

    if dest.exists():
        print(f"  SKIP — {entry['filename']} already exists ({dest.stat().st_size // 1024} KB)")
        return True

    print(f"  Downloading {entry['filename']} (~{entry['size_mb']} MB) ...")
    try:
        resp = requests.get(entry["url"], stream=True, timeout=120,
                            headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except Exception as exc:
        print(f"  ERROR: {exc}")
        print(f"  Download manually from: {entry['url']}")
        print(f"  Save as: {dest}")
        return False

    total = int(resp.headers.get("content-length", 0))
    downloaded = 0
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    print(f"\r    {pct:.0f}%  ({downloaded // 1024} KB)", end="", flush=True)
    print()
    print(f"  Saved {dest.stat().st_size // 1024} KB to {dest}")
    return True


def main() -> None:
    print("Downloading event logs to evaluation/logs/raw/")
    print()
    ok = True
    for entry in LOGS:
        print(f"[{entry['name']}]")
        if not download(entry):
            ok = False
        print()

    if ok:
        print("All downloads complete.")
        print("Next step: run  python ../evaluation/convert_xes_to_csv.py --all")
    else:
        print("Some downloads failed — see messages above.")
        print("Download those files manually and save them to evaluation/logs/raw/")
        sys.exit(1)


if __name__ == "__main__":
    main()
