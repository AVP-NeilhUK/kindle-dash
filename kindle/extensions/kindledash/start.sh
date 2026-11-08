#!/bin/sh
# Launch the loop fully detached, so it survives the Kindle interface being stopped.
DIR=/mnt/us/extensions/kindledash
echo "start.sh $(date)" > $DIR/log.txt
if command -v setsid >/dev/null 2>&1; then
  setsid nohup sh $DIR/dash.sh >> $DIR/log.txt 2>&1 < /dev/null &
else
  nohup sh $DIR/dash.sh >> $DIR/log.txt 2>&1 < /dev/null &
fi
