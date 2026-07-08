"""Count chars / words / lines of the text passed as the first CLI argument.

Usage: python count.py "<text>"
Prints a JSON object: {"chars": int, "words": int, "lines": int}
"""

import json
import sys


def main() -> None:
    text = sys.argv[1] if len(sys.argv) > 1 else ""
    stats = {
        "chars": len(text),
        "words": len(text.split()),
        "lines": text.count("\n") + 1 if text else 0,
    }
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
