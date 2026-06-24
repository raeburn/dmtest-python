# pytest branch status

I set Claude Code to work to convert these tests to use pytest while I was working on other things. I haven't worked much on refining it since. Some assorted notes:

Running "./dmtest run" works, and invokes pytest to actually run tests. Tried to preserve the result-logging interface here as an option; untested. Default is not to preserve.

Running just "pytest" does not work, at least without explicitly updating the Python path too; set an option in pyproject.toml?

thin/discard_tests.py seems to be hanging, with blktrace and blkparse subprocesses? (^C stack trace says waitpid but that's dmtest script waiting for pytest. Maybe we should do an exec instead of running a subprocess in cmd_run.)

But some other test files (blk_archive, bufio, cache, some thin) seem to run okay before that point. "./dmtest run src/dmtest/vdo" runs okay. Just "./dmtest run vdo" doesn't work, it's not the same pattern matching as before.

We created pyproject.toml but there are other filenames that could be used to flag the project root directory for pytest, with slightly different purposes.

Output is more brief, when passing tests, one line per test file. No reporting of test names or time per test, by default. Has various quiet/verbose settings. "-v" shows one line per test, still no timing, but there's also a "--durations=0" option to show timings at the end of the whole run. (It show slowest N so has to do it at the end, but N=0 means all and doesn't change the format.) Docs indicate much more verbose form when failing, showing failing lines of code; again, some options for control. Plugins and hooks too.

Normally test files are "*_test.py" but with the presence of "*_tests.py" files Claude decided to tell pytest to look for a different pattern. Some files didn't match either and had to be renamed. We could go with pytest's default, and rename the _tests files.

Tried to preserve the result database management behavior as an option but haven't looked to see if it works (or if Claude did in fact try to preserve it).
