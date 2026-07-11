import json
import tempfile
import unittest
from pathlib import Path

import gfw_uani_reproduce as reproduce


class GfwUaniReproductionTests(unittest.TestCase):
    def test_imo_checksum(self):
        self.assertTrue(reproduce.imo_is_valid("9524475"))
        self.assertFalse(reproduce.imo_is_valid("9524476"))

    def test_contextual_extraction_rejects_bad_checksum(self):
        articles = [{"url": "https://uani.test/a", "title": "Tracker", "text": "IMO 9524475 and IMO no. 9524476"}]
        valid, rejected = reproduce.extract_uani_imos(articles)
        self.assertEqual([row["imo"] for row in valid], ["9524475"])
        self.assertEqual([row["imo"] for row in rejected], ["9524476"])

    def test_deterministic_join_and_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            uani = root / "uani.json"
            gfw = root / "gfw.json"
            manifest = root / "manifest.json"
            uani.write_text(json.dumps([
                {"url": "https://uani.test/feb-2022", "title": "February 2022 Iran Tanker Tracking", "text": "Listed vessel IMO 9524475", "fetched_at": "2026-07-10T00:00:00Z"},
            ]), encoding="utf-8")
            gfw.write_text(json.dumps({"entries": [
                {"imo": "9524475", "vesselId": "v1", "mmsi": "538010676", "name": "SUEZ RAJAN", "date": "2026-06-11", "hours": 10},
                {"imo": "9524475", "vesselId": "v1", "mmsi": "538010676", "name": "SUEZ RAJAN", "date": "2026-06-11", "hours": 10},
                {"imo": "9524475", "vesselId": "v1", "mmsi": "538010676", "name": "ST. NIKOLAS", "date": "2026-06-12", "hours": 12},
                {"imo": "1234568", "vesselId": "bad", "hours": 1},
            ]}), encoding="utf-8")
            manifest.write_text(json.dumps({
                "gfw_dataset": "public-global-presence", "gfw_dataset_version": "2026-07-10",
                "gfw_query_url": "https://gateway.api.globalfishingwatch.org/test", "pagination": {"page_size": 1000, "method": "cursor"},
                "bbox": [54, 22, 62, 28], "start_date": "2026-06-10", "end_date": "2026-07-10",
                "uani_hub_url": "https://www.unitedagainstnucleariran.com/tanker-tracker", "uani_corpus_version": "sha256 inventory v1",
                "exported_at": "2026-07-10T00:00:00Z",
            }), encoding="utf-8")
            report, normalized, mentions = reproduce.reproduce(str(uani), str(gfw), str(manifest))
            self.assertEqual(report["intermediate_counts"]["matches"], 1)
            self.assertEqual(report["matches"][0]["currently_broadcasting_as"], "ST. NIKOLAS")
            self.assertEqual(report["matches"][0]["presence_hours"], 22)
            self.assertEqual(report["matches"][0]["presence_days"], 2)
            self.assertEqual(report["matches"][0]["observed_names"], ["ST. NIKOLAS", "SUEZ RAJAN"])
            self.assertEqual(len(report["inputs"]["gfw_export"]["sha256"]), 64)
            self.assertEqual(len(normalized), 2)
            self.assertEqual(len(mentions), 1)
            self.assertEqual(report["intermediate_counts"]["duplicate_gfw_records_removed"], 1)

    def test_manifest_requires_reproducibility_fields(self):
        with self.assertRaises(ValueError):
            reproduce.validate_manifest({"bbox": [54, 22, 62, 28]})


if __name__ == "__main__":
    unittest.main()
