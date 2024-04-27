from enum import IntEnum


class IntEnum(IntEnum):
    @classmethod
    def _missing_(cls, value: object):
        if not isinstance(value, int):
            name = str(value).lower()
            for member in cls:
                if member.name.lower() == name:
                    return member
        super()._missing_(value)
