import logging
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


class SlackHandler(logging.Handler):
    def __init__(self, token, channel):
        logging.Handler.__init__(self)
        self.token = token
        self.channel = channel
        print(self.token)
        print(self.channel)
        self.slack_client = WebClient(token=self.token)

    def emit(self, record):
        msg = self.format(record)
        self.slack_client.chat_postMessage(channel=self.channel, text=msg)
