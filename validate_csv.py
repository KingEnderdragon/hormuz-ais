"""
Checks that every row in a poller CSV has exactly as many fields as its own
header declares. Run after any manual edit or migration of
straits_live_timeseries.csv (or similar poller output) before trusting it -
this is exactly the check that would have caught the schema-mismatch bug
where old rows (written before darkening_ratio/darkening_alert existed)
got silently misaligned against a newer header.

Usage: python validate_csv.py <path>
"""
import csv
import sys


def validate(path):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        n = len(header)
        bad = []
        total = 0
        for i, row in enumerate(reader, start=2):
            total += 1
            if len(row) != n:
                bad.append((i, len(row), row))

    if bad:
        print(f"FAIL: {path} - header has {n} fields, but {len(bad)}/{total} "
              f"data rows don't match:")
        for line_no, count, row in bad[:10]:
            print(f"  line {line_no}: {count} fields -> {row}")
        if len(bad) > 10:
            print(f"  ... and {len(bad) - 10} more")
        return False

    print(f"OK: {path} - all {total} data rows have {n} fields, matching the header.")
    return True


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python validate_csv.py <path>")
        sys.exit(2)
    sys.exit(0 if validate(sys.argv[1]) else 1)
