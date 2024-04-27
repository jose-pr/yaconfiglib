from yaconfiglib.yaml.include import Include
from yaconfiglib.yaml.reader import Reader
from yaconfiglib.jinja2 import Jinja2Reader
import yaml
import logging
import sys

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

include = Include()

yaml.SafeLoader.add_constructor('!include', include)



config = yaml.safe_load("test: !include {pathname: examples/includeme.yaml, transform: '{ pathname.name: value.include }' }")

jinjaconfig = yaml.safe_load("test: !include examples/jinja.yaml.j2")

pass
