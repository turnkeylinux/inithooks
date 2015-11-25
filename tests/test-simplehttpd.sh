#!/bin/bash

src=$(dirname $0)/..

echo "Initfence htdocs @ http://127.0.0.1:60080/ https://127.0.0.1:60443/"

set -x
$src/bin/simplehttpd.py $src/turnkey-init-fence/htdocs 60080 60443 $src/tests/cert.pem $src/tests/cert.key
