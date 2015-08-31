#!/usr/bin/env python3

import time, json, sys, re
from datetime import datetime
import traceback
import logging

from slackclient import SlackClient

class Bot:
    def __init__(self, token, logger=None):
        assert isinstance(token, str), "`token` must be a valid Slack API token"
        assert logger is None or not isinstance(logger, logging.Logger), "`logger` must be `None` or a logging function"

        self.client = SlackClient(token)
        if logger is None: self.logger = logging.getLogger(self.__class__.__name__)
        else: self.logger = logger

        # rate limit sending
        self.last_say_time = 0

    def on_step(self):
        self.logger.info("step handler called")
    def on_message(self, message):
        self.logger.info("message handler called with message {}".format(message))
    
    def start_loop(self):
        while True:
            try: self.start() # start the main loop
            except KeyboardInterrupt: break
            except Exception:
                self.logger.error("main loop threw exception:\n{}".format(traceback.format_exc()))
                self.logger.info("restarting in 5 seconds...")
                time.sleep(5)
        self.logger.info("shutting down...")
    
    def start(self):
        # connect to the Slack Realtime Messaging API
        self.logger.info("connecting to Slack realtime messaging API...")
        if not self.client.rtm_connect(): raise ConnectionError("Could not connect to Slack realtime messaging API (possibly a bad token or network issue)")
        self.logger.info("connected to Slack realtime messaging API")

        last_ping = time.time()
        while True:
            # call all the step callbacks
            try: self.on_step()
            except Exception:
                self.logger.error("step processing threw exception:\n{}".format(traceback.format_exc()))

            # call all the message callbacks for each newly received message
            for message in self.client.rtm_read():
                try: self.on_message(message)
                except KeyboardInterrupt: raise
                except Exception:
                    self.logger.error("message processing threw exception:\n{}\n\nmessage contents:\n{}".format(traceback.format_exc(), message))

            # ping the server periodically to make sure our connection is kept alive
            if time.time() - last_ping > 5:
                self.client.server.ping()
                last_ping = time.time()
            
            # avoid checking the socket too often
            time.sleep(0.1)

    def say(self, channel_id, text):
        """Say `text` in the channel with ID `channel_id`."""
        assert self.get_channel_name_by_id(channel_id) is not None, "`channel_id` must be a valid channel ID rather than \"{}\"".format(channel_id)
        assert isinstance(text, str), "`text` must be a string rather than \"{}\"".format(text)

        # rate limit sending to 1 per second, since that's the Slack API limit
        current_time = time.time()
        if current_time - self.last_say_time < 1:
            time.sleep(max(0, current_time - self.last_say_time))
            self.last_say_time += 1
        else:
            self.last_say_time = current_time

        self.logger.info("sending message to channel {}: {}".format(self.get_channel_name_by_id(channel_id), text))
        self.client.rtm_send_message(channel_id, text)

    def get_channel_name_by_id(self, channel_id):
        """Returns the name of the channel with ID `channel_id`, or `None` if the ID is invalid. Channels include public channels, direct messages with other users, and private groups."""
        assert isinstance(channel_id, str), "`channel_id` must be a valid channel ID rather than \"{}\"".format(channel_id)
        for entry in self.client.server.channels:
            if entry.id == channel_id: return entry.name
        return None

    def get_channel_id_by_name(self, channel_name):
        """Returns the ID of the channel with name `channel_name`, or `None` if there is no such channel. Channels include public channels, direct messages with other users, and private groups."""
        assert isinstance(channel_name, str), "`channel_name` must be a valid channel name rather than \"{}\"".format(channel_name)

        channel_name = channel_name.strip().lstrip("#")

        # check for channel reference (these are formatted like `<#CHANNEL_ID>` or `<#CHANNEL_ID|CHANNEL_NAME>`)
        match = re.match(r"<#(\w+)(?:\|[^>]+)?>$", channel_name)
        if match: return match.group(1)

        # search by channel name
        for entry in self.client.server.channels:
            if entry.name == channel_name: return entry.id

        return None

    def get_user_name_by_id(self, user_id):
        """Returns the username of the user with ID `user_id`."""
        assert isinstance(user_id, str), "`user_id` must be a valid user ID rather than \"{}\"".format(user_id)
        for entry in self.client.server.users:
            if entry.id == user_id: return entry.name
        return None

    def get_user_id_by_name(self, user_name):
        """Returns the ID of the user with username `user_name`, or `None` if the ID is invalid."""
        assert isinstance(user_name, str), "`user_name` must be a valid username rather than \"{}\"".format(user_name)

        # check for user reference (these are formatted like `<@USER_ID>` or `<@USER_ID|USER_NAME>`)
        match = re.match(r"<@(\w+)(?:\|[^>]+)?>$", user_name)
        if match: return match.group(1)

        # search by user name
        for entry in self.client.server.users:
            if entry.name == user_name: return entry.id

        # search by user real name
        for entry in self.client.server.users:
            if entry.real_name == user_name: return entry.id

        return None

    def get_direct_message_channel_id_by_user_id(self, user_id):
        """Returns the channel ID of the direct message with the user with ID `user_id`, or `None` if the ID is invalid."""
        listing = json.loads(self.client.api_call("im.list").decode("utf-8"))["ims"]
        for entry in listing:
            if entry["user"] == user_id: return entry["id"]
        return None

class DebugBot:
    def __init__(self, token, logger=None):
        assert isinstance(token, str), "`token` must be a valid Slack API token"
        assert logger is None or not isinstance(logger, logging.Logger), "`logger` must be `None` or a logging function"

        if logger is None: self.logger = logging.getLogger(self.__class__.__name__)
        else: self.logger = logger

    def on_step(self): pass
    def on_message(self, message): pass
    def start_loop(self): self.start()

    def start(self):
        import threading, queue
        import readline # this makes arrow keys work for input()

        channel_name = "#general"

        incoming_message_queue = queue.Queue()
        def accept_input():
            while True:
                text = input("{} | Me: ".format(channel_name))
                time.sleep(0.1) # allow time for the enter keystroke to show up in the terminal
                incoming_message_queue.put({
                    "type": "message",
                    "channel": channel_name,
                    "user": "Me",
                    "text": text,
                    "ts": str(time.time()),
                })
                incoming_message_queue.join()
        input_thread = threading.Thread(target=accept_input)
        input_thread.daemon = True  # thread dies when main thread (only non-daemon thread) exits.
        input_thread.start()

        try:
            while True:
                self.on_step()
                while not incoming_message_queue.empty():
                    self.on_message(incoming_message_queue.get())
                    incoming_message_queue.task_done()
                time.sleep(0.1)
        except KeyboardInterrupt: pass

    def say(self, channel_id, text):
        """Say `text` in the channel with ID `channel_id`."""
        assert self.get_channel_name_by_id(channel_id) is not None, "`channel_id` must be a valid channel ID rather than \"{}\"".format(channel_id)
        assert isinstance(text, str), "`text` must be a string rather than \"{}\"".format(text)
        print("{} | Botty: {}".format(channel_id, text))

    def get_channel_name_by_id(self, channel_id):
        """Returns the name of the channel with ID `channel_id`, or `None` if the ID is invalid. Channels include public channels, direct messages with other users, and private groups."""
        assert isinstance(channel_id, str), "`channel_id` must be a valid channel ID rather than \"{}\"".format(channel_id)
        return channel_id

    def get_channel_id_by_name(self, channel_name):
        """Returns the ID of the channel with name `channel_name`, or `None` if there is no such channel. Channels include public channels, direct messages with other users, and private groups."""
        assert isinstance(channel_name, str), "`channel_name` must be a valid channel name rather than \"{}\"".format(channel_name)
        return channel_name

    def get_user_name_by_id(self, user_id):
        """Returns the username of the user with ID `user_id`."""
        assert isinstance(user_id, str), "`user_id` must be a valid user ID rather than \"{}\"".format(user_id)
        return user_id

    def get_user_id_by_name(self, user_name):
        """Returns the ID of the user with username `user_name`, or `None` if the ID is invalid."""
        assert isinstance(user_name, str), "`user_name` must be a valid username rather than \"{}\"".format(user_name)
        return user_name

    def get_direct_message_channel_id_by_user_id(self, user_id):
        """Returns the channel ID of the direct message with the user with ID `user_id`, or `None` if the ID is invalid."""
        return user_id
