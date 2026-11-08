# Link an appliance to TurnKey Hub

Hub-launched v19 appliances connect automatically. On an appliance installed elsewhere, choose **Link** during firstboot or **Advanced → Mail relaying → Mail relay → Link to TurnKey Hub** in Confconsole. Open https://hub.turnkeylinux.org/link on another device, sign in or create a free Hub account, and enter the displayed code, such as `D34C-F5A9`.

A free account includes a tklapp.com hostname, dynamic DNS and app email delivery. Paid accounts can use a custom sending domain after DNS verification. Backup setup is independent of these services.

Apps that use the system mail service share this connection. Their sender addresses stay unchanged when they use the approved sending domain; other sender addresses use the appliance's default sender. Existing Reply-To addresses and recipients are preserved. Use an existing mailbox for replies.

From a root shell:

```sh
/usr/lib/inithooks/bin/turnkey-mail connect
/usr/lib/inithooks/bin/turnkey-mail status
/usr/lib/inithooks/bin/turnkey-mail test --recipient you@example.com
```

A successful test command means the local queue accepted the message. Check the recipient inbox to confirm delivery. While domain verification or the provider is temporarily unavailable, messages remain queued.

Confconsole also supports another SMTP provider through **Custom SMTP**. Enter the host, port, username and password. Port 587 uses STARTTLS; port 465 uses implicit TLS. The server's certificate is verified before settings change. Existing settings are restored if applying the new configuration fails.

**Reset mail** restores the previous local mail settings while preserving the Hub link and dynamic DNS. Revoke its credential on the Hub email page if you also want to prevent another copy from using that credential.

## Operations and integration

The standalone `bin/turnkey-mail` file is packaged by inithooks. The Hub installs the same reviewed file on supported existing images and passes enrollment through standard input. It is independent of application code.

Enrollment saves a short-lived token and independently generated SMTP/DNS secrets under `/var/lib/turnkey-mail/`, accessible only to root. The retry timer resumes an incomplete connection after firstboot clears its preseed file. A new connection with no existing relay holds direct SMTP delivery until enrollment succeeds. Repeating a completed token preserves the existing device identity.

The DNS updater uses a separate hostname-scoped capability and leaves an enabled existing HubDNS hourly updater in control. The mail credential cannot update DNS. Connection state and credentials must be reset when cloning a server; the Hub snapshot path invokes `turnkey-mail reset` before enrollment with a new token. The full `reset` command clears local mail and DNS credentials without revoking the source server's credential. Use `reset-mail` for the mail-only action. Switching to Custom SMTP also preserves dynamic DNS.

Custom automation can pipe a JSON object to `turnkey-mail configure --stdin`. Its shape is `{"smtp":{"host":"smtp.example.com","port":587,"username":"user"},"password":"..."}`. Obtain the password from protected input rather than placing it in command arguments or shell history.

Validation uses the full `python3 -m unittest discover -s tests -v` suite and the isolated real-Postfix fixture. No application-specific SMTP plugin is required.
