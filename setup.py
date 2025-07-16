#!/usr/bin/env python3
"""Setup script for the XCP package."""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="xcp",
    version="0.1.0",
    author="Maida.AI",
    description="eXtensible Coordination Protocol - A binary-first communication protocol for AI agents",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/maida-ai/xcp",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Communications",
        "Topic :: Internet :: Protocol",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    python_requires=">=3.8",
    install_requires=requirements,
    extras_require={
        "dev": [
            "pytest>=6.0",
            "black>=21.0",
            "flake8>=3.8",
            "mypy>=0.800",
        ],
        "benchmark": [
            "httpx>=0.24.0",
            "h2>=4.0.0",
            "tqdm>=4.64.0",
            "numpy>=1.21.0",
            "rich>=12.0.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "xcp-demo=xcp.demo:main",
        ],
    },
)
