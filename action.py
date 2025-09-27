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

from functools import wraps
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urljoin

# known bad repositories that we'll just have to skip for now
BAD_REPOS = [
    "https://packages.monokai.pro/packages.json",
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


def _open(filepath, *args, **kwargs):
    """Wrapper function to search one dir above if a file does not exist."""
    if not os.path.exists(filepath):
        filepath = os.path.join("..", filepath)

    return open(filepath, "rb", *args, **kwargs)


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
            for v in params:
                string = repr(v)
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


################################################################################
# Tests


class TestContainer:
    """Contains tests that the generators can easily access (when subclassing).

    Does not contain tests itself, must be used as mixin with unittest.TestCase.
    """

    @classmethod
    def setUpClass(cls):
        cls.package_names = CaseInsensitiveDict()
        cls.dependency_names = CaseInsensitiveDict()
        # tuple of (prev_name, include, name); prev_name for case sensitivity
        cls.previous_package_names = CaseInsensitiveDict()

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
        "Text",
        "Textile",
        "Theme - Default",
        "Vintage",
        "XML",
        "YAML",
    )

    rel_b_reg = r"""^ ( https:// bitbucket\.org / [^/#?]+ / [^/#?]+
                      | https:// github\.com / [^/#?]+ / [^/#?]+
                      | https:// gitlab\.com / [^/#?]+ / [^/#?]+
                      ) $"""
    # Strip multilines for better debug info on failures
    rel_b_reg = " ".join(map(str.strip, rel_b_reg.split()))
    release_base_regex = re.compile(rel_b_reg, re.X)

    pac_d_reg = r"""^ ( https:// bitbucket\.org/ [^/#?]+/ [^/#?]+
                        ( /src/ [^#?]*[^/#?] | \#tags | / )?
                      | https:// github\.com/ [^/#?]+/ [^/#?]+
                        (?<!\.git) ( /tree/ [^#?]*[^/#?] | / )?
                      | https:// gitlab\.com/ [^/#?]+/ [^/#?]+
                        (?<!\.git) ( /-/tree/ [^#?]*[^/#?] | / )?
                      ) $"""
    pac_d_reg = " ".join(map(str.strip, pac_d_reg.split()))
    package_details_regex = re.compile(pac_d_reg, re.X)

    def _test_repository_keys(self, filename, data):
        keys = ("$schema", "schema_version", "packages", "dependencies", "includes")
        self.assertTrue(2 <= len(data) <= len(keys), "Unexpected number of keys")
        self.assertIn("schema_version", data)
        self.assertEqual(data["schema_version"], "3.0.0")

        listkeys = [k for k in ("packages", "dependencies", "includes") if k in data]
        self.assertGreater(len(listkeys), 0, "Must contain something")
        for k in listkeys:
            self.assertIsInstance(data[k], list)

        for k in data:
            self.assertIn(k, keys, "Unexpected key")

        includes = data.get("includes", [])
        self.assertIsInstance(includes, list)

        for include in includes:
            self.assertIsInstance(include, str)

    def _test_dependency_names(self, include, data):
        m = re.search(r"(?:^|/)(0-9|[a-z]|dependencies)\.json$", include)
        if not m:
            self.fail("Include filename does not match")

        repo_dependency_names = []
        for pdata in data["dependencies"]:
            name = get_package_name(pdata)
            if name in self.dependency_names:
                self.fail("Dependency names must be unique: " + name)
            else:
                self.dependency_names[name] = include
                repo_dependency_names.append(name)
            if name in self.package_names:
                self.fail(
                    f"Dependency and package names must be unique: {name}, "
                    f"previously occurred in {self.package_names[name]})"
                )

        # Check package order
        self.assertEqual(
            repo_dependency_names,
            sorted(repo_dependency_names, key=str.lower),
            "Dependencies must be sorted alphabetically",
        )

    def _test_repository_package_names(self, include, data):
        m = re.search(r"(?:^|/)(0-9|[a-z]|dependencies)\.json$", include)
        if not m:
            self.fail("Include filename does not match")
        letter = m.group(1)

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
            elif pname in self.dependency_names:
                self.fail(
                    f"Dependency and package names must be unique: {pname}, "
                    f"previously occurred in {self.dependency_names[pname]}"
                )
            else:
                self.package_names[pname] = include
                repo_package_names.append(pname)

        # Check if in the correct file
        for package_name in repo_package_names:
            if letter == "0-9":
                self.assertTrue(package_name[0].isdigit(), "Package inserted in wrong file")
            else:
                self.assertEqual(package_name[0].lower(), letter, "Package inserted in wrong file")

        # Check package order
        self.assertEqual(
            repo_package_names,
            sorted(repo_package_names, key=str.lower),
            "Packages must be sorted alphabetically (by name)",
        )

    def _test_indentation(self, filename, contents):
        for i, line in enumerate(contents.splitlines()):
            self.assertRegex(line, r"^\t*\S", f"Indent must be tabs in line {i + 1}")

    package_key_types_map = {
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

    def _test_package(self, include, data):
        name = get_package_name(data)

        for k, v in data.items():
            self.enforce_key_types_map(k, v, self.package_key_types_map)

            if k == "details":
                self.assertRegex(
                    v,
                    self.package_details_regex,
                    "The details url is badly formatted or invalid",
                )

            elif k == "donate" and v is None:
                # Allow "removing" the donate url that is added by "details"
                continue

            elif k == "labels":
                for label in v:
                    self.assertNotIn(",", label, "Multiple labels should not be in the same string")

                    # self.assertEqual(label, label.lower(),
                    #                  "Label name must be lowercase")

                self.assertCountEqual(
                    v,
                    list(set(v)),
                    "Specifying the same label multiple times is redundant",
                )

            elif k == "previous_names":
                # Test if name is unique, against names and previous_names.
                for prev_name in v:
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
                        self.previous_package_names[prev_name] = (
                            prev_name,
                            include,
                            name,
                        )

            elif k in ("homepage", "readme", "issues", "donate", "buy"):
                self.assertRegex(v, "^https?://")

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

    dependency_key_types_map = {
        "name": str,
        "description": str,
        "releases": list,
        "issues": str,
        "load_order": str,
        "author": str,
    }

    def _test_dependency(self, include, data):
        for k, v in data.items():
            self.enforce_key_types_map(k, v, self.dependency_key_types_map)

            if k == "issues":
                self.assertRegex(v, "^https?://")

            # Test for invalid characters (on file systems)
            elif k == "name":
                # Invalid on Windows (and sometimes problematic on UNIX)
                self.assertNotRegex(v, r'[/?<>\\:*|"\x00-\x19]')
                self.assertFalse(v.startswith("."))

            elif k == "load_order":
                self.assertRegex(v, r"^\d\d$", '"load_order" must be a two digit string')
        for key in ("author", "releases", "issues", "description", "load_order"):
            self.assertIn(key, data, f"{key!r} is required for dependencies")

    pck_release_key_types_map = {
        "base": str,
        "tags": (bool, str),
        "branch": str,
        "sublime_text": str,
        "platforms": (list, str),
        "dependencies": (list, str),
        "version": str,
        "date": str,
        "url": str,
    }

    dep_release_key_types_map = {
        "base": str,
        "tags": (bool, str),
        "branch": str,
        "sublime_text": str,
        "platforms": (list, str),
        "version": str,
        "sha256": str,
        "url": str,
    }

    def _test_release(self, package_name, data, dependency, main_repo=True):
        # Test for required keys (and fail early)
        if main_repo:
            if dependency:
                condition = (
                    "base" in data
                    and ("tags" in data or "branch" in data)
                    or ("sha256" in data and ("url" not in data or data["url"].startswith("http://")))
                )
                self.assertTrue(
                    condition,
                    'A release must have a "base" and a "tags" or "branch" key '
                    "if it is in the main repository. For custom "
                    "releases, a custom repository.json file must be "
                    "hosted elsewhere. The only exception to this rule "
                    "is for packages that can not be served over HTTPS "
                    "since they help bootstrap proper secure HTTP "
                    "support for Sublime Text.",
                )
            else:
                self.assertTrue(
                    ("tags" in data or "branch" in data),
                    'A release must have a "tags" key or "branch" key '
                    "if it is in the main repository. For custom "
                    "releases, a custom repository.json file must be "
                    "hosted elsewhere.",
                )
                for key in ("url", "version", "date"):
                    self.assertNotIn(
                        key,
                        data,
                        "The version, date and url keys should not be "
                        "used in the main repository since a pull "
                        "request would be necessary for every release",
                    )

        elif "tags" not in data and "branch" not in data:
            if dependency:
                for key in ("url", "version"):
                    self.assertIn(
                        key,
                        data,
                        'A release must provide "url" and "version" keys if it does not specify "tags" or "branch"',
                    )
            else:
                for key in ("url", "version", "date"):
                    self.assertIn(
                        key,
                        data,
                        'A release must provide "url", "version" and '
                        '"date" keys if it does not specify "tags" or'
                        '"branch"',
                    )

        else:
            for key in ("url", "version", "date"):
                self.assertNotIn(
                    key,
                    data,
                    f'The key "{key}" is redundant when "tags" or "branch" is specified',
                )

        self.assertIn("sublime_text", data, "A sublime text version selector is required")

        if dependency:
            self.assertIn("platforms", data, "A platforms selector is required for dependencies")

        self.assertFalse(
            ("tags" in data and "branch" in data),
            'A release must have only one of the "tags" or "branch" keys.',
        )

        # Test keys values
        self.check_release_key_values(data, dependency)

    def check_release_key_values(self, data, dependency):
        """Check the key-value pairs of a release for validity."""
        release_key_types_map = self.dep_release_key_types_map if dependency else self.pck_release_key_types_map
        for k, v in data.items():
            self.enforce_key_types_map(k, v, release_key_types_map)

            if k == "url":
                if dependency:
                    if "sha256" not in data:
                        self.assertRegex(v, r"^https://")
                    else:
                        self.assertRegex(v, r"^http://")
                else:
                    self.assertRegex(v, r"^https?://")

            elif k == "base":
                self.assertRegex(
                    v,
                    self.release_base_regex,
                    "The base url is badly formatted or invalid",
                )

            elif k == "sublime_text":
                self.assertRegex(
                    v,
                    r"^(\*|<=?\d{4}|>=?\d{4}|\d{4} - \d{4})$",
                    "sublime_text must be `*`, of the form "
                    "`<relation><version>` "
                    "where <relation> is one of {<, <=, >, >=} "
                    "and <version> is a 4 digit number, "
                    "or of the form `<version> - <version>`",
                )

            elif k == "platforms":
                if isinstance(v, str):
                    v = [v]
                for plat in v:
                    self.assertRegex(plat, r"^(\*|(osx|linux|windows)(-(x(32|64)|arm64))?)$")

                self.assertCountEqual(
                    v,
                    list(set(v)),
                    "Specifying the same platform multiple times is redundant",
                )

                if (
                    ("osx-x32" in v and "osx-x64" in v and "osx-arm64" in v)
                    or ("windows-x32" in v and "windows-x64" in v and "windows-arm64" in v)
                    or ("linux-x32" in v and "linux-x64" in v and "linux-arm64" in v)
                ):
                    self.fail("Specifying all of x32, x64 and arm64 architectures is redundant")

                self.assertFalse(
                    {"osx", "windows", "linux"} == set(v),
                    '"osx, windows, linux" are similar to (and should be replaced by) "*"',
                )

            elif k == "date":
                self.assertRegex(v, r"^\d\d\d\d-\d\d-\d\d \d\d:\d\d:\d\d$")

            elif k == "tags":
                self.assertTrue(bool(v), '"tags" must be `true` or a string of length>0')
                if isinstance(v, str):
                    self.assertFalse(
                        v == "true",
                        'It is unlikely to specify the prefix "true" use not the boolean `true`',
                    )

            elif k == "branch":
                self.assertNotEqual(v, "", '"branch" must be non-empty')

            elif k == "sha256":
                self.assertRegex(v, r"(?i)^[0-9a-f]{64}$")

    def enforce_key_types_map(self, k, v, key_types_map):
        self.assertIn(k, key_types_map, f'Unknown key "{k}"')
        self.assertIsInstance(v, key_types_map[k], k)

        if isinstance(v, list) and key_types_map[k] is not list and len(key_types_map[k]) == 2:
            # Test if all of the lists elements are of the other allowed types
            other_types = tuple(filter(lambda t: t is not list, key_types_map[k]))
            for sub_value in v:
                self.assertIsInstance(sub_value, other_types, k)

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
    def _include_tests(cls, path, stream):
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
        # TODO multi-threading
        stream.write(f"{path} ... ")
        stream.flush()

        success = False
        try:
            if re.match(r"https?://", path, re.I) is not None:
                # Download the repository
                try:
                    req = Request(url=path, headers={"User-Agent": "Mozilla/5.0"})
                    with urlopen(req) as f:
                        source = f.read()
                except Exception as e:
                    yield cls._fail(f"Downloading {path} failed", e)
                    return
                source = source.decode("utf-8", "strict")
            else:
                try:
                    with _open(path) as f:
                        source = f.read().decode("utf-8", "strict")
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
            if schema != "3.0.0" and float(schema) not in (1.0, 1.1, 1.2, 2.0):
                yield cls._fail(f"Unrecognized schema version {schema} in {path}")
                return

            success = True

            if path in BAD_REPOS:
                stream.write("skipping (known bad repo)")
                return

            # Do not generate 1000 failing tests for not yet updated repos
            if schema != "3.0.0":
                stream.write(f"skipping (schema version {data['schema_version']})")
                cls.skipped_repositories[schema] += 1
                return
            else:
                stream.write("done")
        finally:
            if not success:
                stream.write("failed")
            stream.write("\n")

        # `path` is for output during tests only
        yield cls._test_repository_keys, (path, data)

        if "packages" in data:
            for package in data["packages"]:
                yield cls._test_package, (path, package)

                package_name = get_package_name(package)

                if "releases" in package:
                    for release in package["releases"]:
                        (
                            yield (
                                cls._test_release,
                                (
                                    f"{package_name} ({path})",
                                    release,
                                    False,
                                    False,
                                ),
                            )
                        )
        if "includes" in data:
            for include in data["includes"]:
                i_url = urljoin(path, include)
                yield from cls._include_tests(i_url, stream)

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
    def setUpClass(cls):
        super().setUpClass()
        cls.pre_generate()

    # We load the JSON data into `cls.j` before generating tests.
    @classmethod
    def pre_generate(cls):
        if not hasattr(cls, "j") and os.path.isfile(userargs.channel):
            with _open(userargs.channel) as f:
                cls.source = f.read().decode("utf-8", "strict")
                cls.j = json.loads(cls.source)

            from collections import defaultdict

            cls.skipped_repositories = defaultdict(int)

    @classmethod
    def tearDownClass(cls):
        if cls.skipped_repositories:
            # TODO somehow pass stream here
            print(f"Repositories skipped: {dict(cls.skipped_repositories)}")

    def test_channel_keys(self):
        allowed_keys = ("$schema", "repositories", "schema_version")
        keys = sorted(self.j.keys())

        self.assertTrue(2 <= len(keys) <= len(allowed_keys), "Unexpected number of keys")

        for k in keys:
            self.assertIn(k, allowed_keys, "Unexpected key")

        self.assertIn("schema_version", keys)
        self.assertEqual(self.j["schema_version"], "3.0.0")

        self.assertIn("repositories", keys)
        self.assertIsInstance(self.j["repositories"], list)

        for repo in self.j["repositories"]:
            self.assertIsInstance(repo, str)

    def test_indentation(self):
        return self._test_indentation(userargs.channel, self.source)

    def test_channel_repositories(self):
        repos = self.j["repositories"]
        for repo in repos:
            self.assertRegex(
                repo,
                r"^(\.|https://)",
                "Repositories must be relative urls or use the HTTPS protocol",
            )
        self.assertEqual(
            repos,
            sorted(repos, key=str.lower),
            "Repositories must be sorted alphabetically",
        )

    @classmethod
    def generate_repository_tests(cls, stream):
        if not userargs.test_repositories:
            # Only generate tests for all repositories (those hosted online)
            # when run with "--test-repositories" parameter.
            return

        stream.write("Fetching remote repositories:\n")

        for repository in cls.j["repositories"]:
            if repository.startswith("."):
                continue
            if not repository.startswith("http"):
                cls._fail(f"Unexpected repository url: {repository}")

            yield from cls._include_tests(repository, stream)

        stream.write("\n")
        stream.flush()


@unittest.skipIf(
    not userargs.repository or not os.path.isfile(userargs.repository),
    f"No {userargs.repository} found",
)
@generate_test_methods
class RepositoryTests(TestContainer, unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pre_generate()

    # We load the JSON data into `cls.j` before generating tests.
    @classmethod
    def pre_generate(cls):
        if not hasattr(cls, "j"):
            with _open(userargs.repository) as f:
                cls.source = f.read().decode("utf-8", "strict")
                cls.j = json.loads(cls.source)

    def test_repository_keys(self):
        self._test_repository_keys(userargs.repository, self.j)

    def test_indentation(self):
        return self._test_indentation(userargs.repository, self.source)

    @classmethod
    def generate_include_tests(cls, stream):
        for include in cls.j.get("includes", []):
            try:
                with _open(include) as f:
                    contents = f.read().decode("utf-8", "strict")
                data = json.loads(contents)
            except Exception as e:
                yield cls._fail(f"strict while reading {include!r}", e)
                continue

            # `include` is for output during tests only
            yield cls._test_indentation, (include, contents)
            yield cls._test_repository_keys, (include, data)
            yield cls._test_repository_package_names, (include, data)

            for package in data["packages"]:
                yield cls._test_package, (include, package)

                package_name = get_package_name(package)

                if "releases" in package:
                    for release in package["releases"]:
                        (
                            yield (
                                cls._test_release,
                                (f"{package_name} ({include})", release, False),
                            )
                        )

            if "dependencies" in data:
                yield cls._test_dependency_names, (include, data)

                for dependency in data["dependencies"]:
                    yield cls._test_dependency, (include, dependency)

                    dependency_name = get_package_name(dependency)

                    if "releases" in dependency:
                        for release in dependency["releases"]:
                            (
                                yield (
                                    cls._test_release,
                                    (
                                        f"{dependency_name} ({include})",
                                        release,
                                        True,
                                    ),
                                )
                            )

        yield cls._fail("This won't be executed for some reason")


################################################################################
# Main


if __name__ == "__main__":
    unittest.main()
