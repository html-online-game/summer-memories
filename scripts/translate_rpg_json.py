#!/usr/bin/env python3
"""Translate RPG Maker dialogue text to Japanese while preserving the original structure.

This script targets RPG Maker event command code 401 text blocks (Show Text / dialogue),
translates the text through a public translation API, and writes the result into a
separate output folder without modifying the original files.

Example:
    python scripts/translate_rpg_json.py --input data --output translated_data --source en --target ja
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable, List, Tuple
from urllib import error, request, parse

DEFAULT_API_URL = "https://translate.googleapis.com/translate_a/single"

# Strings that are usually identifiers, filenames, asset names, or numeric-like values.
NON_TEXT_KEYWORDS = {
    "id", "ids", "image", "images", "filename", "fileName", "file_name",
    "tileId", "characterName", "characterIndex", "direction", "pattern",
    "pan", "pitch", "volume", "note", "autoplayBgm", "autoplayBgs",
    "battleback1Name", "battleback2Name", "parallaxName", "tilesetId",
    "displayName", "bgm", "bgs", "data", "mapId", "eventId", "switch1Id",
    "switch2Id", "variableId", "itemId", "actorId", "selfSwitchCh", "actorValid",
    "itemValid", "selfSwitchValid", "switch1Valid", "switch2Valid", "variableValid",
    "script", "code", "type", "className", "class", "kind", "tag", "param",
    "faceName", "faceIndex", "character", "icon", "sound", "music"
}


def normalize_text(s: str) -> str:
    return " ".join(s.strip().split())


def should_translate(value: Any, key: Any = None) -> bool:
    if not isinstance(value, str):
        return False

    text = normalize_text(value)
    if not text:
        return False

    # Skip obvious IDs / technical names / file names.
    if len(text) <= 1:
        return False
    if re.fullmatch(r"[A-Za-z0-9_./:-]+(?:\\s+[A-Za-z0-9_./:-]+)+", text):
        return False

    # Keep real sentences, menu text, descriptions, names, etc.
    return True


def walk_and_collect(obj: Any, path: Tuple[Any, ...] = ()) -> List[Tuple[Tuple[Any, ...], str]]:
    results: List[Tuple[Tuple[Any, ...], str]] = []

    if isinstance(obj, dict):
        if "code" in obj:
            if obj.get("code") == 401:
                parameters = obj.get("parameters", [])
                if isinstance(parameters, list):
                    for index, value in enumerate(parameters):
                        if isinstance(value, str) and should_translate(value):
                            results.append((path + ("parameters", index), value))
            return results

        for key, value in obj.items():
            if key in NON_TEXT_KEYWORDS:
                continue
            if key in {"events", "pages", "list"} or (path and path[-1] in {"events", "pages", "list"}):
                if isinstance(value, (dict, list)):
                    results.extend(walk_and_collect(value, path + (key,)))
        return results
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            if isinstance(item, (dict, list)):
                results.extend(walk_and_collect(item, path + (index,)))

    return results


def set_value_by_path(obj: Any, path: Tuple[Any, ...], value: Any) -> None:
    current = obj
    for part in path[:-1]:
        current = current[part]
    current[path[-1]] = value


def translate_batch(api_url: str, texts: List[str], source: str, target: str, max_retries: int = 10, delay_seconds: float = 1.0) -> List[str]:
    if not texts:
        return []

    results: List[str] = []
    for text in texts:
        if not text:
            results.append("")
            continue

        params = {
            "client": "gtx",
            "sl": source,
            "tl": target,
            "dt": "t",
            "ie": "UTF-8",
            "oe": "UTF-8",
            "q": text,
        }
        url = api_url + "?" + parse.urlencode(params)
        req = request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

        for attempt in range(max_retries):
            try:
                with request.urlopen(req, timeout=60) as response:
                    raw = response.read().decode("utf-8")
                    parsed = json.loads(raw)
                    if isinstance(parsed, list) and parsed and isinstance(parsed[0], list):
                        translated_parts = []
                        for item in parsed[0]:
                            if isinstance(item, list) and item and isinstance(item[0], str):
                                translated_parts.append(item[0])
                        results.append("".join(translated_parts))
                        break
                    raise ValueError(f"Unexpected Google Translate response: {raw}")
            except (error.HTTPError, error.URLError, ValueError, json.JSONDecodeError) as exc:
                if isinstance(exc, error.HTTPError) and exc.code in {429, 500, 502, 503, 504}:
                    if attempt < max_retries - 1:
                        sleep_for = delay_seconds * (2 ** attempt)
                        time.sleep(sleep_for)
                        continue
                if attempt == max_retries - 1:
                    raise RuntimeError(f"Google translation failed for text: {text[:80]} ... {exc}")
                time.sleep(delay_seconds)

    return results


def translate_json_file(input_path: Path, output_path: Path, source: str, target: str, batch_size: int = 1, api_url: str = DEFAULT_API_URL, max_retries: int = 10, delay_seconds: float = 1.0) -> int:
    with input_path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)

    entries = walk_and_collect(data)
    if not entries:
        # Preserve original JSON structure but write a copy in the output folder.
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)
            fp.write("\n")
        return 0

    text_values = [value for _, value in entries]
    translated: List[str] = []
    for index in range(0, len(text_values), batch_size):
        batch = text_values[index:index + batch_size]
        translated.extend(translate_batch(api_url, batch, source, target, max_retries=max_retries, delay_seconds=delay_seconds))

    for (path, _), new_text in zip(entries, translated):
        set_value_by_path(data, path, new_text)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
        fp.write("\n")

    return len(entries)


def iter_json_files(input_dir: Path) -> Iterable[Path]:
    for path in sorted(input_dir.rglob("*.json")):
        if path.is_file():
            yield path


def main() -> int:
    parser = argparse.ArgumentParser(description="Translate RPG Maker JSON data to Japanese and save to a new folder.")
    parser.add_argument("--input", default="data", help="Source folder containing JSON files to translate.")
    parser.add_argument("--output", default="translated_data", help="Output folder for translated JSON files.")
    parser.add_argument("--source", default="en", help="Source language code (example: en).")
    parser.add_argument("--target", default="ja", help="Target language code (example: ja).")
    parser.add_argument("--batch-size", type=int, default=1, help="Number of strings per API call. Use 1 for safer operation under Google public rate limits.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be translated without writing files.")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="Override translation API endpoint.")
    parser.add_argument("--max-retries", type=int, default=10, help="Retries before failing on rate limits or temporary translation errors.")
    parser.add_argument("--delay-seconds", type=float, default=1.0, help="Seconds to wait between retry attempts to reduce Google 429 errors.")
    args = parser.parse_args()

    input_dir = Path(args.input).resolve()
    output_dir = Path(args.output).resolve()

    if not input_dir.exists():
        print(f"Input folder not found: {input_dir}", file=sys.stderr)
        return 1

    count = 0
    for src_path in iter_json_files(input_dir):
        rel_path = src_path.relative_to(input_dir)
        out_path = output_dir / rel_path
        if args.dry_run:
            with src_path.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
            print(f"Would translate {src_path} -> {out_path} ({len(walk_and_collect(data))} strings)")
            count += 1
            continue

        translated_count = translate_json_file(
            src_path,
            out_path,
            args.source,
            args.target,
            batch_size=args.batch_size,
            api_url=args.api_url,
            max_retries=args.max_retries,
            delay_seconds=args.delay_seconds,
        )
        print(f"Translated {src_path} -> {out_path} ({translated_count} strings)")
        count += 1

    print(f"Finished. Processed {count} JSON files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
