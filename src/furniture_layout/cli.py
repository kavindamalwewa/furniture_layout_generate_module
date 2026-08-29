from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .optimizer import NoValidLayoutError
from .service import generate_layouts


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate scored furniture layouts from JSON")
    parser.add_argument("input", type=Path, help="Input JSON file")
    parser.add_argument("-o", "--output", type=Path, help="Optional output JSON file")
    args = parser.parse_args()
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        result = generate_layouts(payload)
    except (KeyError, TypeError, ValueError, NoValidLayoutError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2
    output = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

