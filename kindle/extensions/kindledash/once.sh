#!/bin/sh
# Test run: waits for KUAL to close, then shows the dashboard once.
# Press the power button twice to get back.
DIR=/mnt/us/extensions/kindledash
( sleep 4; sh $DIR/fetch.sh ) > $DIR/log.txt 2>&1 &
