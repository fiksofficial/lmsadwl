from setuptools import setup, find_packages

setup(
    name="lmsadwl",
    version="1.0.3",
    description="Lenovo LMSA ROM Downloader — download firmware from official Lenovo servers",
    author="lmsa project",
    python_requires=">=3.8",
    packages=find_packages(),
    install_requires=[
        "requests>=2.25.0",
    ],
    entry_points={
        "console_scripts": [
            "lmsadwl=lmsadwl.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Topic :: System :: Hardware",
        "Topic :: Utilities",
    ],
)
