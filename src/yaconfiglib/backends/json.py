import json
import re

from pathlib_next import Path

from yaconfiglib.backends.base import ConfigBackend

__all__ = ["JsonConfig"]


class JsonConfig(ConfigBackend):
    PATHNAME_REGEX = re.compile(r".*\.json$", re.IGNORECASE)

    def load(
        self,
        path: Path,
        encoding: str = None,
        json_decoder_options=None,
        **options,
    ) -> object:
        encoding = encoding or self.DEFAULT_ENCODING

        return json.loads(
            path.read_text(encoding=encoding), **(json_decoder_options or {})
        )

    def dumps(self, data: str, **options) -> str:
        return json.dumps(data, **options)
