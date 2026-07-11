"""Reproduce the GFW–UANI cross-reference from archived source artifacts.

Inputs are intentionally local and immutable: a JSON/JSONL UANI article corpus,
a JSON/JSONL GFW export, and a query manifest. The command hashes all three,
normalizes identifiers, joins on valid IMO numbers, and writes the report plus
the two normalized intermediate tables needed to audit the result.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


IMO_CONTEXT_RE = re.compile(
    r"\bIMO(?:\s+(?:number|no\.?))?\s*[:#-]?\s*(\d{7})\b",
    re.IGNORECASE,
)
REQUIRED_MANIFEST_FIELDS = (
    "gfw_dataset",
    "gfw_dataset_version",
    "gfw_query_url",
    "pagination",
    "bbox",
    "start_date",
    "end_date",
    "uani_hub_url",
    "uani_corpus_version",
    "exported_at",
)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_records(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if source.suffix.lower() == ".jsonl":
        with source.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    with source.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return payload
    for key in ("entries", "records", "features", "data", "results"):
        value = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(value, list):
            if key == "features":
                return [item.get("properties", item) for item in value]
            return value
    raise ValueError(f"{source} must contain a JSON array or a supported record array")


def nested(record: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        value: Any = record
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                value = None
                break
            value = value[part]
        if value not in (None, ""):
            return value
    return None


def normalize_imo(value: Any) -> str | None:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits if len(digits) == 7 else None


def imo_is_valid(value: Any) -> bool:
    digits = normalize_imo(value)
    return bool(digits) and sum(int(digits[index]) * (7 - index) for index in range(6)) % 10 == int(digits[-1])


def extract_uani_imos(articles: list[dict[str, Any]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    mentions, rejected = [], []
    for article in articles:
        url = str(nested(article, "url", "source_url") or "")
        title = str(nested(article, "title", "name") or "")
        fetched_at = str(nested(article, "fetched_at", "archived_at", "first_seen") or "")
        text = " ".join(str(nested(article, key) or "") for key in ("text", "body", "body_text", "body_excerpt"))
        for match in IMO_CONTEXT_RE.finditer(f"{title} {text}"):
            imo = match.group(1)
            row = {"imo": imo, "article_title": title, "article_url": url, "article_fetched_at": fetched_at}
            (mentions if imo_is_valid(imo) else rejected).append(row)
    unique = {(row["imo"], row["article_url"]): row for row in mentions}
    rejected_unique = {(row["imo"], row["article_url"]): row for row in rejected}
    return sorted(unique.values(), key=lambda row: (row["imo"], row["article_url"])), sorted(rejected_unique.values(), key=lambda row: (row["imo"], row["article_url"]))


def normalize_gfw(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    valid, rejected = [], []
    for record in records:
        imo = normalize_imo(nested(record, "imo", "imo_number", "imoNumber", "vessel.imo", "vessel_info.imo"))
        row = {
            "imo": imo or "",
            "vessel_id": str(nested(record, "vessel_id", "vesselId", "id", "vessel.id") or ""),
            "mmsi": str(nested(record, "mmsi", "ssvid", "vessel.mmsi") or ""),
            "name": str(nested(record, "name", "ship_name", "shipName", "vessel.name") or ""),
            "flag": str(nested(record, "flag", "flag_code", "flagCode", "vessel.flag") or ""),
            "vessel_type": str(nested(record, "vessel_type", "vesselType", "type", "vessel.type") or ""),
            "date": str(nested(record, "date", "timestamp", "start", "start_date") or "")[:10],
            "presence_hours": float(nested(record, "presence_hours", "presenceHours", "hours", "value") or 0),
        }
        if not imo or not imo_is_valid(imo):
            rejected.append(row)
        else:
            valid.append(row)
    return valid, rejected


def aggregate_gfw(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["imo"]].append(record)
    result = {}
    for imo, rows in grouped.items():
        ordered_rows = sorted(rows, key=lambda row: (str(row.get("date", "")), str(row.get("vessel_id", ""))))
        def latest_nonempty(field: str) -> str:
            values = [str(row[field]) for row in ordered_rows if row.get(field)]
            return values[-1] if values else ""
        result[imo] = {
            "imo": imo,
            "vessel_id": latest_nonempty("vessel_id"),
            "mmsi": latest_nonempty("mmsi"),
            "currently_broadcasting_as": latest_nonempty("name"),
            "flag": latest_nonempty("flag"),
            "gfw_vessel_type": latest_nonempty("vessel_type"),
            "presence_hours": round(sum(float(row["presence_hours"]) for row in rows), 6),
            "presence_days": len({row["date"] for row in rows if row.get("date")}),
            "source_record_count": len(rows),
            "observed_names": sorted({str(row["name"]) for row in rows if row.get("name")}),
        }
    return result


def deduplicate_gfw(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in records:
        fingerprint = tuple(row[field] for field in (
            "imo", "vessel_id", "mmsi", "name", "flag", "vessel_type", "date", "presence_hours"
        ))
        unique[fingerprint] = row
    deduplicated = [unique[key] for key in sorted(unique, key=lambda item: tuple(str(value) for value in item))]
    return deduplicated, len(records) - len(deduplicated)


def validate_manifest(manifest: dict[str, Any]) -> None:
    missing = [field for field in REQUIRED_MANIFEST_FIELDS if manifest.get(field) in (None, "", [])]
    if missing:
        raise ValueError(f"query manifest missing required fields: {', '.join(missing)}")
    bbox = manifest["bbox"]
    if not isinstance(bbox, list) or len(bbox) != 4 or not all(isinstance(value, (int, float)) for value in bbox):
        raise ValueError("query manifest bbox must be [west, south, east, north]")
    if not isinstance(manifest["pagination"], dict) or not manifest["pagination"]:
        raise ValueError("query manifest pagination must describe page size/cursor handling")
    if manifest["start_date"] > manifest["end_date"]:
        raise ValueError("query manifest start_date must not follow end_date")
    datetime.fromisoformat(str(manifest["exported_at"]).replace("Z", "+00:00"))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def reproduce(uani_path: str, gfw_path: str, manifest_path: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, str]]]:
    with Path(manifest_path).open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    validate_manifest(manifest)
    articles, raw_gfw = load_records(uani_path), load_records(gfw_path)
    mentions, rejected_mentions = extract_uani_imos(articles)
    gfw_valid_raw, gfw_rejected = normalize_gfw(raw_gfw)
    gfw_valid, duplicate_gfw_records = deduplicate_gfw(gfw_valid_raw)
    gfw_by_imo = aggregate_gfw(gfw_valid)
    articles_by_imo: dict[str, list[dict[str, str]]] = defaultdict(list)
    for mention in mentions:
        articles_by_imo[mention["imo"]].append(mention)
    matches = []
    for imo in sorted(set(articles_by_imo) & set(gfw_by_imo)):
        matches.append({**gfw_by_imo[imo], "uani_sources": articles_by_imo[imo]})
    distinct_gfw_vessels = {
        row["vessel_id"] or row["mmsi"] or row["imo"] for row in gfw_valid
    }
    report = {
        "schema_version": 1,
        "method": "Deterministic valid-IMO join of archived GFW presence records and archived UANI article text",
        "inputs": {
            "uani_corpus": {"path": str(uani_path), "sha256": sha256_file(uani_path), "articles": len(articles)},
            "gfw_export": {"path": str(gfw_path), "sha256": sha256_file(gfw_path), "records": len(raw_gfw)},
            "query_manifest": {"path": str(manifest_path), "sha256": sha256_file(manifest_path), **manifest},
        },
        "intermediate_counts": {
            "valid_uani_mentions": len(mentions),
            "invalid_uani_mentions": len(rejected_mentions),
            "distinct_valid_uani_imos": len({row["imo"] for row in mentions}),
            "valid_gfw_records_with_imo": len(gfw_valid),
            "duplicate_gfw_records_removed": duplicate_gfw_records,
            "rejected_gfw_records_missing_or_invalid_imo": len(gfw_rejected),
            "distinct_gfw_vessels": len(distinct_gfw_vessels),
            "distinct_gfw_imos": len(gfw_by_imo),
            "matches": len(matches),
        },
        "rejected_uani_imo_mentions": rejected_mentions,
        "matches": matches,
    }
    return report, gfw_valid, mentions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uani-corpus", required=True)
    parser.add_argument("--gfw-export", required=True)
    parser.add_argument("--query-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report, gfw_rows, uani_rows = reproduce(args.uani_corpus, args.gfw_export, args.query_manifest)
    (output / "gfw_uani_report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_csv(output / "normalized_gfw_vessels.csv", gfw_rows, ["imo", "vessel_id", "mmsi", "name", "flag", "vessel_type", "date", "presence_hours"])
    write_csv(output / "normalized_uani_imos.csv", uani_rows, ["imo", "article_title", "article_url", "article_fetched_at"])
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"reproduction input error: {exc}", file=sys.stderr)
        raise SystemExit(2)
