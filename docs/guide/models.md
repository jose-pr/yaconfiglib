# Model hydration

`load_as()` loads configuration exactly like `load()`, then hydrates the
result into a typed model instead of returning a loose dict — useful when
you want validation, defaults, and IDE autocomplete instead of dot-access
on an untyped mapping.

## Pydantic

```python
from pydantic import BaseModel
import yaconfiglib

class DBConfig(BaseModel):
    host: str
    port: int

db_settings = yaconfiglib.load_as(DBConfig, "config.yaml", loader="yaml")
print(db_settings.host)
```

Both Pydantic v1 (`.parse_obj`) and v2 (`.model_validate`) are supported
automatically — `load_as` detects which API the model class exposes.
Pydantic is a strictly optional dependency; it's only imported when
`load_as` is actually called with a Pydantic model.

## Dataclasses

```python
from dataclasses import dataclass
import yaconfiglib

@dataclass
class DBConfig:
    host: str
    port: int = 5432

db_settings = yaconfiglib.load_as(DBConfig, "config.yaml")
```

Only keys the dataclass's own signature accepts are passed to the
constructor — extra keys in the loaded document are silently ignored
rather than raising a `TypeError`. `InitVar` parameters are passed;
`field(init=False)` fields are not, and neither is a document key named
`self`.

A field annotated with another dataclass (or a Pydantic model), including
`Optional[...]` of one, is built as an instance rather than left as a
plain dict:

```python
@dataclass
class DB:
    host: str

@dataclass
class App:
    name: str
    db: DB          # -> App(name=..., db=DB(host=...))
```

Containers of models (`List[DB]`, `Dict[str, DB]`) stay as loaded.

## Plain classes

If *model_cls* is neither a Pydantic model nor a dataclass, `load_as`
falls back to calling `model_cls(**data)` directly, so any class whose
`__init__` accepts the loaded document's keys as keyword arguments works
without special integration.

## Combining with other loader options

`yaconfiglib.load_as` accepts every keyword `yaconfiglib.load` does, and
takes several sources. As with `load()`, a keyword `ConfigLoader` accepts
configures the loader (so it reaches nested includes too) and anything else
goes to the backend. `ConfigLoader(...).load_as(...)` is the method form:
there, constructor options belong on the constructor.

```python
settings = yaconfiglib.load_as(
    AppConfig,
    "base.yaml", "production.yaml",
    merge="deep",
    interpolate=True,
)
```

The loaded document must be a mapping (`dict`) — `load_as` raises
`TypeError` if the merged/interpolated result isn't one.
