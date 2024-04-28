import logging
import sys
from dataclasses import dataclass

import yaml

from yaconfiglib.hiera import HieraConfigLoader, LogLevel, MergeMethod
from yaconfiglib.jinja2 import *
from yaconfiglib.toml import *
from yaconfiglib.utils.merge import typed_merge
from yaconfiglib.yaml import *


@dataclass
class Test:
    field_1: str
    field_2: int
    field_3: str

    def __init__(self, **kwargs):
        for arg in kwargs:
            setattr(self, arg, kwargs[arg])
        self.field_4 = f"{self.field_1}_{self.field_2}"


merged = typed_merge(
    Test,
    Test(field_1=11, field_2=22, field_3=33),
    dict(field_1=1, field_2=2),
    init=True,
)


logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

loader = YamlConfigLoader()

yaml.SafeLoader.add_constructor("!load", loader)


hieraconf = HieraConfigLoader(
    interpolate=True,
).load(
    """#!test.yaml
pathname:
  stem: root
""",
    "examples/hiera.yaml",
)

config = yaml.safe_load(
    "test: !load {pathname: examples/includeme.yaml, transform: '{ pathname.name: value.include }', key_factory: '%pathname.as_posix()', type: map }"
)

jinjaconfig = loader.load("examples/jinja.yaml.j2")
pyproject = loader.load("pyproject.toml")


a = MergeMethod(1)
c = MergeMethod("SIMPLE")

l = LogLevel("critical")


pass
