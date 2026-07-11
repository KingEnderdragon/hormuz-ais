import unittest

import validate_straits_135 as validation


class PortWatchTests(unittest.TestCase):
    def test_exact_parity_and_baseline(self):
        portwatch = [
            {"date": "2025-02-28", "n_total": "80", "n_tanker": "40"},
            {"date": "2025-03-01", "n_total": "100", "n_tanker": "50"},
        ]
        straits = [
            {"asOfDate": "2025-02-28", "count": "80", "nTanker": "40", "baseline": "90", "throughputPct": "89"},
            {"asOfDate": "2025-03-01", "count": "100", "nTanker": "50", "baseline": "90", "throughputPct": "111"},
        ]
        report = validation.portwatch_parity(portwatch, straits, "2025-02-28", "2025-03-01")
        self.assertEqual(report["summary"]["hard_failures"], 0)
        self.assertEqual(report["baseline"]["median"], 90)

    def test_detects_revision_and_count_mismatch(self):
        current = [{"date": "2026-01-01", "total": "11"}]
        previous = [{"date": "2026-01-01", "total": "10"}]
        straits = [{"date": "2026-01-01", "total": "10"}]
        report = validation.portwatch_parity(current, straits, "2025-01-01", "2025-12-31", previous)
        self.assertEqual(report["summary"]["mismatch_count"], 1)
        self.assertEqual(report["summary"]["revision_count"], 1)


class AisGapTests(unittest.TestCase):
    def test_labels_and_default_precision(self):
        rows = [
            {"vessel_type": "tanker", "total_sightings": "60", "hours_missing": "5", "last_lat": "26", "last_lon": "56", "corroborated_dark": "true"},
            {"vessel_type": "tanker", "total_sightings": "60", "hours_missing": "5", "last_lat": "26", "last_lon": "56", "reappeared_at": "2026-07-10T00:00:00Z"},
            {"vessel_type": "cargo", "total_sightings": "60", "hours_missing": "5", "last_lat": "26", "last_lon": "56"},
        ]
        report = validation.ais_gap_audit(rows)
        self.assertEqual(report["default_rule"]["flagged"], 2)
        self.assertEqual(report["default_rule"]["precision"], 0.5)


class FieldTests(unittest.TestCase):
    def test_risk_rules(self):
        rows = [
            {"mmsi": "538123456", "imo": "9074729", "vessel_type": "tanker", "ais_dark": "true", "published_risk": "high"},
            {"mmsi": "211123456", "imo": "9074729", "vessel_type": "cargo", "published_risk": "low"},
        ]
        self.assertEqual(validation.risk_audit(rows)["mismatch_count"], 0)

    def test_documented_foc_mids_only(self):
        self.assertIn(636, validation.FOC_MIDS)  # Liberia
        self.assertIn(351, validation.FOC_MIDS)  # Panama
        self.assertNotIn(370, validation.FOC_MIDS)  # Dominican Republic

    def test_editorial_requires_provenance(self):
        record = {"field": "verdict", "value": "closed", "classification": "editorial", "method_version": "1", "as_of": "2026-07-10T00:00:00Z", "source_name": "carrier advisories", "source_url": "https://example.test"}
        self.assertEqual(validation.editorial_audit([record])["failure_count"], 0)
        del record["method_version"]
        self.assertEqual(validation.editorial_audit([record])["failure_count"], 1)

    def test_market_nearest_timestamp(self):
        actual = [{"iso": "2026-07-10T12:00:00Z", "brent": "75.10", "wti": "72.00"}]
        reference = [{"iso": "2026-07-10T12:02:00Z", "brent": "75.00", "wti": "72.10"}]
        self.assertEqual(validation.market_audit(actual, reference, 0.25, 300)["failures"], [])


if __name__ == "__main__":
    unittest.main()


