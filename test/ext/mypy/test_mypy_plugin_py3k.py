import os
import re
import shutil
import tempfile

from sqlalchemy import testing
from sqlalchemy.testing import eq_
from sqlalchemy.testing import fixtures


class MypyPluginTest(fixtures.TestBase):
    __requires__ = ("sqlalchemy2_stubs",)

    @testing.fixture(scope="function")
    def per_func_cachedir(self):
        for item in self._cachedir():
            yield item

    @testing.fixture(scope="class")
    def cachedir(self):
        for item in self._cachedir():
            yield item

    def _cachedir(self):
        with tempfile.TemporaryDirectory() as cachedir:
            with open(
                os.path.join(cachedir, "sqla_mypy_config.cfg"), "w"
            ) as config_file:
                config_file.write(
                    """
                    [mypy]\n
                    plugins = sqlalchemy.ext.mypy.plugin\n
                    """
                )
            with open(
                os.path.join(cachedir, "plain_mypy_config.cfg"), "w"
            ) as config_file:
                config_file.write(
                    """
                    [mypy]\n
                    """
                )
            yield cachedir

    @testing.fixture()
    def mypy_runner(self, cachedir):
        from mypy import api

        def run(
            filename, use_plugin=True, incremental=False, working_dir=None
        ):
            if working_dir:
                path = os.path.join(working_dir, filename)
            else:
                path = os.path.join(
                    os.path.dirname(__file__), "files", filename
                )

            args = [
                "--strict",
                "--raise-exceptions",
                "--cache-dir",
                cachedir,
                "--config-file",
                os.path.join(
                    cachedir,
                    "sqla_mypy_config.cfg"
                    if use_plugin
                    else "plain_mypy_config.cfg",
                ),
            ]

            args.append(path)

            return api.run(args)

        return run

    def _incremental_dirs():
        path = os.path.join(os.path.dirname(__file__), "incremental")
        files = []
        for d in os.listdir(path):
            if os.path.isdir(os.path.join(path, d)):
                files.append(
                    os.path.join(os.path.dirname(__file__), "incremental", d)
                )

        for extra_dir in testing.config.options.mypy_extra_test_paths:
            if extra_dir and os.path.isdir(extra_dir):
                for d in os.listdir(os.path.join(extra_dir, "incremental")):
                    if os.path.isdir(os.path.join(path, d)):
                        files.append(os.path.join(extra_dir, "incremental", d))
        return files

    @testing.combinations(
        *[(pathname,) for pathname in _incremental_dirs()], argnames="pathname"
    )
    @testing.requires.patch_library
    def test_incremental(self, mypy_runner, per_func_cachedir, pathname):
        import patch

        cachedir = per_func_cachedir

        dest = os.path.join(cachedir, "mymodel")
        os.mkdir(dest)

        patches = set()

        print("incremental test: %s" % pathname)

        for fname in os.listdir(pathname):
            if fname.endswith(".py"):
                shutil.copy(
                    os.path.join(pathname, fname), os.path.join(dest, fname)
                )
                print("copying to: %s" % os.path.join(dest, fname))
            elif fname.endswith(".testpatch"):
                patches.add(fname)

        for patchfile in [None] + sorted(patches):
            if patchfile is not None:
                print("Applying patchfile %s" % patchfile)
                patch_obj = patch.fromfile(os.path.join(pathname, patchfile))
                assert patch_obj.apply(1, dest), (
                    "pathfile %s failed" % patchfile
                )
            print("running mypy against %s/mymodel" % cachedir)
            result = mypy_runner(
                "mymodel",
                use_plugin=True,
                incremental=True,
                working_dir=cachedir,
            )
            eq_(
                result[2],
                0,
                msg="Failure after applying patch %s: %s"
                % (patchfile, result[0]),
            )

    def _file_combinations():
        path = os.path.join(os.path.dirname(__file__), "files")
        files = []
        for f in os.listdir(path):
            if f.endswith(".py"):
                files.append(
                    os.path.join(os.path.dirname(__file__), "files", f)
                )

        for extra_dir in testing.config.options.mypy_extra_test_paths:
            if extra_dir and os.path.isdir(extra_dir):
                for f in os.listdir(os.path.join(extra_dir, "files")):
                    if f.endswith(".py"):
                        files.append(
                            os.path.join(
                                os.path.dirname(extra_dir), "files", f
                            )
                        )
        return files

    @testing.combinations(
        *[(filename,) for filename in _file_combinations()], argnames="path"
    )
    def test_mypy(self, mypy_runner, path):
        filename = os.path.basename(path)
        use_plugin = True

        expected_errors = []
        with open(path) as file_:
            for num, line in enumerate(file_, 1):
                if line.startswith("# NOPLUGINS"):
                    use_plugin = False
                    continue

                m = re.match(r"\s*# EXPECTED(_MYPY)?: (.+)", line)
                if m:
                    is_mypy = bool(m.group(1))
                    expected_msg = m.group(2)
                    expected_msg = re.sub(r"# noqa ?.*", "", m.group(2))
                    expected_errors.append(
                        (num, is_mypy, expected_msg.strip())
                    )

        result = mypy_runner(filename, use_plugin=use_plugin)

        if expected_errors:
            eq_(result[2], 1)

            print(result[0])

            errors = []
            for e in result[0].split("\n"):
                if re.match(r".+\.py:\d+: error: .*", e):
                    errors.append(e)

            for num, is_mypy, msg in expected_errors:
                msg = msg.replace("'", '"')
                prefix = "[SQLAlchemy Mypy plugin] " if not is_mypy else ""
                for idx, errmsg in enumerate(errors):
                    if (
                        f"{filename}:{num + 1}: error: {prefix}{msg}"
                        in errmsg.replace("'", '"')
                    ):
                        break
                else:
                    continue
                del errors[idx]

            assert not errors, "errors remain: %s" % "\n".join(errors)

        else:
            eq_(result[2], 0, msg=result[0])
