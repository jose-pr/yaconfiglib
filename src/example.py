import logging
import sys

import yaml

from yaconfiglib.jinja2 import *
from yaconfiglib.toml import *
from yaconfiglib.yaml import *

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

loader = Include()

yaml.SafeLoader.add_constructor("!include", loader)


config = yaml.safe_load(
    "test: !include {pathname: examples/includeme.yaml, transform: '{ pathname.name: value.include }', key_factory: '%pathname.as_posix()', type: map }"
)

jinjaconfig = loader.load("examples/jinja.yaml.j2")
pyproject = loader.load("pyproject.toml")

from yaconfiglib.hiera import HieraConfigLoader, LogLevel, MergeMethod

a = MergeMethod(1)
c = MergeMethod("SIMPLE")

l = LogLevel("critical")

hieraconf = HieraConfigLoader(
    "examples/hiera.yaml", "examples/jinja.yaml.j2", interpolate=True
).data

pass
