import yaml
from yaconfiglib.loader import ConfigLoader
from yaconfiglib.loader import ConfigLoaderMergeMethod as MergeMethod

# Initialize the loader
loader = ConfigLoader()

# Register the loader as the handler for the !include and !load tags in PyYAML
yaml.SafeLoader.add_constructor("!include", loader)
yaml.SafeLoader.add_constructor("!load", loader)

def main():
    print("=== Loading Advanced YAML with Interpolation & !include ===")
    
    # Load the advanced configuration file, enabling Jinja interpolation 
    # and deep merging of any nested includes.
    config = loader.load(
        "examples/advanced.yaml",
        interpolate=True,
        merge=MergeMethod.Deep
    )
    
    # Print the resolved configuration
    print(yaml.dump(config, indent=2))

if __name__ == "__main__":
    main()
