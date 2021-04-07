#!/bin/bash
# shellcheck disable=SC1094

# notification script that can be used for slack or mail
# configure destinations in `notify.ini`

# USAGE:
# /bin/bash /path/to/this/script.sh "message to be sent" "subject"

# slack
enable_slack=$(source notify.ini && echo "$ENABLE_SLACK")
if [ "$enable_slack" -eq 1 ]; then
    slack=$(which slack)
    channel=$(source notify.ini && echo "$SLACK_CHANNEL")
    echo "$1" | $slack chat send --pretext "$2" --channel "$channel"
fi

# mail
enable_mail=$(source notify.ini && echo "$ENABLE_MAIL")
if [ "$enable_mail" -eq 1 ]; then
    mail=$(which mail)
    recipients=$(source notify.ini && echo "$EMAIL_ADDRESSES")
    echo "$1" | $mail -s "$2" "$recipients"
fi
