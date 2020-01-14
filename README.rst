===================================================
System initialization, configuration and preseeding
===================================================

Introduction
============

Before we expose a TurnKey system to a hostile Internet, we first need
to initialize it. This will setup passwords, install security updates,
and configure key applications settings.

This initialization process can be interactive or non-interactive
depending on what works best given where and how the system is deployed.

Interactive system initialization
---------------------------------

A configuration wizard shows a short sequence of simple text dialogs
that look primitive but provide a quick step-by-step process that works
anywhere and requires only the bare minimum of software dependencies - a
big advantage for security sensitive applications:

.. image:: https://www.turnkeylinux.org/files/images/docs/inithooks/turnkey-init-root.png
   :alt: root password dialog
   :width: 650px

All software is potentially buggy but we can minimize the risk by
intentionally favoring simplicity over fancy eye candy.

The configuration dialogs run in one of two places:

1) **The boot console on first boot** on build types (e.g., ISO, VM,
   VMDK) where the real or virtual machine usually provides access to
   an interactive system console.

2) **The first administration login** on build types running on
   *headless* virtual machines (e.g., AWS marketplace, OpenStack,
   Xen).  that don't provide the option to interact with the system at
   boot time.
   
   After boot, a virtual fence redirects attempts to access
   potentially vulnerable services to a web page explaining how to SSH
   into the machine for the first time to initialize the system. After
   initialization the virtual fence comes down and all services can be
   accessed normally.

Non-interactive system initialization
-------------------------------------

The `TurnKey Hub`_ streamlines deployment by preseeding system
initialization settings with values the user provides before launching
an instance through the Hub's cloud deployment web app.

This means when the system boots for the first time it doesn't need to
interact with the user through text dialogs.

Preseeding is well documented and may be used by other hosting providers
or private clouds in a similar way to streamline deployment.

.. _TurnKey Hub: https://hub.turnkeylinux.org/

Under the hood: everything you wanted to know but were afraid to ask
====================================================================

Users wishing to preseed headless builds (e.g. LXC) will find the
`Preseeding`_ section below of value. Otherwise, the preceding introduction
explained everything mere mortals need to know about the system
initialization process.

The rest of the documentation is intended for:

- Appliance hackers interested in learning how TurnKey works under the
  hood and developing their own configuration hooks.

- Expert users who want to understand how system initialization works in
  depth.

- Hosting providers and private cloud fullstack ninjas interested in
  implementing tight integration between TurnKey and custom control
  panels. 

  This isn't a requirement, just a bonus. Without any special
  integration, TurnKey images can be deployed like any other Debian or
  Debian-based image, using your existing deployments scripts. If you
  can deploy Debian or Ubuntu it should be trivial to deploy TurnKey.

Inithooks package design goals
------------------------------

The inithooks package executes system initialization scripts
which:

- **Regenerate secret keys (e.g., SSH, default SSL certificate)**: This
  isn't just a good idea, it's necessary to avoid man in the middle
  attacks.

- **Set passwords (e.g., root, database, application)**: necessary to avoid the risk
  of `hardwired default passwords <http://www.turnkeylinux.org/blog/end-to-default-passwords>`_ 

- **Configure basic application settings (e.g., domain, admin email)**:
  especially useful when configuring the application would require hunting
  down the format of a configuration file.

Also, Inithooks provides a preseeding mechanism designed to make it easy
to integrate TurnKey with custom control panels provided by various
virtualization solutions and cloud hosting providers.

How it works
------------

Inithooks itself is as generic and barebones as possible, leaving
the bulk of functionality up to the appliance specific "hook"
scripts themselves,

These scripts are located in two sub-directories under
/usr/lib/inithooks - everyboot.d and firstboot.d. 

They are executed in alphanumeric ordering. This means a script named
1-foo would be executed before 2-bar, which would itself be executed
before 3-foobar. That's why scripts in these directories have funny
numbers at the beginning.  

The inithooks top-level init script is executed early on in system
initialization, at runlevel 2 15. This enables configuration of the
system prior to most services starting. This should be taken into
consideration when developing hook scripts.

firstboot.d scripts
'''''''''''''''''''

Scripts in the firstboot.d sub-directory are executed under the
following conditions:

#. If the user executes "turnkey-init" from a root shell. This command
   can be used to rerun the firstboot.d inithooks interactively to
   reconfigure the appliance if needed. Certain scripts such as those that
   regenerate secret keys are skipped. See BLACKLIST variable in
   /usr/sbin/turnkey-init for details.

#. When the user logs in as root for the first time into a headless
   system. This triggers "turnkey-init" to run so that the user can
   interactively complete appliance initialization.

#. When a TurnKey appliance boots for the first time

   inithooks checks whether or not this is the first boot by checking
   the value of the RUN\_FIRSTBOOT flag in /etc/default/inithooks. If
   the value is false it runs the scripts and toggles the flag to true.

   The firstboot scripts may run in one of two modes, interactive or
   non-interactive, depending on the type of build.

   **Interactive mode on non-headless builds - Live CD ISO, VMDK and
   OVF**: With these image types interactive access to the virtual
   console during boot is expected so some of the inithooks
   initialization scripts will interact with the user via text dialogs
   the first time the system boots (e.g., ask for passwords,
   application settings, etc.). These are the same scripts that get
   executed if you run "turnkey-init".

   **Non-interactive mode on headless builds - OpenStack, OpenVZ,
   OpenNode, Xen**: with these image types interactive access to the
   virtual console during boot can not be assumed.  The first boot has
   to be capable of running non-interactively, otherwise we risk
   hanging the boot while it waits for user interaction that never
   happens.
   
   So instead of interacting with the user the system pre-initializes
   application settings with dummy defaults and set all passwords to a
   random value. If a root password has already been set (e.g., in a
   pre-deployment script) the headless preseeding script will not
   overwrite it, so your root password should work just fine.
   
   The output from the non-interactive running of the firstboot
   scripts is logged to /var/log/inithooks.log.

   Interactive appliance configuration is delayed until the first time
   the user logs in as root. This is accomplished with the help of the
   /usr/lib/inithooks/firstboot.d/29preseed hook, which only exists on
   headless builds::

    #!/bin/bash -e
    # generic preseeding of inithooks.conf if it doesn't exist

    [ -e $INITHOOKS_CONF ] && exit 0

    MASTERPASS=$(mcookie | cut --bytes 1-8)

    cat>$INITHOOKS_CONF<<EOF
    export ROOT_PASS=$MASTERPASS
    export DB_PASS=$MASTERPASS
    export APP_PASS=$MASTERPASS
    export APP_EMAIL=admin@example.com
    export APP_DOMAIN=DEFAULT
    export HUB_APIKEY=SKIP
    export SEC_ALERTS=SKIP
    export SEC_UPDATES=FORCE
    EOF

    chmod +x /usr/lib/inithooks/firstboot.d/30turnkey-init-fence


   **Initialization fence**: the above headless preseeding hook also
   activates the "initialization fence" mechanism which uses iptables to
   redirect attempts to access the local web server to a static web page
   served by inithooks/bin/simplehttpd.py. 
   
   This page explains you need to log in as root first in order to
   finish initializing the system. The purpose of the fence is used to
   prevent users from accessing uninitialized web applications, which in
   some cases can pose a security risk.

   After the user logs in as root and completes the initialization
   process the "initialization fence" is turned off. Users can then
   access applications running on the local web server.

   What firstboot.d/30turnkey-init-fence does:
   
   1) enables turnkey-init-fence as a service and starts it

      service is enabled / disabled via update-rc.d

   2) activates ~$USERNAME/.profile.d/turnkey-init-fence

      the .profile.d script launches a dtach session bound to a socket

          if a session is already bound to the socket attach to it

          what command are we running in the dtach session?

                turnkey-init -> deactivate initfence (service and profile.d)

everyboot.d scripts
'''''''''''''''''''

Scripts that are in the everyboot.d sub-directory run on every boot. We
try to minimize the number of scripts that live here because they're
basically a poor man's init script and real init scripts are often a
better idea.

Setting the root password in a headless deployment
--------------------------------------------------

On headless deployments the user needs to login as root to complete the
appliance initialization process, but how do you login as root?

Not a problem if you're using OpenNode or ProxMox - those systems
prompt you to choose a root password before deploying a TurnKey image.

On OpenStack you can log in as root with your configured SSH keypair
or retrieve the random root password from the "system log". 

Other virtualization / private cloud solutions should be able to use
their existing deployment scripts to set the root password, just like
they already do with Debian and Ubuntu.

Another more advanced option is to "preseed" the /etc/inithooks.conf
file in the apliance's filesystem before booting it for the first
time. This lets you leverage inithooks to pre-configure not just the
root password but also the database and application passwords, admin
email, domain name, etc.

However note that using preseeding deactivates the "initilization
fence". If you're using preseeding TurnKey assumes you've already
interacted with the user some other way (e.g., web control panel) to
get the preseeded configuration values.

Preseeding
----------

By default, when an appliance is run for the first time, the firstboot
scripts will prompt the user interactively, through the virtual
console, to choose various passwords and basic application
configuration settings. 

It is possible to bypass this interactive configuration process by
creating /etc/inithooks.conf in the appliance filesystem and
writing inithooks configuration variables into it before the
first system boot. For example:
::

    cat>/etc/inithooks.conf<<EOF
    export ROOT_PASS=supersecretrootpass
    export DB_PASS=supersecretmysqlpass
    export APP_EMAIL=admin@example.com
    export APP_PASS=webappadminpassword
    export SEC_ALERTS=admin@example.com
    export SEC_UPDATES=FORCE
    export HUB_APIKEY=SKIP
    EOF

Don't worry about leaving sensitive passwords in there: after the
first boot, inithooks blanks /etc/inithooks.conf out so important
passwords aren't accidentally left in the clear.

This preseeding mechanism makes it relatively easy to integrate TurnKey
with custom control panels, virtualization solutions, etc.

How exactly you create /etc/inithooks.conf is up to you and the
capabilities of the virtualization platform you are using. For example,
many virtualization platforms provide a facility through which you can
run scripts or add files to the filesystem before the first boot.

List of initialization hooks and preseeding configuration parameters
--------------------------------------------------------------------

Below is a list of firstboot hooks. All interactive hooks
have preseeding options to support cloud deployment, hosting and ISV
integration.

If not preseeded, the user will be asked interactively. The SKIP
and FORCE options should be self explanatory. Note that secupdates
is automatically skipped when in live demo mode.

Most inithooks that are configurable are interactive, however not all.
Non-interactive hooks that can be adjusted via preseeding are marked
below with an asterisk ('*').

Note that almost all appliances have their own application specific
secret-regeneration hooks which will run regardless. 

Common to all appliances:
::

    15regen-sslcert         DH_BITS                 [ 1024 | 2048 | 4096 ]
    30rootpass              ROOT_PASS
    50auto-apt-archive      AUTO_APT_ARCHIVE        [ SKIP ]
    80tklbam                HUB_APIKEY              [ SKIP ]
    85secalerts             SEC_ALERTS              [ SKIP ]
    92etckeeper*            ETCKEEPER_COMMIT        [ SKIP ]
    95secupdates            SEC_UPDATES             [ SKIP | FORCE ]


Notes:

    - DH_BITS refers to the number of bits used when generating Diffie-Hellman
      parameters used in TLS (i.e. HTTPS) _`Diffie-Hellman key exchange`. 2048
      is recommended but can be slow to generate, particularly on low resource
      servers. 4096 is another option but may take hours. 1024 is default (so
      firstboot isn't too slow...). Note this one doesn't have an interactive
      counterpart at the moment, but can be re-run from the commandline::

            export DH_BITS=2048 # or alternatively DH_BITS=4096
            /usr/lib/inithooks/firstboot.d/15regen-dhparams

    - ETCKEEPER_COMMIT refers to whether (or not) etckeeper commits the current
      state of /etc. If not set it will be.


Specific to headless builds:
::

    29preseed               INITFENCE               [ SKIP ]

Appliance specific:
::

    35mysqlpass             DB_PASS
    35pgsqlpass             DB_PASS

    40ansible               APP_PASS
    40couchdb               APP_PASS
    40espocrm               APP_PASS
    40etherpad              APP_PASS
    40githttp               APP_PASS
    40icesecretset          APP_PASS
    40jenkins               APP_PASS
    40mediawiki             APP_PASS
    40mibew                 APP_PASS
    40mongodb               APP_PASS
    40moodle                APP_PASS
    40mumblesupw            APP_PASS
    40observium             APP_PASS
    40odoo                  APP_PASS
    40openvas               APP_PASS
    40orangehrm             APP_PASS
    40otrs                  APP_PASS
    40phpmumbleadmin        APP_PASS
    40plone                 APP_PASS
    40sugarcrm              APP_PASS
    40suitecrm              APP_PASS
    40torrentserver         APP_PASS
    40trac                  APP_PASS
    40typo3                 APP_PASS
    40zoneminder            APP_PASS
    40redis                 APP_PASS [, APP_IP_BIND, APP_PROTECTED]
    40nextcloud             APP_PASS, APP_DOMAIN
    40openldap              APP_PASS, APP_DOMAIN
    40owncloud              APP_PASS, APP_DOMAIN
    40zurmo                 APP_PASS, APP_DOMAIN
    40domain-controller     APP_PASS, APP_DOMAIN [, APP_REALM, APP_JOIN, APP_JOIN_NS]]
    40b2evolution           APP_PASS, APP_EMAIL
    40collabtive            APP_PASS, APP_EMAIL
    40concrete5             APP_PASS, APP_EMAIL
    40django                APP_PASS, APP_EMAIL
    40dokuwiki              APP_PASS, APP_EMAIL
    40drupal7               APP_PASS, APP_EMAIL
    40e107                  APP_PASS, APP_EMAIL
    40ezplatform            APP_PASS, APP_EMAIL
    40foodsoft              APP_PASS, APP_EMAIL
    40gallery               APP_PASS, APP_EMAIL
    40joomla                APP_PASS, APP_EMAIL
    40kliqqi                APP_PASS, APP_EMAIL
    40limesurvey            APP_PASS, APP_EMAIL
    40mahara                APP_PASS, APP_EMAIL
    40mambo                 APP_PASS, APP_EMAIL
    40mantis                APP_PASS, APP_EMAIL
    40mattermost            APP_PASS, APP_EMAIL
    40mayan                 APP_PASS, APP_EMAIL
    40moinmoin              APP_PASS, APP_EMAIL
    40omeka                 APP_PASS, APP_EMAIL
    40oscommerce            APP_PASS, APP_EMAIL
    40phpbb                 APP_PASS, APP_EMAIL
    40processmaker          APP_PASS, APP_EMAIL
    40redmine               APP_PASS, APP_EMAIL
    40roundup               APP_PASS, APP_EMAIL
    40silverstripe          APP_PASS, APP_EMAIL
    40simpleinvoices        APP_PASS, APP_EMAIL
    40sitracker             APP_PASS, APP_EMAIL
    40twiki                 APP_PASS, APP_EMAIL
    40ushahidi              APP_PASS, APP_EMAIL
    40vanilla               APP_PASS, APP_EMAIL
    40vtiger                APP_PASS, APP_EMAIL
    40wordpress             APP_PASS, APP_EMAIL
    40xoops                 APP_PASS, APP_EMAIL
    40canvas                APP_PASS, APP_EMAIL, APP_DOMAIN
    40drupal8               APP_PASS, APP_EMAIL, APP_DOMAIN
    40elgg                  APP_PASS, APP_EMAIL, APP_DOMAIN
    40foswiki               APP_PASS, APP_EMAIL, APP_DOMAIN
    40gitlab                APP_PASS, APP_EMAIL, APP_DOMAIN
    40gnusocial             APP_PASS, APP_EMAIL, APP_DOMAIN
    40icescrum              APP_PASS, APP_EMAIL, APP_DOMAIN
    40matomo                APP_PASS, APP_EMAIL, APP_DOMAIN
    40phplist               APP_PASS, APP_EMAIL, APP_DOMAIN
    40opencart              APP_PASS, APP_EMAIL, APP_DOMAIN
    40prestashop            APP_PASS, APP_EMAIL, APP_DOMAIN
    40punbb                 APP_PASS, APP_EMAIL, APP_DOMAIN
    40simplemachines        APP_PASS, APP_EMAIL, APP_DOMAIN
    40zencart               APP_PASS, APP_EMAIL, APP_DOMAIN
    40magento               APP_PASS, APP_EMAIL, APP_DOMAIN [, APP_PRIVKEY, APP_PUBKEY]
    40bugzilla              APP_PASS, APP_EMAIL [, APP_OUTMAIL]
    40ghost                 APP_PASS, APP_EMAIL, APP_DOMAIN [, APP_UNAME]

Fileserver appliance specific - LXC only:
::

    35samba-container       APP_PASS

Linux and Samba user management is separate and discrete. Previously by default
Samba users were mapped 1-1 with Linux users and Samba supported syncronization
of passwords between the Linux and Samba users (so essentially the difference
between the 2 user management systems was hidden from the end user). However
due to a significant security issue, this module has been removed. Samba4 has
moved to prioritize support for AD integration (which uses a different
paradigm - all Samba users are contained within a single Linux user account).

To somewhat work around this limitation, on the TurnKey Fileserver appliance,
when you set the root (Linux) user password, the Samba root user password is
also set. However for an LXC container, the root password is set on the host,
not the guest. So this workaround is not possible. Hence the Samba root
password must be set separately.


Development notes
-----------------

So you're creating a new appliance and want to add initialization
hooks. Awesome! Here are some examples to get you going.
 
Non-interactive inithook
''''''''''''''''''''''''

The following example is used in the Joomla3 appliance. It
regenerates the *secret*, and sets a random mysql password for the
joomla user.
 
::

    /usr/lib/inithooks/firstboot.d/20regen-joomla-secrets
    
    #!/bin/bash -e
    # regenerate joomla secret key and mysql password
    
    . /etc/default/inithooks
    
    updateconf() {
        CONF=/var/www/joomla/configuration.php
        sed -i "s/var $1 = \(.*\)/var $1 = '$2';/" $CONF
    }
    
    updateconf '\$secret' $(mcookie)$(mcookie)
    
    PASSWORD=$(mcookie)
    updateconf '\$password' $PASSWORD
    
    $INITHOOKS_PATH/bin/mysqlconf.py --user=joomla --pass="$PASSWORD"

 
Interactive inithook
''''''''''''''''''''

The following example is used to set the root password in
all appliances. If ROOTPASS is not set, the user will be asked to
enter a password interactively.
 
::

    /usr/lib/inithooks/firstboot.d/30rootpass
    
    #!/bin/bash -e
    # set root password
    
    . /etc/default/inithooks
    
    [ -e $INITHOOKS_CONF ] && . $INITHOOKS_CONF
    $INITHOOKS_PATH/bin/setpasspass.py root --pass="$ROOTPASS"

 
::

    /usr/lib/inithooks/bin/setpass.py
    
    #!/usr/bin/python3
    # Copyright (c) 2010 Alon Swartz <alon@turnkeylinux.org>
    """Set account password
    
    Arguments:
        username      username of account to set password for
    
    Options:
        -p --pass=    if not provided, will ask interactively
    """
    
    import sys
    import getopt
    import subprocess
    from subprocess import PIPE
    
    from dialog_wrapper import Dialog
    
    def fatal(s):
        print >> sys.stderr, "Error:", s
        sys.exit(1)
    
    def usage(e=None):
        if e:
            print >> sys.stderr, "Error:", e
        print >> sys.stderr, "Syntax: %s <username> [options]" % sys.argv[0]
        print >> sys.stderr, __doc__
        sys.exit(1)
    
    def main():
        try:
            opts, args = getopt.gnu_getopt(sys.argv[1:], "hp:", ['help', 'pass='])
        except getopt.GetoptError, e:
            usage(e)
    
        if len(args) != 1:
            usage()
    
        username = args[0]
        password = ""
        for opt, val in opts:
            if opt in ('-h', '--help'):
                usage()
            elif opt in ('-p', '--pass'):
                password = val
    
        if not password:
            d = Dialog('TurnKey GNU/Linux - First boot configuration')
            password = d.get_password(
                "%s Password" % username.capitalize(),
                "Please enter new password for the %s account." % username)
    
        command = ["chpasswd"]
        input = ":".join([username, password])
        
        p = subprocess.Popen(command, stdin=PIPE, shell=False)
        p.stdin.write(input)
        p.stdin.close()
        err = p.wait()
        if err:
            fatal(err)
    
    if __name__ == "__main__":
        main()

.. _Diffie-Hellman key exchange: https://en.wikipedia.org/wiki/Diffie-Hellman_key_exchange
