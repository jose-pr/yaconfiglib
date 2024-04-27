import logging
import sys

import yaml

from yaconfiglib.jinja2 import *
from yaconfiglib.toml import *
from yaconfiglib.yaml import *

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

loader = YamlConfigLoader()

yaml.SafeLoader.add_constructor("!load", loader)


config = yaml.safe_load(
    "test: !load {pathname: examples/includeme.yaml, transform: '{ pathname.name: value.include }', key_factory: '%pathname.as_posix()', type: map }"
)

jinjaconfig = loader.load("examples/jinja.yaml.j2")
pyproject = loader.load("pyproject.toml")

from yaconfiglib.hiera import HieraConfigLoader, LogLevel, MergeMethod

a = MergeMethod(1)
c = MergeMethod("SIMPLE")

l = LogLevel("critical")

hieraconf = HieraConfigLoader(
    """pathname:
  stem: root
""",
    "examples/hiera.yaml",
    interpolate=True,
).data

pass
