from setuptools import setup


def get_version():
    version = {}
    with open("ffac/version.py") as f:
        exec(f.read(), version)
    return version["__version__"]


long_description = """**ffac** is a wrapper around **ffmpeg** for batch audio conversion.

Project home on gitlab: https://gitlab.com/peczony/ffac
"""


setup(
    name="ffac",
    version=get_version(),
    author="Alexander Pecheny",
    author_email="peczony@gmail.com",
    description="Wrapper around ffmpeg for batch audio conversion",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://gitlab.com/peczony/ffac",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    packages=["ffac"],
    entry_points={"console_scripts": ["ffac = ffac.__main__:main"]},
    install_requires=["tqdm"],
)
