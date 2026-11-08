#!/bin/sh
# Downloads the dashboard and draws it. Shared by once.sh and dash.sh.
DIR=/mnt/us/extensions/kindledash
. $DIR/dash.conf
# wait up to 60s for Wi-Fi
i=0
while ! lipc-get-prop com.lab126.wifid cmState 2>/dev/null | grep -q CONNECTED; do
  i=$((i+1)); [ $i -gt 30 ] && break; sleep 2
done
# -k: the Kindle's certificate store is years out of date
echo "fetching $URL"
if curl -k -s -f -m 60 -o $DIR/new.png "$URL?t=$(date +%s)" || \
   wget -q -O $DIR/new.png "$URL"; then
  mv $DIR/new.png $DIR/dash.png; echo "download ok"
else echo "download FAILED"
fi
[ -f $DIR/dash.png ] && eips -f -g $DIR/dash.png
BATT=$(lipc-get-prop com.lab126.powerd battLevel 2>/dev/null)
if [ -n "$BATT" ] && [ "$BATT" -le "$LOW_BATTERY" ]; then
  eips 1 39 " BATTERY ${BATT}% - CHARGE ME "
fi
