#!/bin/sh
# Slack CLI get-manifest hook: print the app manifest JSON. The CLI appends
# flags such as --source=<dir>; they are not needed here.
exec cat "$(dirname "$0")/../manifest.json"
