import typing as _ty

from jinja2 import Environment, Template

DEFAULT_ENV = Environment(extensions=["jinja2.ext.do"])


def load_template(
    source: str,
    name: str = None,
    filename: str = None,
    environment: Environment = None,
    globals: _ty.MutableMapping = None,
):
    environment = environment or DEFAULT_ENV
    code = environment.compile(source, name, filename)
    return Template.from_code(environment, code, environment.make_globals(globals))


def compile(
    code: str, environment: Environment = None, globals: _ty.MutableMapping = None
):
    return load_template(code, environment=environment, globals=globals).render


def eval(
    code: str,
    environment: Environment = None,
    globals: _ty.MutableMapping = None,
):
    template = load_template(
        "{% do _meta.__setitem__('result', " + code + ") %}",
        environment=environment,
        globals=globals,
    )

    def eval_(**kwargs):
        _meta = {}
        template.render(_meta=_meta, **kwargs)
        return _meta["result"]

    return eval_
