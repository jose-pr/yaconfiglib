import logging
import sys

import yaml

from yaconfiglib.jinja2 import Jinja2Reader
from yaconfiglib.yaml import *

logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

include = Include()

yaml.SafeLoader.add_constructor('!include', include)



config = yaml.safe_load("test: !include {pathname: examples/includeme.yaml, transform: '{ pathname.name: value.include }' }")

jinjaconfig = yaml.safe_load("test: !include examples/jinja.yaml.j2")

pass
