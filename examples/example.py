"""Three things yaconfiglib does, runnable from any directory.

    python examples/example.py

Every path is resolved against this file's own directory, so the working
directory does not matter.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from yaconfiglib import ConfigLoader, ConfigLoaderMergeMethod, typed_merge

HERE = Path(__file__).resolve().parent

logging.basicConfig(level=logging.INFO)


def show(title, value):
    print(f"\n=== {title} ===")
    print(json.dumps(value, indent=2, default=str))


def layered_load(loader):
    """Several sources, deep-merged, then interpolated once as a whole.

    `layered.yaml` pulls in a JSON and an INI document with `!load`, and
    `hiera.yaml` contributes Jinja expressions. With `interpolate=True` the
    expressions are rendered against the *merged* document, so a value in one
    file can refer to a key that came from another.
    """
    return loader.load(
        "layered.yaml",
        "hiera.yaml",
        interpolate=True,
        merge=ConfigLoaderMergeMethod.Deep,
    )


def templated_source(loader):
    """A `.j2` source is rendered first, then parsed as the format it names.

    `jinja.yaml.j2` renders to YAML, so it is parsed as YAML.
    """
    return loader.load("jinja.yaml.j2")


@dataclass
class ServerConfig:
    host: str
    port: int
    scheme: str = "https"


def typed_merge_demo():
    """Merge several objects into one instance of a type, guided by its hints.

    The dict's `port` arrives as a string and is coerced to `int`, because
    that is what the field is annotated as. A `None` is skipped rather than
    overriding an earlier value.
    """
    return typed_merge(
        ServerConfig,
        ServerConfig(host="localhost", port=8080),
        {"host": "example.com", "port": "443"},
        {"scheme": None},
    )


def main():
    loader = ConfigLoader(base_dir=HERE)

    show("Layered load with interpolation", layered_load(loader))
    show("Jinja2-templated source", templated_source(loader))

    merged = typed_merge_demo()
    show("typed_merge into a dataclass", vars(merged))
    print(f"port is {merged.port!r}, coerced to {type(merged.port).__name__}")


if __name__ == "__main__":
    main()
