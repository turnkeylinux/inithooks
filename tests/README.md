# Appliance mail validation

Run the complete suite with `python3 -m unittest discover -s tests -v`.
The helper tests use isolated directories and mocked service commands.

`python3 tests/mail_postfix_maps.py` exercises sender maps against the real
Postfix regexp engine using temporary map files. It does not change Postfix
configuration.

`tests/mail_postfix_integration.py` is destructive and belongs only in a
disposable client container with Postfix and SMTP SASL modules installed.
Provide the shared helper path and a root-only relay credential JSON file.
Install the fixture relay certificate in system trust first, and set
`TURNKEY_MAIL_DISPOSABLE_FIXTURE=yes`. The script exercises real configuration,
map permissions, authentication, rollback, queue submission and local reset.
Its queue assertions need a reachable fixture relay that accepts the messages;
inspect that relay's provider fixture output to verify sender and recipients.

The firstboot terminal audit uses the real dialog implementation with fixture
responses. Install pexpect and pyte in the test environment, then run:

```sh
python3 tests/exercise_hub_services_terminal.py /path/to/evidence
```

Inspect the captured terminal screens at 60, 80 and 120 columns. These rendered
screens supplement the interaction assertions and do not prove a live Hub link.

Version 2 link tests cover discovery before SMTP-secret registration, stable
retry secrets, manual mail preference across synchronization and restart,
independent custom sender/DNS state, backup authentication conversion and failed
initialization retries. The helper retains existing backup encryption/history
and explicit legacy initialization paths.

`turnkey-mail sync` updates approved services. `use-hub` resumes Hub mail without
pairing, while `reset-mail` selects manual mail and preserves DNS and backup.
Actual installed-client backup and filesystem restore proof is coordinated with
the Hub fixtures; keep registry/config/profile data in dedicated QA directories.
