import os
import subprocess
from dataclasses import dataclass

INITHOOK_LOG = os.getenv("INITHOOKS_LOGFILE", "/var/log/inithooks.log")
LOG_LEVELS = ["err", "warn", "info", "debug"]


class InitLogError(Exception):
    pass


@dataclass
class InitLog:
    inithook_name: str
    log_file: str = INITHOOK_LOG

    def write(self, msg: str, level: str = "info") -> None:
        """Write to log & journal
        valid levels: err|warn|info|debug
        """
        if level not in LOG_LEVELS:
            raise InitLogError(f"invalid log level '{level}'")
        msg = f"[{self.inithook_name}] {msg}".rstrip()
        subprocess.run(
            [
                "/usr/bin/logger",
                "-t",
                "inithooks",
                "-p",
                level,
                msg,
            ]
        )
        with open(self.log_file, "a") as fob:
            fob.write(f"{msg}\n")
