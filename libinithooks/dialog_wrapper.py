# Copyright (c) 2010 Alon Swartz <alon@turnkeylinux.org>
# Copyright (c) 2020-2025 TurnKey GNU/Linux <admin@turnkeylinux.org>
import re
import sys
import dialog
import secrets
import string
import traceback
from io import StringIO
from os import environ
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


def generate_password(length: int = 20) -> str:
    """Generate a cryptographically secure random password.

    Uses the secrets module (CSPRNG). Guarantees at least one character
    from each of the 4 complexity categories (uppercase, lowercase,
    digit, symbol). Avoids shell-problematic characters.
    """
    if length < 12:
        length = 12

    uppercase = string.ascii_uppercase
    lowercase = string.ascii_lowercase
    digits = string.digits
    symbols = "!@#%^&*_+-=?"

    required = [
        secrets.choice(uppercase),
        secrets.choice(lowercase),
        secrets.choice(digits),
        secrets.choice(symbols),
    ]

    all_chars = uppercase + lowercase + digits + symbols
    remaining = [secrets.choice(all_chars) for _ in range(length - len(required))]

    chars = required + remaining
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]

    return "".join(chars)


class Dialog:
    def __init__(self, title: str, width: int = 60, height: int = 20) -> None:
        self.width = width
        self.height = height

        self.console = dialog.Dialog(dialog="dialog")
        self.console.add_persistent_args(["--no-collapse"])
        self.console.add_persistent_args(["--backtitle", title])
        self.console.add_persistent_args(["--no-mouse"])
        self.console.add_persistent_args(["--colors"])

    def _handle_exitcode(self, retcode: int) -> bool:
        logging.debug(f"_handle_exitcode(retcode={retcode!r})")
        if retcode == self.console.ESC:
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
    ) -> str | tuple[str, str]:
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
                return_value = method("\n" + text, *args, **kws)
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

        return return_value

    def error(self, text: str) -> str:
        """'Error' titled message with single 'ok' button
        Returns 'Ok'"""
        height = self._calc_height(text)
        return str(
            self.wrapper("msgbox", text, height, self.width, title="Error"),
        )

    def msgbox(self, title: str, text: str) -> str:
        """Titled message with single 'ok' button
        Returns 'Ok'"""
        height = self._calc_height(text)
        logging.debug(f"msgbox(title={title!r}, text=<redacted>)")
        return str(
            self.wrapper("msgbox", text, height, self.width, title=title),
        )

    def infobox(self, text: str) -> str:
        """Untitled message with single 'ok' button
        Returns 'Ok'"""
        height = self._calc_height(text)
        logging.debug(f"infobox(text={text!r}")
        return str(
            self.wrapper("infobox", text, height, self.width),
        )

    def inputbox(
        self,
        title: str,
        text: str,
        init: str = "",
        ok_label: str = "OK",
        cancel_label: str = "Cancel",
    ) -> tuple[str, str]:
        """Titled message with text input and single choice of 2 buttons
        Returns tuple of 'ok'/'cancel' & the input string"""
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
        return_tuple = self.wrapper(
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
        assert isinstance(return_tuple, tuple)
        return return_tuple

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
        choices: list[tuple[str, str]],
    ) -> str:
        """Titled message with single choice of options & 'ok' button.
        choices is a list of options, each option is a tuple of option tag and
        option (short) description
        Returns selected option tag"""
        return_tuple = self.wrapper(
            "menu",
            text,
            self.height,
            self.width,
            menu_height=len(choices) + 1,
            title=title,
            choices=choices,
            no_cancel=True,
        )
        assert isinstance(return_tuple, tuple)
        return return_tuple[0]

    def get_password(
        self,
        title: str,
        text: str,
        pass_req: int = 8,
        min_complexity: int = 3,
        blacklist: list[str] | None = None,
        offer_generate: bool = True,
        gen_length: int = 20,
    ) -> str | None:
        """Validated password input with optional auto-generate.

        When offer_generate is True (default), presents a menu first:
          - Generate: creates a strong random password, shows it to
            the user, and asks for confirmation.
          - Manual: traditional password input with complexity check.

        Fully backward compatible: existing calls without the new
        parameters get the generate option automatically. Pass
        offer_generate=False for the original behavior.

        Returns password"""
        if offer_generate:
            choice = self.menu(
                title,
                f"{text}\n\nChoose how to set this password:",
                [
                    ("Generate", "Strong random password (recommended)"),
                    ("Manual", "Type my own password"),
                ],
            )
            if choice == "Generate":
                return self._generate_password_flow(title, gen_length)

        return self._manual_password_flow(
            title, text, pass_req, min_complexity, blacklist
        )

    def _generate_password_flow(
        self, title: str, length: int = 20
    ) -> str:
        """Generate a strong password and show it to the user.

        Displays the password in a highlighted reverse-video box,
        centered within the dialog, with a bold red warning.

        Returns password.
        """
        while True:
            password = generate_password(length)

            # Dialog content width is roughly self.width - 6
            content_width = self.width - 6

            # Build reverse-video box
            box_width = max(len(password) + 8, 36)
            pw_pad_left = (box_width - len(password)) // 2
            pw_pad_right = box_width - len(password) - pw_pad_left
            empty_line = " " * box_width
            pw_line = " " * pw_pad_left + password + " " * pw_pad_right

            # Center the box within content area
            box_margin = max((content_width - box_width - 4) // 2, 0)
            margin = " " * box_margin

            # Center the title and warning
            title_text = "Your generated password:"
            title_pad = max((content_width - len(title_text)) // 2, 0)

            warning = ">>> SAVE THIS PASSWORD NOW <<<"
            warn_pad = max((content_width - len(warning)) // 2, 0)

            note1 = "It will NOT be shown again."
            note1_pad = max((content_width - len(note1) - 2) // 2, 0)

            note2 = "Store it in a password manager."
            note2_pad = max((content_width - len(note2)) // 2, 0)

            text = (
                f"\n{' ' * title_pad}\ZbYour generated password:\Zn\n\n"
                f"{margin}\Zb\Zr  {empty_line}  \Zn\n"
                f"{margin}\Zb\Zr  {pw_line}  \Zn\n"
                f"{margin}\Zb\Zr  {empty_line}  \Zn\n\n"
                f"{' ' * warn_pad}\Zb\Z1{warning}\Zn\n\n"
                f"{' ' * note1_pad}It will \ZbNOT\Zn be shown again.\n"
                f"{' ' * note2_pad}Store it in a password manager."
            )

            height = 18
            width = max(self.width, box_width + 16)
            self.wrapper("msgbox", text, height, width, title=title)

            # Confirmation dialog
            q_text = "Did you save this password?"
            q_pad = max((content_width - len(q_text)) // 2, 0)

            hint = "'Saved' = continue     'New' = generate another"
            hint_pad = max((content_width - len(hint)) // 2, 0)

            confirm_text = (
                f"\n{' ' * q_pad}Did you save this password?\n\n"
                f"{margin}\Zb\Zr  {empty_line}  \Zn\n"
                f"{margin}\Zb\Zr  {pw_line}  \Zn\n"
                f"{margin}\Zb\Zr  {empty_line}  \Zn\n\n"
                f"{' ' * hint_pad}\Zb\Z2Saved\Zn = continue"
                f"     \Zb\Z1New\Zn = generate another"
            )

            confirmed = self.yesno(
                "Confirm", confirm_text,
                yes_label="Saved", no_label="New",
            )

            if confirmed:
                return password

    def _manual_password_flow(
        self,
        title: str,
        text: str,
        pass_req: int = 8,
        min_complexity: int = 3,
        blacklist: list[str] | None = None,
    ) -> str | None:
        """Original manual password entry with validation.

        Titled message with password (redacted input) box & 'ok' button.
        Password is validated against defined rules (pass_req, min_complexity &
        blacklist). Method will loop until deemed valid.
        """
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
        """Validated input box (email) with optional prefilled value and 'Ok'
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

    def get_input(self, title: str, text: str, init: str = "") -> str | None:
        """Input box within optional prefilled value & 'Ok' button
        Returns input"""
        while 1:
            s = self.inputbox(title, text, init, "Apply", "")[1]
            if not s:
                self.error(f"{title} is required.")
                continue
            return s
