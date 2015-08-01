#!/bin/bash -e

fatal() { echo "fatal [$(basename $0)]: $@" 1>&2; exit 1; }
info() { echo "info [$(basename $0)]: $@"; }

usage() {
cat<<EOF
Syntax: $(basename $0) email
Configure security alert emails
EOF
exit 1
}

configure_alias() {
    info $FUNCNAME $@
    user=$1
    email=$2
    cfg=/etc/aliases
    touch $cfg
    grep -q ^${user}: $cfg || (echo "$user:    $email" >> $cfg; return)
    sed -i "s/${user}:.*/${user}:    $email/" $cfg
    newaliases
}

configure_cronapt() {
    info $FUNCNAME $@
    key=$1
    val=$2
    cfg="/etc/cron-apt/config"
    [ -e $cfg ] || return
    grep -q ^${key}= $cfg || (echo "$key=\"$val\"" >> $cfg; return)
    sed -i "s/^${key}=.*/$key=\"$val\"/" $cfg
}

send_enabled_notification() {
    info $FUNCNAME $@
    recipient=$1
    subject="[$(hostname)] system alerts and notifications enabled"
    mail -s "$subject" $recipient <<EOF
This server is configured to send you system alerts and notifications.
For more information, see:
http://www.turnkeylinux.org/security-alerts

--
$(turnkey-version)
EOF
}

if [[ "$#" != "1" ]]; then
    usage
fi

email=$1

configure_alias "root" "$email"
configure_cronapt "MAILON" "output"
configure_cronapt "MAILTO" "root"
send_enabled_notification "root"

