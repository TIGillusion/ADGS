#!/usr/bin/env python
"""
csv_cleaner.py — A general-purpose CSV data cleaning utility.

Features:
  - Read any CSV file with automatic encoding detection fallback
  - Handle missing values (NaN): fill with custom value or drop rows/columns
  - Remove duplicate rows (based on all columns or a subset)
  - Auto-detect and convert column types (int, float, date, bool, string)
  - CLI interface with --input, --output, --fill-missing, --dedupe, --columns,
    --drop-missing, --type-convert, --encoding, --dry-run

Usage examples:
  python csv_cleaner.py -i raw.csv -o clean.csv --fill-missing "" --dedupe
  python csv_cleaner.py -i raw.csv -o clean.csv --drop-missing rows --dedupe --columns name,age
  python csv_cleaner.py -i raw.csv --dry-run
"""

import argparse
import csv
import io
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core cleaning functions
# ---------------------------------------------------------------------------

def read_csv(path: str, encoding: str = "utf-8") -> pd.DataFrame:
    """Read a CSV file with encoding fallback."""
    encodings = [encoding, "utf-8", "utf-8-sig", "gbk", "gb2312", "latin-1"]
    seen = set()
    for enc in encodings:
        if enc in seen:
            continue
        seen.add(enc)
        try:
            df = pd.read_csv(path, encoding=enc)
            log.info("Read %s with encoding=%s  (%d rows, %d cols)", path, enc, len(df), len(df.columns))
            return df
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise RuntimeError(f"Could not decode {path} with any tried encoding: {seen}")


def fill_missing(df: pd.DataFrame, fill_value: str, columns: list[str] | None = None) -> pd.DataFrame:
    """
    Fill NaN values.
    - If fill_value is '__drop__', drop rows containing any NaN.
    - Otherwise fill string columns with fill_value, numeric columns with 0,
      datetime columns with NaT (unchanged).
    - columns (optional): limit to specific columns.
    """
    if fill_value == "__drop__":
        cols = columns or list(df.columns)
        before = len(df)
        df = df.dropna(subset=cols)
        log.info("Dropped %d rows with missing values (in columns %s)", before - len(df), cols)
        return df

    before = df.isna().sum().sum()
    if before == 0:
        log.info("No missing values found, skip fill.")
        return df

    target_cols = columns or list(df.columns)
    for col in target_cols:
        if col not in df.columns:
            log.warning("Column '%s' not found, skipping.", col)
            continue
        if df[col].dtype in ["object", "string"]:
            df[col] = df[col].fillna(fill_value)
        elif pd.api.types.is_numeric_dtype(df[col]):
            df[col] = df[col].fillna(0)
        # datetime cols stay as NaT
    after = df.isna().sum().sum()
    log.info("Filled %d missing values (%d remaining NaN).", before - after, after)
    return df


def drop_na(df: pd.DataFrame, how: str = "any", columns: list[str] | None = None) -> pd.DataFrame:
    """
    Drop rows/columns with NaN.
    - how: 'rows' drops rows, 'any'/'all' drops columns
    - columns: limit to subset (for rows drop)
    """
    if how == "rows":
        before = len(df)
        df = df.dropna(subset=columns, how="any")
        log.info("Dropped %d rows with missing values.", before - len(df))
    else:
        before = len(df.columns)
        df = df.dropna(axis="columns", how=how)
        log.info("Dropped %d columns with missing values.", before - len(df.columns))
    return df


def deduplicate(df: pd.DataFrame, columns: list[str] | None = None, keep: str = "first") -> pd.DataFrame:
    """Remove duplicate rows based on all columns or a subset."""
    before = len(df)
    if columns:
        existing = [c for c in columns if c in df.columns]
        df = df.drop_duplicates(subset=existing, keep=keep)
        log.info("Deduplicated by %s.", existing)
    else:
        df = df.drop_duplicates(keep=keep)
    log.info("Removed %d duplicate rows (%d -> %d).", before - len(df), before, len(df))
    return df


def convert_types(df: pd.DataFrame, type_map: dict | None = None) -> pd.DataFrame:
    """
    Convert column types.
    - type_map: {col_name: target_type_str} where target_type_str is one of
      'int', 'float', 'string', 'bool', 'datetime'.
    - If type_map is None, auto-detect: try to parse numeric columns and date columns.
    """
    if type_map:
        for col, t in type_map.items():
            if col not in df.columns:
                log.warning("Column '%s' not found for type conversion, skipping.", col)
                continue
            try:
                if t == "int":
                    df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
                elif t == "float":
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                elif t == "string":
                    df[col] = df[col].astype("string")
                elif t == "bool":
                    df[col] = df[col].map({"1": True, "0": False, "true": True, "false": False, True: True, False: False})
                elif t == "datetime":
                    df[col] = pd.to_datetime(df[col], errors="coerce")
                else:
                    log.warning("Unknown type '%s' for column '%s', skipping.", t, col)
            except Exception as e:
                log.warning("Failed to convert '%s' to %s: %s", col, t, e)
    else:
        # Auto-detect
        for col in df.columns:
            if df[col].dtype == "object":
                # Try date first
                try:
                    converted = pd.to_datetime(df[col], errors="raise")
                    if converted.notna().any():
                        df[col] = converted
                        log.info("Auto-converted '%s' -> datetime.", col)
                        continue
                except (ValueError, TypeError):
                    pass
                # Try numeric
                try:
                    converted = pd.to_numeric(df[col], errors="raise")
                    # Only convert if most values are parseable
                    if converted.notna().sum() / len(converted) > 0.7:
                        if (converted == converted.astype(int)).all():
                            df[col] = converted.astype("Int64")
                            log.info("Auto-converted '%s' -> Int64.", col)
                        else:
                            df[col] = converted
                            log.info("Auto-converted '%s' -> float.", col)
                except (ValueError, TypeError):
                    pass
    return df


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def clean(input_path: str,
          output_path: str | None = None,
          fill_missing_value: str | None = None,
          drop_missing: str | None = None,
          dedupe: bool = False,
          dedupe_columns: list[str] | None = None,
          fill_columns: list[str] | None = None,
          type_map: dict | None = None,
          auto_type: bool = False,
          encoding: str = "utf-8",
          dry_run: bool = False) -> pd.DataFrame:
    """Run the full cleaning pipeline and return the cleaned DataFrame."""
    df = read_csv(input_path, encoding)
    log.info("Starting with %d rows, %d columns.", len(df), len(df.columns))

    # Step 1: Type conversion (before fill/drop so numeric ops work correctly)
    if type_map:
        df = convert_types(df, type_map)
    elif auto_type:
        df = convert_types(df, None)

    # Step 2: Handle missing values
    if fill_missing_value is not None:
        if fill_missing_value == "__drop__":
            df = drop_na(df, how="rows", columns=fill_columns)
        else:
            df = fill_missing(df, fill_missing_value, columns=fill_columns)
    elif drop_missing:
        df = drop_na(df, how=drop_missing, columns=fill_columns)

    # Step 3: Deduplicate
    if dedupe:
        df = deduplicate(df, columns=dedupe_columns)

    log.info("Result: %d rows, %d columns.", len(df), len(df.columns))

    # Step 4: Save
    if output_path and not dry_run:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False, encoding="utf-8")
        log.info("Saved to %s", out)

    return df


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_type_map(s: str) -> dict:
    """Parse 'col1:type1,col2:type2' into dict."""
    result = {}
    for item in s.split(","):
        parts = item.strip().split(":", 1)
        if len(parts) != 2:
            log.warning("Invalid type mapping '%s', expected col:type", item)
            continue
        result[parts[0].strip()] = parts[1].strip()
    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="CSV data cleaning tool — fill missing, dedupe, convert types.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s -i raw.csv -o clean.csv --fill-missing "N/A" --dedupe
  %(prog)s -i raw.csv -o clean.csv --drop-missing rows --columns name,age
  %(prog)s -i raw.csv --dry-run --auto-type
  %(prog)s -i raw.csv -o clean.csv --type-convert "age:int,birthdate:datetime"
""",
    )
    p.add_argument("-i", "--input", required=True, help="Input CSV file path")
    p.add_argument("-o", "--output", help="Output CSV file path (default: <input>_cleaned.csv)")
    p.add_argument("--fill-missing", dest="fill_missing", default=None,
                   help="Value to fill NaN cells (e.g. 'N/A', '', '0'). Use '__drop__' to drop rows with NaN.")
    p.add_argument("--dedupe", action="store_true", help="Remove duplicate rows")
    p.add_argument("--dedupe-columns", dest="dedupe_columns", default=None,
                   help="Columns to deduplicate on (comma-separated); default: all columns")
    p.add_argument("--columns", default=None,
                   help="Limit fill/drop operations to these columns (comma-separated)")
    p.add_argument("--drop-missing", choices=["any", "all", "rows"], default=None,
                   help="Drop rows/cols with missing values: 'rows' (subset), 'any', 'all'")
    p.add_argument("--type-convert", dest="type_convert", default=None,
                   help="Explicit type mapping 'col:type,col:type' (int/float/string/bool/datetime)")
    p.add_argument("--auto-type", action="store_true", help="Auto-detect column types")
    p.add_argument("--encoding", default="utf-8", help="Input file encoding (default: utf-8)")
    p.add_argument("--dry-run", action="store_true", help="Preview cleaning without writing output")
    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not Path(args.input).exists():
        log.error("Input file not found: %s", args.input)
        sys.exit(1)

    output = args.output or str(Path(args.input).stem + "_cleaned.csv")
    fill_cols = [c.strip() for c in args.columns.split(",")] if args.columns else None
    dedupe_cols = [c.strip() for c in args.dedupe_columns.split(",")] if args.dedupe_columns else None
    tmap = parse_type_map(args.type_convert) if args.type_convert else None

    clean(
        input_path=args.input,
        output_path=output if not args.dry_run else None,
        fill_missing_value=args.fill_missing,
        drop_missing=args.drop_missing,
        dedupe=args.dedupe,
        dedupe_columns=dedupe_cols,
        fill_columns=fill_cols,
        type_map=tmap,
        auto_type=args.auto_type,
        encoding=args.encoding,
        dry_run=args.dry_run,
    )

    # Summary
    log.info("Done.")


if __name__ == "__main__":
    main()