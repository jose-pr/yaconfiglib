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

Only keys matching the dataclass's declared fields are passed to the
constructor — extra keys in the loaded document are silently ignored
rather than raising a `TypeError`.

## Plain classes

If *model_cls* is neither a Pydantic model nor a dataclass, `load_as`
falls back to calling `model_cls(**data)` directly, so any class whose
`__init__` accepts the loaded document's keys as keyword arguments works
without special integration.

## Combining with other loader options

`load_as` accepts every keyword `load()` does — merging, interpolation,
and multiple sources all work the same way:

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
