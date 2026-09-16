import logging
import re
import typing as _ty
from configparser import (
    BasicInterpolation,
    ConfigParser,
    ExtendedInterpolation,
    Interpolation,
)

try:
    from pathlib_next import Path
except ImportError:
    from pathlib import Path

from yaconfiglib.backends.base import ConfigBackend

__all__ = ["IniConfig"]

logger = logging.getLogger(__name__)

#: "Use the instance's value" — `None` is itself a valid interpolation choice,
#: so it cannot double as the per-call default.
_UNSET = object()


def _interpolation(choice: object) -> _ty.Optional[Interpolation]:
    """Turn an *interpolation* option into a configparser interpolation."""
    if choice is None or isinstance(choice, Interpolation):
        return choice
    if isinstance(choice, str):
        name = choice.lower()
        if name == "basic":
            return BasicInterpolation()
        if name == "extended":
            return ExtendedInterpolation()
        if name == "none":
            return None
    raise ValueError(
        f"invalid interpolation {choice!r}: expected 'basic', 'extended', 'none', "
        "None, or a configparser.Interpolation instance"
    )


class IniConfig(ConfigBackend):
    """Backend for ``*.ini``/``*.cfg`` files, parsed via the standard library's :class:`configparser.ConfigParser`.

    Returns a ``{section_name: {key: value}}`` mapping. All values are
    strings, matching :mod:`configparser` semantics — use interpolation
    (:func:`yaconfiglib.utils.jinja2.interpolate`) or manual coercion if
    typed values are needed.

    ``%`` interpolation is on by default, as configparser does it, so
    ``%(name)s`` references resolve and a literal percent is written ``%%``.
    Pass ``interpolation=None`` (or the per-call ``ini_interpolation=None``) to
    read such values verbatim — that is what a logging or alembic formatter
    line needs, since ``%(levelname)-5.5s`` is not a config reference.

    Keys in ``[DEFAULT]`` are inherited by every section and are not returned
    as a section of their own; a file that has nothing else logs a warning
    naming the escape hatch.
    """

    PATHNAME_REGEX = re.compile(r".*\.(ini|cfg)$", re.IGNORECASE)
    DEFAULT_SECTION = "DEFAULT"

    def __init__(self, interpolation: object = "basic") -> None:
        self.interpolation = interpolation

    def load(
        self,
        path: "_ty.Union[Path, str]",
        encoding: "_ty.Optional[str]" = None,
        path_factory: "_ty.Optional[_ty.Callable[[str], Path]]" = None,
        ini_default_section: "_ty.Optional[str]" = None,
        ini_interpolation: "_ty.Any" = _UNSET,
        **options: object,
    ) -> object:
        """Parse *path* as INI and return a ``{section: {key: value}}`` dict.

        Args:
            path: File to parse, either a ``Path`` or a string (converted
                via *path_factory*).
            encoding: Text encoding, defaults to :attr:`DEFAULT_ENCODING`.
            path_factory: Path constructor used when *path* is a string.
            ini_default_section: Section name used for
                :class:`~configparser.ConfigParser`'s ``default_section``,
                defaults to :attr:`DEFAULT_SECTION`. Naming a section the
                file does not use turns ``[DEFAULT]`` into an ordinary
                section instead of inherited defaults.
            ini_interpolation: Overrides the instance's *interpolation* for
                this call: ``"basic"``, ``"extended"``, ``"none"``/``None``,
                or a :class:`configparser.Interpolation` instance.

        Raises:
            ValueError: If *ini_interpolation* is not one of those.
        """
        path = self._coerce_path(path, path_factory)
        choice = (
            self.interpolation if ini_interpolation is _UNSET else ini_interpolation
        )

        parser = ConfigParser(
            default_section=ini_default_section or self.DEFAULT_SECTION,
            interpolation=_interpolation(choice),
        )
        # The full path, so two app.ini files in a glob are
        # distinguishable; as_posix() because configparser formats
        # its source with %r, which doubles Windows backslashes.
        parser.read_string(self._read_text(path, encoding), path.as_posix())
        result = {}
        for section in parser.sections():
            d = result[section] = {}
            section_obj = parser[section]
            for key in section_obj:
                d[key] = section_obj[key]
        if parser.defaults() and not result:
            # Defaults are inherited by sections rather than returned, so a
            # file with nothing but [DEFAULT] loads as {} — which looks like an
            # empty file.
            logger.warning(
                "%s: only the %r section is present, and its keys are inherited "
                "by other sections rather than returned; pass "
                'ini_default_section="<a name the file does not use>" to load '
                "them as a section",
                path.name,
                parser.default_section,
            )
        return result
