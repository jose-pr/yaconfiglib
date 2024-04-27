import io
from io import IOBase

from jinja2 import Environment, Template
from pathlib_next import Path, PosixPathname, UriPath
from pathlib_next.utils.stat import FileStat, FileStatLike


class MemPathBackend(dict): ...


class MemBytesIO(io.BytesIO):
    def __init__(self, initial_bytes: bytearray) -> None:
        self._bytes = initial_bytes
        super().__init__(initial_bytes)

    def close(self) -> None:
        self.seek(0)
        self._bytes.clear()
        self._bytes.extend(self.read())
        return super().close()


_BACKEND = MemPathBackend()


class MemPath(UriPath):

    __SCHEMES = ("memview",)
    backend: MemPathBackend

    def _initbackend(self):
        return _BACKEND

    def _parent_container(self) -> tuple[dict[str, bytearray], str]:
        parent = self.backend
        *ancestors, name = (
            self.normalized_path.removeprefix(".").removeprefix("/").split("/")
        )
        for path in ancestors:
            if path not in parent:
                raise FileNotFoundError(self.parent)
            else:
                parent = parent[path]

        return parent, name

    def stat(self, *, follow_symlinks=True) -> FileStatLike:
        parent, name = self._parent_container()
        if name not in parent:
            return FileNotFoundError(self)

        return FileStat(is_dir=isinstance(parent[name], dict))

    def _listdir(self):
        parent, name = self._parent_container()
        content = parent.get(name) if name else parent
        if not isinstance(content, dict):
            raise NotADirectoryError(self)
        for c in content:
            yield c

    def _open(self, mode="r", buffering=-1) -> IOBase:
        parent, name = self._parent_container()
        if "w" in mode:
            content = parent.setdefault(name, bytearray())
            return MemBytesIO(content)
        elif name not in parent:
            return FileNotFoundError(self)
        else:
            content = parent[name]

        return io.BytesIO(content)
