#!/usr/bin/env python3
# type: ignore
"""Generate Python code from Protobuf definitions."""

import subprocess
import sys
from pathlib import Path


def main():
    """Generate Python code from .proto files."""
    proto_dir = Path("proto")
    output_dir = Path("xcp/generated")

    # Create output directory
    output_dir.mkdir(exist_ok=True)

    # Generate Python code for each .proto file
    for proto_file in proto_dir.glob("*.proto"):
        print(f"Generating Python code for {proto_file}")

        try:
            subprocess.run(
                ["protoc", f"--python_out={output_dir}", f"--proto_path={proto_dir}", str(proto_file)], check=True
            )
            print(f"✓ Generated {proto_file.stem}_pb2.py")
        except subprocess.CalledProcessError as e:
            print(f"✗ Failed to generate {proto_file}: {e}")
            sys.exit(1)
        except FileNotFoundError:
            print("✗ protoc compiler not found. Please install Protocol Buffers compiler.")
            sys.exit(1)


if __name__ == "__main__":
    main()
