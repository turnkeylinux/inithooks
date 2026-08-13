# Copyright (c) 2010 Alon Swartz <alon@turnkeylinux.org>
# Copyright (c) 2020-2025 TurnKey GNU/Linux <admin@turnkeylinux.org>

import re
import sys
import dialog
import traceback
from io import StringIO
from os import environ
from urllib.parse import urlparse
import logging

EMAIL_RE = re.compile(r"(?:^|\s).*\S@\S+(?:\s|$)", re.IGNORECASE)

LOG_LEVEL = logging.INFO
if "DIALOG_DEBUG" in environ.keys():
    LOG_LEVEL = logging.DEBUG

logging.basicConfig(
    filename="/var/log/dialog.log", encoding="utf-8", level=LOG_LEVEL
)


class Error(Exception):
    pass


def password_complexity(password: str) -> int:
    """return password complexity score from 0 (invalid) to 4 (strong)"""

    lowercase = re.search("[a-z]", password) is not None
    uppercase = re.search("[A-Z]", password) is not None
    number = re.search(r"\d", password) is not None
    nonalpha = re.search(r"\W", password) is not None

    return sum([lowercase, uppercase, number, nonalpha])


class Dialog:
    def __init__(self, title: str, width: int = 60, height: int = 20) -> None:
        self.width = width
        self.height = height

        self.console = dialog.Dialog(dialog="dialog")
        self.console.add_persistent_args(["--no-collapse"])
        self.console.add_persistent_args(["--backtitle", title])
        self.console.add_persistent_args(["--no-mouse"])

    def _handle_exitcode(self, retcode: int) -> bool:
        logging.debug(f"_handle_exitcode(retcode={retcode!r})")
        if retcode == self.console.ESC:  # ESC, ALT+?
            text = "Do you really want to quit?"
            if self.console.yesno(text) == self.console.OK:
                sys.exit(0)
            return False
        logging.debug(
            "_handle_exitcode(): [no conditions met, returning True]"
        )
        return True

    def _calc_height(self, text: str) -> int:
        height = 6
        for line in text.splitlines():
            height += (len(line) // self.width) + 1

        return height

    def wrapper(
        self, dialog_name: str, text: str, *args, **kws
    ) -> tuple[int, str]:
        retcode = 0
        logging.debug(
            f"wrapper(dialog_name={dialog_name!r}, text=<redacted>,"
            f" *{args!r}, **{kws!r})"
        )
        try:
            method = getattr(self.console, dialog_name)
        except AttributeError as e:
            logging.error(
                f"wrapper(dialog_name={dialog_name!r}, ...) raised exception",
                exc_info=e,
            )
            raise Error("dialog not supported: " + dialog_name)

        while 1:
            try:
                retcode = method("\n" + text, *args, **kws)
                logging.debug(
                    f"wrapper(dialog_name={dialog_name!r}, ...) -> {retcode!r}"
                )
                if self._handle_exitcode(retcode):
                    break

            except Exception as e:
                sio = StringIO()
                traceback.print_exc(file=sio)
                logging.error(
                    f"wrapper(dialog_name={dialog_name!r}) raised exception",
                    exc_info=e,
                )
                self.msgbox("Caught exception", sio.getvalue())

        return retcode

    def error(self, text: str) -> tuple[int, str]:
        """'Error' titled message with single 'ok' button
        Returns 'Ok'"""
        height = self._calc_height(text)
        return self.wrapper("msgbox", text, height, self.width, title="Error")

    def msgbox(self, title: str, text: str) -> tuple[int, str]:
        """Titled message with single 'ok' button
        Returns 'Ok'"""
        height = self._calc_height(text)
        logging.debug(f"msgbox(title={title!r}, text=<redacted>)")
        return self.wrapper("msgbox", text, height, self.width, title=title)

    def infobox(self, text: str) -> tuple[int, str]:
        """Untitled message with single 'ok' button
        Returns 'Ok'"""
        height = self._calc_height(text)
        logging.debug(f"infobox(text={text!r}")
        return self.wrapper("infobox", text, height, self.width)

    def inputbox(
        self,
        title: str,
        text: str,
        init: str = "",
        ok_label: str = "OK",
        cancel_label: str = "Cancel",
    ) -> tuple[int, str]:
        """Titled message with text input and single choice of 2 buttons
        Returns 'Ok' or "Cancel'"""
        logging.debug(
            f"inputbox(title={title!r}, text=<redacted>,"
            + f" init={init!r}, ok_label={ok_label!r},"
            + f" cancel_label={cancel_label!r})"
        )

        height = self._calc_height(text) + 3
        no_cancel = True if cancel_label == "" else False
        logging.debug(
            f"inputbox(...) [calculated height={height},"
            f" no_cancel={no_cancel}]"
        )
        return self.wrapper(
            "inputbox",
            text,
            height,
            self.width,
            title=title,
            init=init,
            ok_label=ok_label,
            cancel_label=cancel_label,
            no_cancel=no_cancel,
        )

    def yesno(
        self,
        title: str,
        text: str,
        yes_label: str = "Yes",
        no_label: str = "No",
    ) -> bool:
        """Titled message with single choice of 2 buttons
        Returns True ('Yes" button) or False ('No' button)"""
        height = self._calc_height(text)
        retcode = self.wrapper(
            "yesno",
            text,
            height,
            self.width,
            title=title,
            yes_label=yes_label,
            no_label=no_label,
        )
        logging.debug(
            f"yesno(title={title!r}, text=<redacted>,"
            f" yes_label={yes_label!r}, no_label={no_label!r})"
            f" -> {retcode}"
        )
        return True if retcode == "ok" else False

    def menu(
        self,
        title: str,
        text: str,
        # [(opt1, opt1_info), (opt2, opt2_info)]
        choices: list[tuple[str, str]],
    ) -> str:
        """Titled message with single choice of options & 'ok' button
        Returns selected option - e.g. 'opt1'"""
        _, choice = self.wrapper(  # return_code, choice
            "menu",
            text,
            self.height,
            self.width,
            menu_height=len(choices) + 1,
            title=title,
            choices=choices,
            no_cancel=True,
        )
        return choice

    def get_password(
        self,
        title: str,
        text: str,
        pass_req: int = 8,
        min_complexity: int = 3,
        blacklist: list[str] | None = None,
    ) -> str | None:
        """Validated titled message with password (redacted input) box &
        'ok' button - also accepts password limitations
        Returns password"""
        req_string = (
            f"\n\nPassword Requirements\n - must be at least {pass_req}"
            " characters long\n - must contain characters from at"
            f" least {min_complexity} of the following categories: uppercase,"
            " lowercase, numbers, symbols"
        )
        if blacklist:
            req_string = (
                f"{req_string}. Also must NOT contain these characters:"
                f" {' '.join(blacklist)}"
            )
        else:
            blacklist = []
        height = self._calc_height(text + req_string) + 3

        def ask(title: str, text: str) -> str:
            """Titled input box (input redacted) & 'ok' button"""
            return self.wrapper(
                "passwordbox",
                text + req_string,
                height,
                self.width,
                title=title,
                ok_label="OK",
                no_cancel="True",
                insecure=True,
            )[1]

        while 1:
            password = ask(title, text)
            if not password:
                self.error("Please enter non-empty password!")
                continue

            if isinstance(pass_req, int):
                if len(password) < pass_req:
                    self.error(
                        f"Password must be at least {pass_req} characters."
                    )
                    continue
            elif not re.match(pass_req, password):
                # TODO "Type analysis indicates code is unreachable"?!
                self.error("Password does not match complexity requirements.")
                continue

            if password_complexity(password) < min_complexity:
                if min_complexity <= 3:
                    self.error(
                        "Insecure password! Mix uppercase, lowercase,"
                        " and at least one number. Multiple words and"
                        " punctuation are highly recommended but not"
                        " strictly required."
                    )
                elif min_complexity == 4:
                    self.error(
                        "Insecure password! Mix uppercase, lowercase,"
                        " numbers and at least one special/punctuation"
                        " character. Multiple words are highly"
                        " recommended but not strictly required."
                    )
                continue

            found_items = []
            for item in blacklist:
                if item in password:
                    found_items.append(item)
            if found_items:
                self.error(
                    f"Password can NOT include these characters: {blacklist}."
                    f" Found {found_items}"
                )
                continue

            if password == ask(title, "Confirm password"):
                return password

            self.error("Password mismatch, please try again.")

    def get_email(self, title: str, text: str, init: str = "") -> str | None:
        """Vaidated input box (email) with optional prefilled value and 'Ok'
        button
        Returns email"""
        logging.debug(
            f"get_email(title={title!r}, text=<redacted>, init={init!r})"
        )
        while 1:
            email = self.inputbox(title, text, init, "Apply", "")[1]
            logging.debug(f"get_email(...) email={email!r}")
            if not email:
                self.error("Email is required.")
                continue

            if not EMAIL_RE.match(email):
                self.error("Email is not valid")
                continue

            return email

    def get_domain(self, title: str, text: str, init: str = "") -> tuple[str, str] | None:
        """Validated domain input box with optional prefilled value. Strips scheme
        Returns domain"""
        logging.debug(
            f"get_domain(title={title!r}, text=<redacted>, init={init!r})"
        )
        while 1:
            domain = self.inputbox(title, text, init, "Apply", "")[1]

            domain, scheme, message = validate_domain(domain)

            if not domain:
                self.error(message)
                continue
            elif message == 'Extra parts':
                if self.yesno('Domain Confirmation', f'Extra non-domain parts recieved, is this the domain you want to set? `{p.netloc}`'):
                    return (scheme, domain)
                continue
            else:
                return (scheme, domain)

    def get_input(self, title: str, text: str, init: str = "") -> str | None:
        """Input box within optional prefilled value & 'Ok' button
        Returns input"""
        while 1:
            s = self.inputbox(title, text, init, "Apply", "")[1]
            if not s:
                self.error(f"{title} is required.")
                continue
            return s

_LABEL_RE = re.compile(r'^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$')
_SCHEME_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.\-]*://')
 
def validate_domain(domain: str) -> tuple[str | None, str | None, str | None]:
    """
    Returns (domain, scheme, message).
 
    - domain is None only on a hard failure (input could not be salvaged);
      `message` explains why and the UI should block submission.
    - domain is not None and message is not None when something *meaningful*
      was removed (path, query, fragment, port) - UI should confirm (yes/no)
      before accepting the cleaned value.
    - domain is not None and message is None when input was already clean,
      or only had a non-meaningful change removed (scheme pulled into its
      own field, a lone trailing slash, surrounding whitespace).
    - scheme is None if the input had no scheme, or on hard failure.
    """
    if not domain or not domain.strip():
        return None, None, "Domain is required."
 
    raw = domain
    domain = domain.strip()
 
    if domain.startswith('//'):
        return None, None, "Domain cannot start with `//`"
 
    has_scheme = bool(_SCHEME_RE.match(domain))
    candidate = domain if has_scheme else '//' + domain
 
    try:
        p = urlparse(candidate)
    except ValueError:
        return None, None, "Domain is invalid"
 
    scheme = p.scheme or None
    if scheme and scheme not in ('http', 'https'):
        return None, None, f'Unsupported scheme "{scheme}"'
 
    netloc = p.netloc
 
    if '@' in netloc:
        return None, None, "Domain cannot include a username or password"
 
    host, sep, port = netloc.partition(':')
 
    # A doubled/nested scheme (e.g. "http://http://example.com") ends up
    # looking like a host of "http" with an empty/garbage port.
    if sep and not port.isdigit():
        return None, None, f'Domain "{raw}" contains a nested URL'
 
    if sep and not (0 < int(port) < 65536):
        return None, None, 'Domain port specifier is malformed'
 
    if not host:
        return None, None, f'Domain "{raw}" is invalid'
 
    labels = host.rstrip('.').split('.')
    if len(host) > 253 or not labels or not all(_LABEL_RE.match(l) for l in labels):
        return None, None, f'Domain "{raw}" is invalid'
 
    meaningful_changes = []
    if sep:
        meaningful_changes.append('port')
    if p.path not in ('', '/'):
        meaningful_changes.append('path')
    if p.query:
        meaningful_changes.append('query string')
    if p.fragment:
        meaningful_changes.append('fragment')
 
    if meaningful_changes:
        parts = ', '.join(meaningful_changes)
        return host, scheme, f'The {parts} in "{raw}" will be removed - is "{host}" correct?'
 
    return host, scheme, None

