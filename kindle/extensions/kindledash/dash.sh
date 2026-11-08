#!/bin/sh
DIR=/mnt/us/extensions/kindledash
. $DIR/dash.conf
trap '' HUP TERM INT          # ignore the signals sent when the interface stops
echo "dash.sh running, pid $$"
sleep 6                       # let KUAL close first
lipc-set-prop com.lab126.powerd preventScreenSaver 1 && echo "screensaver off"
lipc-set-prop com.lab126.wifid enable 1 2>/dev/null
if stop lab126_gui 2>/dev/null; then echo "stopped lab126_gui"
elif stop framework 2>/dev/null; then echo "stopped framework"
else echo "could not stop interface (dashboard may get drawn over)"; fi
sleep 5
echo "wifi: $(lipc-get-prop com.lab126.wifid cmState 2>/dev/null)"
while true; do
  echo "--- refresh $(date)"
  sh $DIR/fetch.sh
  sleep $INTERVAL
done
