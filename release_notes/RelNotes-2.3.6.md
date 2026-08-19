## v2.3.6 Release notes

  * Bugfix:
    - Do not show error message for '09...' firstboot script/s.
      Somewhat cosmetic issue; numerical bash tests assume leading zero means
      an octal number and obviously 09 is not a valid octal number!
    - Make inithooks_cache.py executable (in postinst script) - resolves
      package install failure.
    - Ensure that firstboot 95secupdates only install security updates.
    - Refactor run script to ensure that inithooks run completes before
      rebooting (if needed post 95secupdates).
  * Functional robustness:
    - Ensure that generated self signed SSL cert & key match before moving on.
      Avoids occasional race condition where new key has not been written to
      disk when webserver is restarted (causing webserver to fail startup).
    - Ensure there is no failure when installing packages (95secupdates).
  * Minor dev tweak:
    - Minor comment update.
