#!/usr/bin/env python3

import argparse
import json
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[1]
SOURCE_DIR = ROOT / "catalog"
OUTPUT = ROOT / "ai-catalog.json"
MAX_ENTRIES = 10_000
MAX_BYTES = 10 * 1024 * 1024
MCP_CATALOG = "https://api.mcp.github.com/.well-known/ai-catalog.json"


def fail(message):
    raise SystemExit(message)


def validate(entry, source):
    if not isinstance(entry, dict):
        fail(f"{source}: entry must be a JSON object")

    # ponytail: mirror the current ingestion contract without adding a schema dependency.
    for field in ("identifier", "displayName"):
        if not isinstance(entry.get(field), str) or not entry[field].strip():
            fail(f"{source}: {field} must be a non-empty string")
    if not any(
        isinstance(entry.get(field), str) and entry[field].strip()
        for field in ("type", "mediaType")
    ):
        fail(f"{source}: type or mediaType must be a non-empty string")
    has_url = entry.get("url") is not None
    has_data = entry.get("data") is not None
    if has_url == has_data:
        fail(f"{source}: exactly one of url or data is required")
    if has_url and (not isinstance(entry["url"], str) or not entry["url"].strip()):
        fail(f"{source}: url must be a non-empty string")
    if has_url:
        url = urlparse(entry["url"])
        if url.scheme not in ("http", "https") or not url.netloc:
            fail(f"{source}: url must be an absolute HTTP(S) URL")
    if has_data and not isinstance(entry["data"], dict):
        fail(f"{source}: data must be an object")
    if not entry["identifier"].startswith("urn:ai:"):
        fail(f"{source}: identifier must start with urn:ai:")

    version = entry.get("version")
    if version is not None and not isinstance(version, str):
        fail(f"{source}: version must be a string")


def load_mcp_entries():
    try:
        request = Request(MCP_CATALOG, headers={"User-Agent": "agentfinder-catalog-generator"})
        with urlopen(request, timeout=30) as response:
            document = json.load(response)
    except (OSError, URLError, json.JSONDecodeError) as error:
        fail(f"{MCP_CATALOG}: {error}")
    if not isinstance(document, dict) or not isinstance(document.get("entries"), list):
        fail(f"{MCP_CATALOG}: expected a catalog document with an entries array")
    return document["entries"]


def generate():
    entries = []
    identities = set()
    local_identifiers = set()

    def add(entry, source):
        generated = dict(entry)
        generated["identifier"] = "urn:air:" + entry["identifier"][len("urn:ai:") :]
        if not isinstance(generated.get("type"), str) or not generated["type"].strip():
            generated["type"] = entry.get("mediaType")
        version = entry.get("version")
        identity = (generated["identifier"], version)
        if identity in identities:
            fail(
                f"{source}: duplicate identifier/version identity "
                f"{generated['identifier']} {version or ''}"
            )
        identities.add(identity)
        entries.append(generated)

    for path in sorted(SOURCE_DIR.glob("*/*.json"), key=lambda item: item.as_posix()):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            fail(f"{path.relative_to(ROOT)}: invalid JSON: {error}")
        validate(entry, path.relative_to(ROOT))
        add(entry, path.relative_to(ROOT))
        local_identifiers.add(entry["identifier"])

    remote_entries = sorted(
        load_mcp_entries(),
        key=lambda entry: (str(entry.get("identifier")), str(entry.get("version"))),
    )
    for index, entry in enumerate(remote_entries):
        source = f"{MCP_CATALOG} entry {index}"
        validate(entry, source)
        if entry["identifier"] not in local_identifiers:
            add(entry, source)

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
