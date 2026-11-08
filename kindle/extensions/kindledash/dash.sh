#!/bin/sh
DIR=/mnt/us/extensions/kindledash
. $DIR/dash.conf
lipc-set-prop com.lab126.powerd preventScreenSaver 1
# stop the Kindle's own interface so it can't draw over the dashboard
stop lab126_gui 2>/dev/null || stop framework 2>/dev/null
sleep 5
while true; do
  sh $DIR/fetch.sh
  sleep $INTERVAL
done
