# yaconfiglib
Another Configuration Parser library for python.

Goal was to not worry about the format and being able to load and merge multiple configs in different formats.

Currently implemented backends for toml, yaml as a starting point, really easy to add more.

Wanted the option to generate a config with jinja and then load the config from the generate template.

Wanted a function like hiera in puppet to reference values already declared in the document, and expanded it to support basic functions allowed in a single jinja {%do %} statement. While preserving the type of the produced value. 

Wanted the ability to merge multiple configs into one with user configurable methods.

Wanted to load files from any arbitrary path that didnt need to be local filesystem path, but liked the api of pathlib.Path so ended up also writing another package: pathlib_next to extend pathlib to work with custom Path implementation like URI/SFTP/HTTP etc. WIP

# Based on the following libraries
Heavily modified but based on the work of the following libraries. I was using them to load configs but had to modify them to fit my needs and ended up writing my own.
* v1 of yamlinclude: https://github.com/tanbro/pyyaml-include 
* hiyapyco for chain loading/merge template support: https://github.com/zerwes/hiyapyco


