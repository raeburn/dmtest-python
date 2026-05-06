import logging as log
import os
import subprocess
import dmtest.dependency_tracker as dep


def run(command, raise_on_fail=True):
    log.info(f"running: '{command}'")

    # Register the exe with the dependency tracker
    exe = command.lstrip().split()[0]
    dep.add_exe(exe)

    # ensure we get some good debug for tools written in rust
    # if they panic
    bt_env = os.environ.copy()
    bt_env["RUST_BACKTRACE"] = "full"

    proc = subprocess.Popen(
        command,
        env=bt_env,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    stdout, stderr = proc.communicate()
    if stdout:
        log.info(f"stdout:\n{stdout.rstrip()}")
    if stderr:
        log.info(f"stderr:\n{stderr.rstrip()}")
    if proc.returncode:
        log.info(f"return code: {proc.returncode}")
    return_code = proc.returncode
    if return_code and raise_on_fail:
        log.error(f"process '{command}' failed with exit status {return_code}, raising exception")
        raise subprocess.CalledProcessError(return_code, command)
    return (return_code, stdout.strip(), stderr.strip())
