from yaconfiglib.yaml.include import Include
from yaconfiglib.yaml.reader import Reader
import yaml
import logging
import sys

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

include = Include()

yaml.SafeLoader.add_constructor('!include', include)



config = yaml.safe_load("test: !include {pathname: examples/includeme.yaml, transform: value.include }")

pass
