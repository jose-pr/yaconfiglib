"""Load examples/advanced.yaml, which shows the `!include` forms.

    python examples/run_advanced.py

Paths resolve against this file's own directory, so the working directory
does not matter.
"""

import json
import logging
from pathlib import Path

from yaconfiglib import ConfigLoader, ConfigLoaderMergeMethod

HERE = Path(__file__).resolve().parent

logging.basicConfig(level=logging.INFO)


def main():
    loader = ConfigLoader(base_dir=HERE)

    config = loader.load(
        "advanced.yaml",
        interpolate=True,
        merge=ConfigLoaderMergeMethod.Deep,
    )

    print("=== advanced.yaml, includes resolved and interpolated ===")
    print(json.dumps(config, indent=2, default=str))


if __name__ == "__main__":
    main()
