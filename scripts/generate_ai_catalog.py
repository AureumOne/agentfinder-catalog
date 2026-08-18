#!/usr/bin/env python3

import argparse
import json
from pathlib import Path
from urllib.error import URLError
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[1]
SOURCE_DIR = ROOT / "catalog"
OUTPUT = ROOT / "ai-catalog.json"
MAX_ENTRIES = 10_000
MAX_BYTES = 10 * 1024 * 1024
MCP_CATALOG = "https://api.mcp.github.com/.well-known/ai-catalog.json"
MCP_REGISTRY = "https://api.mcp.github.com/v0.1/servers"
MCP_REGISTRY_PAGE_SIZE = 100


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


def load_mcp_registry_records():
    records = []
    cursor = None
    while True:
        query = {"limit": MCP_REGISTRY_PAGE_SIZE}
        if cursor:
            query["cursor"] = cursor
        url = f"{MCP_REGISTRY}?{urlencode(query)}"
        try:
            request = Request(url, headers={"User-Agent": "agentfinder-catalog-generator"})
            with urlopen(request, timeout=30) as response:
                document = json.load(response)
        except (OSError, URLError, json.JSONDecodeError) as error:
            fail(f"{MCP_REGISTRY}: {error}")
        if not isinstance(document, dict) or not isinstance(document.get("servers"), list):
            fail(f"{url}: expected a registry document with a servers array")
        records.extend(document["servers"])
        metadata = document.get("metadata")
        cursor = metadata.get("nextCursor") if isinstance(metadata, dict) else None
        if not cursor:
            return records


def registry_record_key(entry):
    url = entry.get("url")
    if not isinstance(url, str):
        return None
    path = [unquote(part) for part in urlparse(url).path.split("/") if part]
    try:
        servers_index = path.index("servers")
        return path[servers_index + 1], path[servers_index + 3]
    except (ValueError, IndexError):
        return None


def display_name(entry, registry_record=None):
    server = registry_record.get("server", {}) if isinstance(registry_record, dict) else {}
    if not isinstance(server, dict):
        server = {}
    metadata = server.get("_meta", {})
    publisher = (
        metadata.get("io.modelcontextprotocol.registry/publisher-provided", {})
        if isinstance(metadata, dict)
        else {}
    )
    github = publisher.get("github", {}) if isinstance(publisher, dict) else {}
    candidates = (
        github.get("displayName"),
        server.get("title"),
        entry.get("title"),
        entry.get("displayName"),
        entry.get("name"),
    )
    return next((value for value in candidates if isinstance(value, str) and value.strip()), None)


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
    registry_records = {
        (record["server"].get("name"), record["server"].get("version")): record
        for record in load_mcp_registry_records()
        if isinstance(record, dict)
        and isinstance(record.get("server"), dict)
        and isinstance(record["server"].get("name"), str)
        and isinstance(record["server"].get("version"), str)
    } if remote_entries else {}
    for index, entry in enumerate(remote_entries):
        source = f"{MCP_CATALOG} entry {index}"
        validate(entry, source)
        if entry["identifier"] not in local_identifiers:
            generated = dict(entry)
            record = registry_records.get(registry_record_key(entry))
            name = display_name(entry, record)
            if name:
                generated["displayName"] = name
            add(generated, source)

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
