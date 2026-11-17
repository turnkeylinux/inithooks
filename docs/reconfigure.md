# Reconfigure

Open **Reconfigure** in Confconsole's advanced menu, or run:

```sh
sudo turnkey reconfigure
```

Select a setting to open its existing initialization dialog. **Details** shows
its complete description. Each entry can include several settings, such as an
app's administrator password, email and domain. Completing or cancelling an
entry returns to the menu. Finish initial setup before using Reconfigure.

At a new-password prompt, leave the field empty to generate a strong password.
Save the generated value when it is displayed. Reconfigure offers the settings
supported by the installed hooks; the generated password is displayed once.

## Hook annotations

Reconfigure discovers files in `/usr/lib/inithooks/firstboot.d/` when the menu
opens. New executable hooks appear by default. Add these opening comments
to describe an entry:

```sh
#!/bin/bash -e
# reconfigure: yes
# reconfigure-title: WordPress
# reconfigure-description: Change the administrator password, email and site address.
```

Use `# reconfigure: no` for initialization-only operations and hooks that require
other first-boot steps. Put annotations before executable statements. Explicit
annotations take precedence over the package's compatibility catalog. Malformed
explicit eligibility values hide the entry until corrected. An explicit `yes`
also permits a non-executable script with a shebang to run through its declared
interpreter. This preserves container policies that disable password hooks during
first boot while permitting an administrator to reconfigure them later.

Without metadata, the title comes from the filename with leading digits removed,
dashes and underscores converted to spaces, and words capitalized. The first
opening descriptive comment supplies the description. Copyright and tooling
comments are skipped. A missing description leaves a title-only entry.

The selected hook runs as root with `_TURNKEY_INIT=1` and
`_TURNKEY_RECONFIGURE=1`. It receives a clean environment containing terminal and
locale settings. Its existing initialization defaults still apply. The system
password hook uses the explicit reconfiguration flag to permit a password
change on containers whose first-boot password is normally set by the host.

## Existing images

The inithooks package supplies the menu, command and a compatibility catalog for
older external hooks. This provides reviewed descriptions and exclusions on
existing servers through a package upgrade. Packaged first-boot hooks carry
their annotations directly. The matching turnkey-hub metapackage must accompany
an upgrade when it is installed, because it depends on the exact inithooks version.

Refresh the external catalog from the maintained sources after annotating hooks:

```sh
tools/update-reconfigure-legacy /path/to/common/overlays \
    /path/to/turnkeylinux-apps /path/to/buildtasks/patches
```

Inspect the discovered entries without executing a hook:

```sh
turnkey-reconfigure --list
```

The shared password dialog accepts `generate=False` for existing-credential
prompts and `generate=True` for new passwords. Compatibility with older Samba
domain-joining prompts disables generation for `domain-controller.py` unless
the caller explicitly enables it.
