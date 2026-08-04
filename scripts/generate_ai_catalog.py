#!/usr/bin/env python3

import argparse
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[1]
SOURCE_DIR = ROOT / "catalog"
OUTPUT = ROOT / "ai-catalog.json"
MAX_ENTRIES = 10_000
MAX_BYTES = 10 * 1024 * 1024


def fail(message):
    raise SystemExit(message)


def generate():
    entries = []
    identities = set()

    for path in sorted(SOURCE_DIR.glob("*/*.json"), key=lambda item: item.as_posix()):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            fail(f"{path.relative_to(ROOT)}: invalid JSON: {error}")

        if not isinstance(entry, dict):
            fail(f"{path.relative_to(ROOT)}: entry must be a JSON object")

        # ponytail: mirror the current ingestion contract without adding a schema dependency.
        for field in ("identifier", "displayName"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                fail(f"{path.relative_to(ROOT)}: {field} must be a non-empty string")
        if not any(
            isinstance(entry.get(field), str) and entry[field].strip()
            for field in ("type", "mediaType")
        ):
            fail(f"{path.relative_to(ROOT)}: type or mediaType must be a non-empty string")
        if sum(entry.get(field) is not None for field in ("url", "data")) != 1:
            fail(f"{path.relative_to(ROOT)}: exactly one of url or data is required")
        if not entry["identifier"].startswith("urn:ai:"):
            fail(f"{path.relative_to(ROOT)}: identifier must start with urn:ai:")

        version = entry.get("version")
        if version is not None and not isinstance(version, str):
            fail(f"{path.relative_to(ROOT)}: version must be a string")

        generated = dict(entry)
        generated["identifier"] = "urn:air:" + entry["identifier"][len("urn:ai:") :]
        identity = (generated["identifier"], version)
        if identity in identities:
            fail(
                f"{path.relative_to(ROOT)}: duplicate identifier/version identity "
                f"{generated['identifier']} {version or ''}"
            )
        identities.add(identity)
        entries.append(generated)

    if len(entries) > MAX_ENTRIES:
        fail(f"catalog has {len(entries)} entries; limit is {MAX_ENTRIES}")

    document = {
        "specVersion": "1.0",
        "host": {"displayName": "GitHub Agent Finder Catalog"},
        "entries": entries,
    }
    rendered = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()
    if len(rendered) > MAX_BYTES:
        fail(f"generated catalog is {len(rendered)} bytes; limit is {MAX_BYTES}")
    return rendered, len(entries)


def main():
    parser = argparse.ArgumentParser(description="Generate the ARD ingestion catalog")
    parser.add_argument("--check", action="store_true", help="fail if output is missing or stale")
    args = parser.parse_args()

    rendered, count = generate()
    if args.check:
        if not OUTPUT.exists():
            fail(f"{OUTPUT.name} is missing; run {SCRIPT.relative_to(ROOT)}")
        if OUTPUT.read_bytes() != rendered:
            fail(f"{OUTPUT.name} is stale; run {SCRIPT.relative_to(ROOT)}")
    else:
        OUTPUT.write_bytes(rendered)

    print(f"{OUTPUT.name}: {count} entries, {len(rendered)} bytes")


if __name__ == "__main__":
    main()
