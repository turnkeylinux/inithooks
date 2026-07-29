## v2.3.4 Release notes
    
  * Bugfix firstboot.d/95secupdates: 
    - Make skipping secupdates via preseed and interactively act consistantly.
    - Related improved journal logging:
        - Successful secupdates install are logged as info.
        - Skipped secupdates are logged as warning.
