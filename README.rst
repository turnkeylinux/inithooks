=======================================================================================
Initialization Hooks (inithooks) - TurnKey initialization, configuration and preseeding
=======================================================================================

The intended audience for this page are:

- Hosting providers and private cloud operators interested in
  integrating TurnKey with their virtualization / private cloud system.

- Appliance developers interested in how TurnKey works under the hood.

Inithook design goals
---------------------

The inithooks package executes system initialization scripts
which:

- **Regenerate secret keys (e.g., SSH, default SSL certificate)**:
  necessary to avoid man in the middle attacks

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

They are  executed in alphanumeric ordering.  This means a script named
1-foo would be executed before 2-bar, which would itself be executed
before 3-foobar.  That's why scripts in these directories have funny
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
   OVF**: With these image types interactive access to the virtual console
   during boot is expected so some of the inithooks initialization
   scripts will interact with the user via text dialogs the first time
   the system boots (e.g., ask for passwords, application settings,
   etc.). These are the same scripts that get executed if you run
   "turnkey-init".

   **Non-interactive mode on headless builds - OpenStack, OpenVZ,
   OpenNode, Xen**: with these image types interactive access to the
   virtual console during boot can not be assumed.  The first boot has
   to be capable of running non-interactively, otherwise we would risk
   hanging the boot while it waits for user interaction that never
   happens.
   
   So instead of interacting with the user the system pre-initializes
   application settings with dummy defaults and set all passwords to a
   random value.  The output from the non-interactive running of the
   firstboot scripts is logged to /var/log/inithooks.log.

   Interactive appliance configuration is delayed until the first time
   the user logs in as root. This is accomplished with the help of the
   /usr/lib/inithooks/firstboot.d/29preseed hook, which only exists on
   headless builds::

        #!/bin/bash -e
        # generic preseeding of inithooks.conf if it doesn't exist

        # activate the initfence if it hasn't been explicitly turned off
        if ! [ -e $INITHOOKS_CONF ] || ! grep -i -q "INITFENCE=SKIP" $INITHOOKS_CONF; then
            chmod +x /usr/lib/inithooks/firstboot.d/30turnkey-init-firstlogin
        fi

        [ -e $INITHOOKS_CONF ] && exit 0

        MASTERPASS=$(mcookie | cut --bytes 1-8)

        cat>$INITHOOKS_CONF<<EOF
        export ROOT_PASS=$MASTERPASS
        export DB_PASS=$MASTERPASS
        export APP_PASS=$MASTERPASS
        export APP_EMAIL=admin@example.com
        export APP_DOMAIN=DEFAULT
        export HUB_APIKEY=SKIP
        export SEC_UPDATES=FORCE
        EOF

   **Initialization fence**: the above headless preseeding hook also activates the
   "initialization fence" mechanism. It uses iptables to
   redirect attempts to access the local web server to a static web page
   which explains that you need to log in as root first in order to
   finish initializing the system. The fence is used to prevent users
   from accessing possibly insecure uninitialized web ap, possibly insecure 

   After the user logs in as root and completes the initialization
   process the "initialization fence" is turned off so that users can
   access applications running on the local web server.

everyboot.d scripts
'''''''''''''''''''

Scripts that are in the everyboot.d sub-directory run on every boot. We
try to minimize the number of scripts that live here because they're
basically a poor man's init script and real init scripts are often a
better idea.

Setting the root password in a headless deployment
--------------------------------------------------

On headless deployments the user needs to login as root to complete the
appliance initialization process, but how do you login as root if you
don't know the random root password?

On OpenStack you can log in with your configured SSH keypair or retrieve
the random root password from the "system log". 

If you're using ProxMox or OpenNode you're in luck because they are
already integrated with TurnKey and let you choose your root password.

Other virtualization / private cloud solutions will need to be
configured to "preseed" a /etc/inithooks.conf file in the appliance's
filesystem before booting it for the first time. See below for
additional details on the preseeding mechanism.

Preseeding
----------

By default, when an appliance is run for the first time, the firstboot
scripts will prompt the user interactively, through the virtual console,
to choose various passwords and basic application configuration
settings. This is what happens in the non-headless builds (e.g., ISO,
VMDK, OVF).
 
It is possible to bypass this interactive configuration process by
creating /etc/inithooks.conf in the appliance filesystem and
writing inithooks configuration variables into it before inithooks
runs for the first time. For example:
::

    cat>/etc/inithooks.conf<<EOF
    HUB_APIKEY=SKIP
    ROOT_PASS=supersecretrootpass
    DB_PASS=supersecretmysqlpass
    APP_EMAIL=admin@example.com
    APP_PASS=webappadminpassword
    EOF

Don't worry after the first boot, inithooks blanks out /etc/inithooks.conf so
important passwords aren't left in the clear.

This preseeding mechanism makes it relatively easy to integrate TurnKey
with custom control panels, virtualization solutions, etc.

How exactly you create /etc/inithooks.conf is up to you and the
capabilities of the virtualization platform you are using. For example,
many virtualization platforms provide a facility through which you can
run scripts or add files to the filesystem before the first boot .

List of initialization hooks and preseeding configuration parameters
--------------------------------------------------------------------

Below is a list of interactive firstboot hooks. All interactive hooks
have preseeding options to support cloud deployment, hosting and ISV
integration.

Note that almost all appliances have their own application specific
secret-regeneration hooks. 
 
Common to all appliances:
::

    30rootpass              ROOT_PASS
    50auto-apt-archive      AUTO_APT_ARCHIVE        [ SKIP ]
    80tklbam                HUB_APIKEY              [ SKIP ]
    92etckeeper             ETCKEEPER_COMMIT        [ SKIP ]
    95secupdates            SEC_UPDATES             [ SKIP | FORCE ]

Specific to headless builds

    29preseed               INITFENCE               [ SKIP ]

Appliance specific:
::

    35mysqlpass             DB_PASS
    35pgsqlpass             DB_PASS
    
    40mldonkey              APP_PASS
    40fileserver            APP_PASS
    40moodle                APP_PASS
    40mediawiki             APP_PASS
    40trac                  APP_PASS
    40otrs                  APP_PASS
    40tomcat                APP_PASS
    40wordpress             APP_PASS, APP_EMAIL
    40bugzilla              APP_PASS, APP_EMAIL
    40joomla                APP_PASS, APP_EMAIL
    40mantis                APP_PASS, APP_EMAIL
    40gallery               APP_PASS, APP_EMAIL
    40deki                  APP_PASS, APP_EMAIL
    40django                APP_PASS, APP_EMAIL
    40dokuwiki              APP_PASS, APP_EMAIL
    40moinmoin              APP_PASS, APP_EMAIL
    40roundup               APP_PASS, APP_EMAIL
    40redmine               APP_PASS, APP_EMAIL
    40phpbb                 APP_PASS, APP_EMAIL
    40twiki                 APP_PASS, APP_EMAIL
    40vtiger                APP_PASS, APP_EMAIL
    40prestashop            APP_PASS, APP_EMAIL
    40magento               APP_PASS, APP_EMAIL, APP_DOMAIN
    40statusnet             APP_PASS, APP_EMAIL, APP_DOMAIN
    40ejabberd              APP_PASS, APP_DOMAIN
    40domain-controller     APP_PASS, APP_DOMAIN

 
If not preseeded, the user will be asked interactively. The SKIP
and FORCE options should be self explanatory. Note that secupdates
is automatically skipped when in live demo mode.
 
Development notes
-----------------

So you're creating a new appliance and want to add initialization
hooks. Awesome! Here are some examples to get you going.
 
Non-interactive inithook
''''''''''''''''''''''''

The following example is used in the Joomla15 appliance. It
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
    
    #!/usr/bin/python
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
            d = Dialog('TurnKey Linux - First boot configuration')
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


