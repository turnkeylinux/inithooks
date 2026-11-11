# v2.3.7 release notes

* Supply DNS and backup credentials through the existing Hub connection.
* Preserve existing service credentials during linking and package upgrades.
* Initialize available HubDNS automatically after linking, through its own command.
* Initialize backups when a backup or restore is requested.
* Complete package installation without opening an interactive linking dialog.
* Stop periodic service-readiness polling after upgrading the helper.
