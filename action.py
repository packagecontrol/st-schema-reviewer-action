#!/usr/bin/env python3

"""Tests for the validity of the channel and repository files.

Arguments:
    --channel=channel.json
        Channel filename to test

    --repository=repository.json
        Repository filename to test

    --test-repositories
        Also generates tests for all repositories in `channel.json` (the http
        ones).
"""

import argparse
import os
import re
import json
import sys
import unittest

from collections import defaultdict
from functools import wraps
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import unquote_to_bytes
from urllib.parse import urljoin

# known bad repositories that we'll just have to skip for now
BAD_REPOS = [
    "https://raw.githubusercontent.com/blake-regalia/linked-data.syntaxes/master/channels/sublime/package-control.json",
]

generator_method_type = "method"

parser = argparse.ArgumentParser()
parser.add_argument("--channel", default="channel.json")
parser.add_argument("--repository", default="repository.json")
parser.add_argument("--test-repositories", action="store_true", default=False)
userargs, unittesting_args = parser.parse_known_args()
sys.argv = sys.argv[:1] + unittesting_args

################################################################################
# Utilities


def generate_test_methods(cls, stream=sys.stdout):
    """Class decorator for classes that use test generating methods.

    A class that is decorated with this function will be searched for methods
    starting with "generate_" (similar to "test_") and then run like a nosetest
    generator.
    Note: The generator function must be a classmethod!

    If a "pre_generate" classmethod exists, it will be run before the generator
    functions.

    Generate tests using the following statement:
        yield method, (arg1, arg2, arg3)  # ...
    """
    attributes = list(cls.__dict__.keys())
    if "pre_generate" in attributes:
        func = getattr(cls, "pre_generate")
        if not func.__class__.__name__ == generator_method_type:
            raise TypeError("Pre-Generator method must be classmethod")

        func()

    for name in list(cls.__dict__.keys()):
        generator = getattr(cls, name)
        if not name.startswith("generate_") or not callable(generator):
            continue

        if not generator.__class__.__name__ == generator_method_type:
            raise TypeError("Generator methods must be classmethods")

        # Create new methods for each `yield`
        for sub_call in generator(stream):
            method, params = sub_call

            @wraps(method)
            def wrapper(self, method=method, params=params):
                return method(self, *params)

            # Do not attempt to print lists/dicts with printed length of 1000 or
            # more, they are not interesting for us (probably the whole file)
            args = []
            for param in params:
                string = repr(param)
                if len(string) > 1000:
                    args.append("...")
                else:
                    args.append(string)

            mname = method.__name__
            if mname.startswith("_test"):
                mname = mname[1:]
            elif not mname.startswith("test_"):
                mname = "test_" + mname

            # Include parameters in attribute name
            name = f"{mname}({', '.join(args)})"
            setattr(cls, name, wrapper)

        # Remove the generator afterwards. It did its work.
        delattr(cls, name)

    return cls


# Very limited subclassing of dict class, which just suits our needs
class CaseInsensitiveDict(dict):
    @classmethod
    def _k(cls, key):
        return key.lower() if isinstance(key, str) else key

    def __getitem__(self, key):
        return super().__getitem__(self._k(key))

    def __setitem__(self, key, value):
        super().__setitem__(self._k(key), value)

    def __contains__(self, key):
        return super().__contains__(self._k(key))


def get_package_name(data):
    """Get "name" from a package with a workaround when it's not defined.

    Use the last part of details url for the package's name otherwise since
    packages must define one of these two keys anyway.
    """
    return data.get("name") or data.get("details").strip("/").rsplit("/", 1)[-1]


def from_uri(uri: str) -> str:  # roughly taken from Python 3.13
    """Return a new path from the given 'file' URI."""
    if not uri.lower().startswith("file:"):
        raise ValueError(f"URI does not start with 'file:': {uri!r}")
    path = os.fsdecode(unquote_to_bytes(uri))
    path = path[5:]
    if path[:3] == "///":
        # Remove empty authority
        path = path[2:]
    elif path[:12].lower() == "//localhost/":
        # Remove 'localhost' authority
        path = path[11:]
    if path[:3] == "///" or (path[:1] == "/" and path[2:3] in ":|"):
        # Remove slash before DOS device/UNC path
        path = path[1:]
        path = path[0].upper() + path[1:]
    if path[1:2] == "|":
        # Replace bar with colon in DOS drive
        path = path[:1] + ":" + path[2:]
    if not os.path.isabs(path):
        raise ValueError(f"URI is not absolute: {uri!r}. Parsed so far: {path!r}")
    return path


################################################################################
# Tests


class TestContainer:
    """Contains tests that the generators can easily access (when subclassing).

    Does not contain tests itself, must be used as mixin with unittest.TestCase.
    """

    seen_repositories = set()
    skipped_repositories = defaultdict(int)

    package_names = CaseInsensitiveDict()
    library_names = CaseInsensitiveDict()
    # tuple of (prev_name, include, name); prev_name for case sensitivity
    previous_package_names = CaseInsensitiveDict()

    # Default packages for ST2 and ST3 are largely the same,
    # except for Pascal and Rust
    # which only ship in ST3
    default_packages = (
        "ActionScript",
        "AppleScript",
        "ASP",
        "Batch File",
        "Binary",
        "C#",
        "C++",
        "Clojure",
        "Color Scheme - Default",
        "CSS",
        "D",
        "Default",
        "Diff",
        "Erlang",
        "Git Formats",
        "Go",
        "Graphviz",
        "Groovy",
        "Haskell",
        "HTML",
        "Java",
        "JavaScript",
        "Language - English",
        "LaTeX",
        "Lisp",
        "Lua",
        "Makefile",
        "Markdown",
        "Matlab",
        "Objective-C",
        "OCaml",
        "Pascal",
        "Perl",
        "PHP",
        "Python",
        "R",
        "Rails",
        "Regular Expressions",
        "RestructuredText",
        "Ruby",
        "Rust",
        "Scala",
        "ShellScript",
        "SQL",
        "TCL",
        # "TOML",  # Package existed before the default one was added.
        "Text",
        "Textile",
        "Theme - Default",
        "Vintage",
        "XML",
        "YAML",
    )

    pkg_d_reg = r"""^ ( https:// bitbucket\.org/ [^/#?]+/ [^/#?]+
                        ( /src/ [^#?]*[^/#?] | \#tags | / )?
                      | https:// codeberg\.org/ [^/#?]+/ [^/#?]+
                        ( /src/ [^#?]*[^/#?] | \#tags | / )?
                      | https:// github\.com/ [^/#?]+/ [^/#?]+
                        (?<!\.git) ( /tree/ [^#?]*[^/#?] | / )?
                      | https:// gitlab\.com/ [^/#?]+/ [^/#?]+
                        (?<!\.git) ( /-/tree/ [^#?]*[^/#?] | / )?
                      ) $"""
    pkg_d_reg = " ".join(map(str.strip, pkg_d_reg.split()))
    package_details_regex = re.compile(pkg_d_reg, re.X)

    rel_b_reg = r"""^ ( https:// bitbucket\.org / [^/#?]+ / [^/#?]+
                      | https:// codeberg\.org / [^/#?]+ / [^/#?]+
                      | https:// github\.com / [^/#?]+ / [^/#?]+
                      | https:// gitlab\.com / [^/#?]+ / [^/#?]+
                      | https:// pypi\.org / project / [^/#?]+ (?: / [^/#?]+ )?
                      ) $"""
    # Strip multilines for better debug info on failures
    rel_b_reg = " ".join(map(str.strip, rel_b_reg.split()))
    release_base_regex = re.compile(rel_b_reg, re.X)

    lib_key_types_map = {
        "name": str,
        "author": (str, list),
        "description": str,
        "issues": str,
        "releases": list,
    }

    pkg_key_types_map = {
        "name": str,
        "details": str,
        "description": str,
        "releases": list,
        "homepage": str,
        "author": (str, list),
        "readme": str,
        "issues": str,
        "donate": (str, type(None)),
        "buy": str,
        "previous_names": list,
        "labels": list,
    }

    lib_release_key_types_map = {
        "base": str,
        "asset": str,
        "tags": (bool, str),
        "branch": str,
        "sublime_text": str,
        "platforms": (list, str),
        "python_versions": (list, str),
        "date": str,
        "version": str,
        "sha256": str,
        "url": str,
    }

    pkg_release_key_types_map = {
        "base": str,
        "asset": str,
        "tags": (bool, str),
        "branch": str,
        "sublime_text": str,
        "platforms": (list, str),
        "python_versions": (list, str),
        "libraries": (list, str),
        "date": str,
        "version": str,
        "sha256": str,
        "url": str,
    }

    def _test_indentation(self, filename, contents):
        for i, line in enumerate(contents.splitlines()):
            self.assertRegex(line, r"^\t*\S", f"Indent must be tabs in line {i + 1}")

    def _test_channel_keys(self, filename, data):
        self.assertIn("schema_version", data)
        self.assertEqual(data["schema_version"], "4.0.0")

        allowed_keys = ("$schema", "schema_version", "repositories")
        for key in data:
            self.assertIn(key, allowed_keys, f"Unexpected key {key} found!")

        self.assertIn("repositories", data)
        repos = data["repositories"]
        self.assertIsInstance(repos, list)
        self.assertGreater(len(repos), 0, "Channel must contain at least one repository")

        for repo in repos:
            self.assertIsInstance(repo, str)
            self.assertRegex(
                repo,
                r"^(\./|(?:file|https)://)",
                "Repositories must be relative urls or use the FILE or HTTPS protocol",
            )

        self.assertEqual(
            repos,
            sorted(repos, key=str.lower),
            "Repositories must be sorted alphabetically",
        )

    def _test_repository_keys(self, filename, data):
        self.assertIn("schema_version", data)
        self.assertEqual(data["schema_version"], "4.0.0")

        allowed_keys = ("$schema", "schema_version", "packages", "libraries", "includes")
        for key in data:
            self.assertIn(key, allowed_keys, f"Unexpected key {key} found!")

        list_keys = [key for key in ("packages", "libraries", "includes") if key in data]
        self.assertGreater(
            len(list_keys), 0, 'Repositories must contain at least one of "packages", "libraries" or "includes".'
        )
        for key in list_keys:
            self.assertIsInstance(data[key], list)

        includes = data.get("includes", [])
        for include in includes:
            self.assertIsInstance(include, str)
            self.assertRegex(
                include,
                r"^(\./|(?:file|https)://)",
                "Repositories must be relative urls or use the FILE or HTTPS protocol",
            )

        self.assertEqual(
            includes,
            sorted(includes, key=str.lower),
            "Includes must be sorted alphabetically",
        )

    def _test_library_names(self, filename, data):
        match = re.search(r"(?:^|[\\/])[0-9a-z_-]+\.json$", filename)
        if not match:
            self.fail("Include filename does not match")

        repo_library_names = []
        for pdata in data["libraries"]:
            name = pdata["name"]
            if name in self.library_names:
                self.fail("Library names must be unique: " + name)
            else:
                self.library_names[name] = filename
                repo_library_names.append(name)

            # Check case sensitive match between package and library.
            # Python import machinary is case-sensitive, thus differently cased
            # packages and libraries are not causing issues.
            package_name = self.package_names.get(name)
            if package_name == name:
                self.fail(f"Library and package names must be unique: {name}, previously occurred in {package_name})")

        # Check package order
        self.assertEqual(
            repo_library_names,
            sorted(repo_library_names, key=str.lower),
            "Libraries must be sorted alphabetically",
        )

    def _test_package_names(self, filename, data):
        match = re.search(r"(?:^|[\\/])([0-9a-z_-]+)\.json$", filename)
        if not match:
            self.fail("Include filename does not match")
            fname = ""
        else:
            fname = match.group(1)

        repo_package_names = []
        # Collect package names and check if they are unique,
        # including occurrences in previous_names.
        for pdata in data["packages"]:
            pname = get_package_name(pdata)
            if pname in self.package_names:
                self.fail(f"Package names must be unique: {pname}, previously occurred in {self.package_names[pname]}")
            elif (
                pname in self.previous_package_names
                # check casing
                and pname == self.previous_package_names[pname][0]
            ):
                print(pname, self.previous_package_names[pname][0])
                self.fail(
                    f"Package names can not occur as a name and as a previous_name: {pname},"
                    " previously occurred as previous_name in "
                    f"{self.previous_package_names[pname][1]}: {self.previous_package_names[pname][2]}"
                )

            # Check case sensitive match between package and library.
            # Python import machinary is case-sensitive, thus differently cased
            # packages and libraries are not causing issues.
            elif (lib_name := self.library_names.get(pname)) == pname:
                self.fail(f"Library and package names must be unique: {pname}, previously occurred in {lib_name}")
            else:
                self.package_names[pname] = filename
                repo_package_names.append(pname)

        # Check if in the correct file.
        # Primarily targets default channel's repository, which names files 0-9.json, a.json, b.json, etc.
        if fname == "0-9":
            for package_name in repo_package_names:
                self.assertTrue(package_name[0].isdigit(), "Package inserted in wrong file")
        elif len(fname) == 1:
            for package_name in repo_package_names:
                self.assertEqual(package_name[0].lower(), fname[0].lower(), "Package inserted in wrong file")

        # Check package order
        self.assertEqual(
            repo_package_names,
            sorted(repo_package_names, key=str.lower),
            "Packages must be sorted alphabetically (by name)",
        )

    def _test_library_keys(self, filename, data):
        for key, value in data.items():
            self._check_key_value_types(key, value, self.lib_key_types_map)
            match key:
                case "issues":
                    self.assertRegex(value, r"^https://")
                case "name":
                    # Test for invalid characters (on file systems)
                    # Invalid on Windows (and sometimes problematic on UNIX)
                    self.assertNotRegex(value, r'[/?<>\\:*|"\x00-\x19]')
                    self.assertFalse(value.startswith("."))

        for key in self.lib_key_types_map:
            self.assertIn(key, data, f"{key!r} is required for libraries")

    def _test_package_keys(self, filename, data):
        name = get_package_name(data)

        for key, value in data.items():
            self._check_key_value_types(key, value, self.pkg_key_types_map)

            match key:
                case "details":
                    self.assertRegex(
                        value,
                        self.package_details_regex,
                        "The details url is badly formatted or invalid",
                    )

                case "donate":
                    if value is not None:
                        # Allow "removing" the donate url that is added by "details"
                        self.assertRegex(value, r"^https://")

                case "labels":
                    for label in value:
                        self.assertNotIn(
                            ",",
                            label,
                            "Multiple labels should not be in the same string",
                        )

                    self.assertCountEqual(
                        value,
                        set(value),
                        "Specifying the same label multiple times is redundant",
                    )

                case "previous_names":
                    # Test if name is unique, against names and previous_names.
                    for prev_name in value:
                        if prev_name in self.previous_package_names:
                            self.fail(
                                f"Previous package names must be unique: {prev_name}, "
                                f"previously occurred in {self.previous_package_names[prev_name]}"
                            )
                        elif prev_name in self.package_names:
                            self.fail(
                                f"Package names can not occur as a name and as a previous_name: {prev_name}, "
                                f"previously occurred as name in {self.package_names[prev_name]}"
                            )
                        else:
                            self.previous_package_names[prev_name] = (prev_name, filename, name)

                case ("homepage", "readme", "issues", "buy"):
                    self.assertRegex(value, r"^https://")

        # Test for invalid characters (on file systems)
        # Invalid on Windows (and sometimes problematic on UNIX)
        self.assertNotRegex(
            name,
            r'[/?<>\\:*|"\x00-\x19]',
            "Package names must be valid folder names on all operating systems",
        )
        # Invalid on OS X (or more precisely: hidden)
        self.assertFalse(name.startswith("."), "Package names may not start with a dot")

        self.assertNotIn(name, self.default_packages)

        if "details" not in data:
            for key in ("name", "homepage", "author", "releases"):
                self.assertIn(key, data, f'{key!r} is required if no "details" URL provided')

    def _test_library_release(self, package_name, data, only_templates=True):
        if only_templates:
            self.assertTrue(
                "base" in data and ("asset" in data or "tags" in data or "branch" in data),
                'A release must have an "asset", "tags" or "branch" key '
                "if it is in the main repository. For custom "
                "releases, a custom repository.json file must be "
                "hosted elsewhere. The only exception to this rule "
                "is for packages that can not be served over HTTPS "
                "since they help bootstrap proper secure HTTP "
                "support for Sublime Text.",
            )
            for key in ("url", "version", "date", "sha256"):
                self.assertNotIn(
                    key,
                    data,
                    "The version, date and url keys should not be "
                    "used in the main repository since a pull "
                    "request would be necessary for every release",
                )

        elif "asset" not in data and "tags" not in data and "branch" not in data:
            # we don't care for date of library releases as it is not displayed anywhere,
            # but want sha256 hash to be present for security reasons
            for key in ("url", "version", "sha256"):
                self.assertIn(
                    key,
                    data,
                    'A release must provide "url", "sha256" and "version" keys'
                    ' if it does not specify "asset", "tags" or "branch".',
                )

        else:
            for key in ("url", "version", "date", "sha256"):
                self.assertNotIn(
                    key,
                    data,
                    f'The key "{key}" is redundant when "asset", "tags" or "branch" is specified',
                )

        self.assertFalse(
            (("asset" in data or "tags" in data) and "branch" in data),
            'A release must have only one of the "tags" or "branch" keys.',
        )

        for key in ("python_versions",):
            self.assertIn(key, data, f"{key!r} is required")

        # Test keys values
        self._check_release_key_values(data, True)

    def _test_package_release(self, package_name, data, only_templates=True):
        if only_templates:
            self.assertTrue(
                ("asset" in data or "tags" in data or "branch" in data),
                'A release must have an "asset", "tags" or "branch" key '
                "if it is in the main repository. For custom "
                "releases, a custom repository.json file must be "
                "hosted elsewhere.",
            )
            for key in ("url", "version", "date", "sha256"):
                self.assertNotIn(
                    key,
                    data,
                    "The version, date and url keys should not be "
                    "used in the main repository since a pull "
                    "request would be necessary for every release",
                )

        elif "asset" not in data and "tags" not in data and "branch" not in data:
            # Date of package releases is required as being displayed,
            # but sha256 is not provided by most explicit package releases, unfortunatelly.
            for key in ("url", "version", "date"):
                self.assertIn(
                    key,
                    data,
                    'A release must provide "url", "version" and '
                    '"date" keys if it does not specify "tags" or'
                    '"branch"',
                )

        else:
            for key in ("url", "version", "date", "sha256"):
                self.assertNotIn(
                    key,
                    data,
                    f'The key "{key}" is redundant when "asset", "tags" or "branch" is specified',
                )

        self.assertFalse(
            (("asset" in data or "tags" in data) and "branch" in data),
            'A release must have only one of the "tags" or "branch" keys.',
        )

        # Test keys values
        self._check_release_key_values(data, False)

    def _check_release_key_values(self, data, library):
        """Check the key-value pairs of a release for validity."""
        release_key_types_map = self.lib_release_key_types_map if library else self.pkg_release_key_types_map
        for key, value in data.items():
            self._check_key_value_types(key, value, release_key_types_map)

            match key:
                case "url":
                    if library:
                        if "sha256" not in data:
                            self.assertRegex(value, r"^https://")
                        else:
                            self.assertRegex(value, r"^http://")
                    else:
                        self.assertRegex(value, r"^https?://")

                case "base":
                    self.assertRegex(
                        value,
                        self.release_base_regex,
                        "The base url is badly formatted or invalid",
                    )

                case "sublime_text":
                    self.assertNotEqual(value, "*", 'Optional "sublime_text": "*" can be removed.')
                    self.assertRegex(
                        value,
                        r"^(\*|<=?\d{4}|>=?\d{4}|\d{4} - \d{4})$",
                        "sublime_text must be `*`, of the form `<relation><version>` where <relation> "
                        "is one of {<, <=, >, >=} and <version> is a 4 digit number, "
                        "or of the form `<version> - <version>`",
                    )

                    match = re.match(r"^([<>=]{1,2})(\d+)$", value)
                    if match:
                        op = match.group(1)
                        ver = int(match.group(2))
                        match op:
                            case "<=":
                                self.assertGreater(ver, 3142, "Release incompatible with ST3143+.")
                            case "<":
                                self.assertGreater(ver, 3143, "Release incompatible with ST3143+.")
                            case ">=":
                                self.assertGreater(ver, 3143, "Obsolete sublime_text specifier. Should be removed.")
                            case ">":
                                self.assertGreater(ver, 3142, "Obsolete sublime_text specifier. Should be removed.")

                    match = re.match(r"^(\d+) - (\d+)$", value)
                    if match:
                        ver2 = int(match.group(2))
                        self.assertGreater(ver2, 3142, "Release incompatible with ST3143+.")
                        ver1 = int(match.group(1))
                        self.assertGreater(ver1, 3142, f"Considder changing specifier to <={ver2}.")

                case "platforms":
                    if isinstance(value, str):
                        value = [value]

                    self.assertNotEqual(value, ["*"], 'Optional "platforms": ["*"] can be removed.')

                    for plat in value:
                        self.assertRegex(plat, r"^(\*|(osx|linux|windows)(-(x(32|64)|arm64))?)$")

                    self.assertCountEqual(
                        value,
                        set(value),
                        "Specifying the same platform multiple times is redundant",
                    )

                    if (
                        ("osx-x32" in value and "osx-x64" in value and "osx-arm64" in value)
                        or ("linux-x32" in value and "linux-x64" in value and "linux-arm64" in value)
                        or ("windows-x32" in value and "windows-x64" in value and "windows-arm64" in value)
                    ):
                        self.fail("Specifying all of x32, x64 and arm64 architectures is redundant")

                    self.assertNotEqual(
                        {"osx", "windows", "linux"},
                        set(value),
                        '"osx, windows, linux" are similar to (and should be replaced by) "*"',
                    )

                case "python_versions":
                    python_versions = ("3.3", "3.8", "3.13")
                    for pyver in value:
                        self.assertIn(pyver, python_versions, f"Unsupported value {pyver} in {key}!")

                case "date":
                    self.assertRegex(value, r"^\d\d\d\d-\d\d-\d\d \d\d:\d\d:\d\d$")

                case "tags":
                    self.assertTrue(bool(value), '"tags" must be `true` or a string of length>0')
                    if isinstance(value, str):
                        self.assertFalse(
                            value == "true",
                            '"true" is an unlikely prefix. You probably want the boolean `true` instead.',
                        )

                case "branch":
                    self.assertNotEqual(value, "", '"branch" must be non-empty')

                case "sha256":
                    self.assertRegex(value, r"^[0-9A-Fa-f]{64}$")

    def _check_key_value_types(self, key, value, key_types_map):
        self.assertIn(key, key_types_map, f"Unexpected key {key} found!")
        self.assertIsInstance(value, key_types_map[key], key)

        if isinstance(value, list) and key_types_map[key] is not list and len(key_types_map[key]) == 2:
            # Test if all of the lists elements are of the other allowed types
            other_types = tuple(filter(lambda t: t is not list, key_types_map[key]))
            for sub_value in value:
                self.assertIsInstance(sub_value, other_types, key)

    def _test_error(self, msg, e=None):
        """
        A generic error-returning function used by the meta-programming features
        of this class.

        :param msg:
            The error message to return

        :param e:
            An optional exception to include with the error message
        """

        if e:
            if isinstance(e, HTTPError):
                self.fail(f"{msg}: {e}")
            else:
                self.fail(f"{msg}: {e!r}")
        else:
            self.fail(msg)

    @classmethod
    def _generate_repository_tests(cls, path, stream, only_templates=True):
        """
        Yields tuples of (method, args) to add to a unittest TestCase class.
        A meta-programming function to expand the definition of class at run
        time, based on the contents of a file or URL.

        :param cls:
            The class to add the methods to

        :param path:
            The URL or file path to fetch the repository info from

        :param stream:
            A file-like object used for diagnostic output that provides .write()
            and .flush()
        """
        stream.write(f"{path} ... ")
        stream.flush()

        is_url = False
        success = False
        try:
            is_url = re.match(r"https?://", path, re.I) is not None
            if is_url:
                # prevent infinite recursions
                path = path.lower()
                if path in cls.seen_repositories:
                    yield cls._fail(f"Duplicate included {path} found!")
                    return
                cls.seen_repositories.add(path)

                # download the repository
                try:
                    req = Request(url=path, headers={"User-Agent": "Mozilla/5.0"})
                    with urlopen(req) as f:
                        source = f.read()
                except Exception as e:
                    yield cls._fail(f"Downloading {path} failed", e)
                    return
                source = source.decode("utf-8", "strict")

            else:
                # convert to local filesystem path
                if path.startswith("file:///"):
                    path = from_uri(path)

                # prevent infinite recursions
                if path in cls.seen_repositories:
                    yield cls._fail(f"Duplicate included {path} found!")
                    return
                cls.seen_repositories.add(path)

                # read repository from file
                try:
                    with open(path, encoding="utf-8") as f:
                        source = f.read()
                except Exception as e:
                    yield cls._fail(f"Opening {path} failed", e)
                    return

            if not source:
                yield cls._fail(f"{path} is empty")
                return

            # Parse the repository
            try:
                data = json.loads(source)
            except Exception as e:
                yield cls._fail(f"Could not parse {path}", e)
                return

            # Check for the schema version first (and generator failures it's
            # badly formatted)
            if "schema_version" not in data:
                yield cls._fail(f"No schema_version found in {path}")
                return
            schema = data["schema_version"]
            if schema not in ("2.0", "3.0.0", "4.0.0"):
                yield cls._fail(f"Unrecognized schema version {schema} in {path}")
                return

            success = True

            if path in BAD_REPOS:
                stream.write("skipping (known bad repo)")
                return

            # Do not generate 1000 failing tests for not yet updated repos
            if schema != "4.0.0":
                stream.write(f"skipping (schema version {data['schema_version']})")
                cls.skipped_repositories[schema] += 1
                return
            else:
                stream.write("done")
        finally:
            if not success:
                stream.write("failed")
            stream.write("\n")

        yield cls._test_indentation, (path, source)
        yield cls._test_repository_keys, (path, data)

        if "packages" in data:
            yield cls._test_package_names, (path, data)

            for package in data["packages"]:
                yield cls._test_package_keys, (path, package)

                package_name = get_package_name(package)

                for release in package.get("releases", []):
                    yield (
                        cls._test_package_release,
                        (f"{package_name} ({path})", release, only_templates),
                    )

        if "libraries" in data:
            yield cls._test_library_names, (path, data)

            for library in data["libraries"]:
                yield cls._test_library_keys, (path, library)

                library_name = library["name"]

                for release in library.get("releases", []):
                    yield (
                        cls._test_library_release,
                        (f"{library_name} ({path})", release, only_templates),
                    )

        if "includes" in data:
            root = os.path.dirname(path)
            for include in data["includes"]:
                if isinstance(include, str):
                    # resolve relative path
                    if include.startswith("./"):
                        include = urljoin(root, include) if is_url else os.path.normpath(os.path.join(root, include))
                    yield from cls._generate_repository_tests(include, stream, only_templates)

    @classmethod
    def _fail(cls, *args):
        """
        Generates a (method, args) tuple that returns an error when called.
        Allows for deferring an error until the tests are actually run.
        """

        return cls._test_error, args

    @classmethod
    def _write(cls, stream, string):
        """
        Writes diagnostic output to a file-like object.

        :param stream:
            Must have the methods .write() and .flush()

        :param string:
            The string to write - a newline will NOT be appended
        """

        stream.write(string)
        stream.flush()


@unittest.skipIf(
    not userargs.channel or not os.path.isfile(userargs.channel),
    f"No {userargs.channel} found",
)
@generate_test_methods
class ChannelTests(TestContainer, unittest.TestCase):
    maxDiff = None

    @classmethod
    def pre_generate(cls):
        """
        Load the JSON data into `cls.data` before generating tests.
        """
        with open(userargs.channel, encoding="utf-8") as f:
            cls.source = f.read()
            cls.data = json.loads(cls.source)

    @classmethod
    def generate_repository_tests(cls, stream):
        if not userargs.test_repositories or "repositories" not in cls.data:
            # Only generate tests for all repositories (those hosted online)
            # when run with "--test-repositories" parameter.
            return

        stream.write("Fetching remote repositories:\n")

        for repository in cls.data["repositories"]:
            if isinstance(repository, str) and repository.startswith(("file://", "https://")):
                yield from cls._generate_repository_tests(repository, stream, False)

        stream.write("\n")
        stream.flush()

    @classmethod
    def tearDownClass(cls):
        if cls.skipped_repositories:
            # TODO somehow pass stream here
            print(f"Repositories skipped: {dict(cls.skipped_repositories)}")

    def test_channel(self):
        self._test_channel_keys(userargs.channel, self.data)

    def test_indentation(self):
        self._test_indentation(userargs.channel, self.source)


@unittest.skipIf(
    not userargs.repository or not os.path.isfile(userargs.repository),
    f"No {userargs.repository} found",
)
@generate_test_methods
class RepositoryTests(TestContainer, unittest.TestCase):
    maxDiff = None

    @classmethod
    def generate_repository_tests(cls, stream):
        yield from cls._generate_repository_tests(userargs.repository, stream)


################################################################################
# Main


if __name__ == "__main__":
    unittest.main()
