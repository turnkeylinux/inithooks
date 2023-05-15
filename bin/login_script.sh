#!/bin/bash

. /etc/default/inithooks
${INITHOOKS_PATH}/run
clear
echo -en "\nDebian GNU/Linux $(lsb_release -rs) $(hostname) tty1\n\n"
login -p
