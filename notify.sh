#!/bin/bash
# shellcheck disable=SC1094

# notification script that can be used for slack or mail
# configure destinations in `notify.ini`
# see `README.md` for setup information

# USAGE:
# /bin/bash /path/to/this/script.sh "message to be sent" "subject"

# slack
enable_slack=$(source notify.ini && echo "$ENABLE_SLACK")
if [ "$enable_slack" -eq 1 ]; then
    slack=$(source "$(dirname "$0")"/notify.ini && echo "$SLACK_CMD")
    channel=$(source notify.ini && echo "$SLACK_CHANNEL")
    echo "$1" | $slack chat send --pretext "$2" --channel "$channel"
fi

# mail
enable_mail=$(source notify.ini && echo "$ENABLE_MAIL")
if [ "$enable_mail" -eq 1 ]; then
    mail=$(source "$(dirname "$0")"/notify.ini && echo "$MAIL_CMD")
    recipients=$(source notify.ini && echo "$EMAIL_ADDRESSES")
    echo "$1" | $mail -s "$2" "$recipients"
fi
