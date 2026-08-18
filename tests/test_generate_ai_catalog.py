import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import generate_ai_catalog


class ValidateEntryTest(unittest.TestCase):
    def test_display_name_precedence(self):
        entry = {"displayName": "owner/repo", "title": "Catalog title", "name": "server-name"}
        registry_record = {
            "server": {
                "title": "Registry title",
                "_meta": {
                    "io.modelcontextprotocol.registry/publisher-provided": {
                        "github": {"displayName": "GitHub display name"}
                    }
                },
            }
        }
        self.assertEqual(
            generate_ai_catalog.display_name(entry, registry_record),
            "GitHub display name",
        )
        self.assertEqual(
            generate_ai_catalog.display_name(entry, {"server": {"title": "Registry title"}}),
            "Registry title",
        )
        self.assertEqual(
            generate_ai_catalog.display_name(entry, {}),
            "Catalog title",
        )
        self.assertEqual(
            generate_ai_catalog.display_name({"displayName": "owner/repo"}, {}),
            "owner/repo",
        )

    def test_registry_record_key_from_version_url(self):
        entry = {
            "url": (
                "https://api.mcp.github.com/v0.1/servers/"
                "microsoft%2Fmarkitdown/versions/1.0.0"
            )
        }
        self.assertEqual(
            generate_ai_catalog.registry_record_key(entry),
            ("microsoft/markitdown", "1.0.0"),
        )

    def test_url_or_data(self):
        entry = {
            "identifier": "urn:ai:example.com:test:item",
            "displayName": "Test",
            "type": "application/example",
        }
        for delivery in (
            {"url": ""},
            {"url": "relative"},
            {"url": 1},
            {"data": "value"},
            {},
            {"url": "https://example.com", "data": {}},
        ):
            with self.subTest(delivery=delivery), self.assertRaises(SystemExit):
                generate_ai_catalog.validate(entry | delivery, "test")

        generate_ai_catalog.validate(entry | {"url": "https://example.com"}, "test")
        generate_ai_catalog.validate(entry | {"data": {}}, "test")

    def test_generated_type_falls_back_to_media_type(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "catalog" / "example"
            source.mkdir(parents=True)
            entry = {
                "identifier": "urn:ai:example.com:test:item",
                "displayName": "Test",
                "type": "",
                "mediaType": "application/example",
                "url": "https://example.com",
            }
            (source / "item.json").write_text(json.dumps(entry), encoding="utf-8")
            with (
                patch.object(generate_ai_catalog, "ROOT", root),
                patch.object(generate_ai_catalog, "SOURCE_DIR", root / "catalog"),
                patch.object(generate_ai_catalog, "load_mcp_entries", return_value=[]),
            ):
                rendered, _ = generate_ai_catalog.generate()

        self.assertEqual(json.loads(rendered)["entries"][0]["type"], "application/example")

    def test_generated_mcp_entry_uses_registry_github_display_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "catalog" / "example"
            source.mkdir(parents=True)
            (source / "item.json").write_text(
                json.dumps(
                    {
                        "identifier": "urn:ai:example.com:test:item",
                        "displayName": "Test",
                        "type": "application/example",
                        "url": "https://example.com",
                    }
                ),
                encoding="utf-8",
            )
            remote = {
                "identifier": "urn:ai:registry.modelcontextprotocol.io:owner:repo",
                "displayName": "owner/repo",
                "type": "application/mcp-server+json",
                "url": (
                    "https://api.mcp.github.com/v0.1/servers/"
                    "owner%2Frepo/versions/1.0.0"
                ),
                "version": "1.0.0",
            }
            registry_record = {
                "server": {
                    "name": "owner/repo",
                    "version": "1.0.0",
                    "title": "Registry title",
                    "_meta": {
                        "io.modelcontextprotocol.registry/publisher-provided": {
                            "github": {"displayName": "Publisher display name"}
                        }
                    },
                }
            }
            with (
                patch.object(generate_ai_catalog, "ROOT", root),
                patch.object(generate_ai_catalog, "SOURCE_DIR", root / "catalog"),
                patch.object(generate_ai_catalog, "load_mcp_entries", return_value=[remote]),
                patch.object(
                    generate_ai_catalog,
                    "load_mcp_registry_records",
                    return_value=[registry_record],
                ),
            ):
                rendered, _ = generate_ai_catalog.generate()

        entries = json.loads(rendered)["entries"]
        self.assertEqual(entries[1]["displayName"], "Publisher display name")


if __name__ == "__main__":
    unittest.main()
