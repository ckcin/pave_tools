#!/usr/bin/env python3
"""
check_env.py
Utility script to verify that the PAVE pipeline's environment has all required dependencies.
"""

import sys
import importlib.util

REQUIRED_LIBS = {
    "numpy": "numpy",
    "pandas": "pandas",
    "xarray": "xarray",
    "matplotlib": "matplotlib",
    "scipy": "scipy",
    "cartopy": "cartopy",
    "sunpy": "sunpy",
    "netCDF4": "netCDF4",
    "boto3": "boto3"
}

def main():
    print("==========================================")
    print(" PAVE Pipeline: Environment Check utility ")
    print("==========================================")
    
    all_passed = True
    missing_libs = []

    for label, module_name in REQUIRED_LIBS.items():
        spec = importlib.util.find_spec(module_name)
        if spec is None:
            print(f"[FAIL] {label.ljust(12)} -> MISSING")
            missing_libs.append(label)
            all_passed = False
        else:
            print(f"[ OK ] {label.ljust(12)} -> Installed")

    print("==========================================")
    
    if all_passed:
        print("Success! Your python environment has all required libraries to run PAVE.")
        sys.exit(0)
    else:
        print("WARNING: You are missing required libraries!")
        print("Please install them using pip or conda:")
        print(f"   pip install {' '.join(missing_libs)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
