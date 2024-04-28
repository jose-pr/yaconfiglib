import logging
import sys
from dataclasses import dataclass

import yaml

from yaconfiglib import YamlConfig, hiera
from yaconfiglib.hiera import LogLevel, MergeMethod
from yaconfiglib.loader import ConfigLoader
from yaconfiglib.utils.merge import typed_merge


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

loader = YamlConfig()

yaml.SafeLoader.add_constructor("!load", loader)


hieraconf = hiera.load(
    """#!test.yaml
pathname:
  stem: root
""",
    "examples/hiera.yaml",
    interpolate=True,
)
print(yaml.dump(hieraconf, indent=2))

config = yaml.safe_load(
    "test: !load {pathname: examples/includeme.yaml, transform: '{ pathname.name: value.include }', key_factory: '%pathname.as_posix()', type: map }"
)
print(yaml.dump(config, indent=2))

configloader = ConfigLoader()

jinjaconfig = loader.load("examples/jinja.yaml.j2", configloader=configloader)
print(yaml.dump(jinjaconfig, indent=2))

pyproject = configloader.load("pyproject.toml")
print(yaml.dump(pyproject, indent=2))


a = MergeMethod(1)
c = MergeMethod("SIMPLE")

l = LogLevel("critical")


pass
