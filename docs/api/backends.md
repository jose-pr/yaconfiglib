# Backends

The base contract every backend implements, followed by each built-in
format backend.

## Base contract

::: yaconfiglib.backends.base.ConfigBackend

## YAML

::: yaconfiglib.backends.yaml.YamlConfig

## TOML

::: yaconfiglib.backends.toml.TomlConfig

## JSON

::: yaconfiglib.backends.json.JsonConfig

## INI

::: yaconfiglib.backends.ini.IniConfig

## dotenv

::: yaconfiglib.backends.dotenv.DotenvBackend

## Environment variables

::: yaconfiglib.backends.env.EnvVarBackend

## Commands and scripts

::: yaconfiglib.backends.command.CommandBackend

## In-memory Python objects

::: yaconfiglib.backends.python_backend.PythonBackend

## Jinja2-templated sources

::: yaconfiglib.backends.jinja2.Jinja2ConfigLoader
