import yaml
from yaconfiglib.loader import ConfigLoader
from yaconfiglib.loader import ConfigLoaderMergeMethod as MergeMethod

# Initialize the loader. !include / !load are registered automatically on the
# first YAML load; no manual yaml.add_constructor call is needed.
loader = ConfigLoader()

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
