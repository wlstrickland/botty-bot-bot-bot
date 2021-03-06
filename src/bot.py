#!/usr/bin/env python3

import time, json, sys, re
from datetime import datetime
import traceback
import logging
from collections import deque

from slackclient import SlackClient

class SlackBot:
    def __init__(self, token, logger=None):
        assert isinstance(token, str), "`token` must be a valid Slack API token"
        assert logger is None or not isinstance(logger, logging.Logger), "`logger` must be `None` or a logging function"

        self.client = SlackClient(token)
        if logger is None: self.logger = logging.getLogger(self.__class__.__name__)
        else: self.logger = logger

        self.max_message_id = 1 # every message sent over RTM needs a unique positive integer ID - this should technically be handled by the Slack library, but that's broken as of now
        self.unprocessed_messages = deque() # store unprocessed messages to allow message peeking
        self.last_say_time = 0 # store last message send timestamp to rate limit sending
        self.bot_user_id = None # ID of this bot user

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

    def get_unprocessed_messages(self):
        result = list(self.unprocessed_messages) + self.client.rtm_read()
        self.unprocessed_messages.clear()
        return result

    def peek_unprocessed_messages(self):
        self.unprocessed_messages.extend(self.client.rtm_read())
        return list(self.unprocessed_messages)

    def peek_new_messages(self):
        new_messages = self.client.rtm_read()
        self.unprocessed_messages.extend(new_messages)
        return list(new_messages)

    def start(self):
        # connect to the Slack Realtime Messaging API
        self.logger.info("connecting to Slack realtime messaging API...")
        if not self.client.rtm_connect(): raise ConnectionError("Could not connect to Slack realtime messaging API (possibly a bad token or network issue)")
        self.logger.info("connected to Slack realtime messaging API")

        # obtain the bot credentials
        authentication = self.client.api_call("auth.test")
        assert authentication["ok"], "Could not authenticate with Slack API"
        self.bot_user_id = authentication["user_id"]

        last_ping = time.time()
        while True:
            # call all the step callbacks
            try: self.on_step()
            except Exception:
                self.logger.error("step processing threw exception:\n{}".format(traceback.format_exc()))

            # call all the message callbacks for each newly received message
            for message in self.get_unprocessed_messages():
                try: self.on_message(message)
                except KeyboardInterrupt: raise
                except Exception:
                    self.logger.error("message processing threw exception:\n{}\n\nmessage contents:\n{}".format(traceback.format_exc(), message))

            # ping the server periodically to make sure our connection is kept alive
            if time.time() - last_ping > 5:
                self.client.server.ping()
                last_ping = time.time()

            # delay to avoid checking the socket too often
            time.sleep(0.01)

    def say(self, channel_id, sendable_text):
        """Say `sendable_text` in the channel with ID `channel_id`, returning the message ID (unique within each `SlackBot` instance)."""
        assert self.get_channel_name_by_id(channel_id) is not None, "`channel_id` must be a valid channel ID rather than \"{}\"".format(channel_id)
        assert isinstance(sendable_text, str), "`text` must be a string rather than \"{}\"".format(sendable_text)

        # rate limit sending to 1 per second, since that's the Slack API limit
        current_time = time.time()
        if current_time - self.last_say_time < 1:
            time.sleep(max(0, 1 - (current_time - self.last_say_time)))
            self.last_say_time += 1
        else:
            self.last_say_time = current_time

        self.logger.info("sending message to channel {}: {}".format(self.get_channel_name_by_id(channel_id), sendable_text))

        # the correct method to use here is `rtm_send_message`, but it's technically broken since it doesn't send the message ID so we're going to do this properly ourselves
        # the message ID allows us to correlate messages with message responses, letting us ensure that messages are actually delivered properly
        # see the "Sending messages" heading at https://api.slack.com/rtm for more details
        message_id = self.max_message_id
        self.max_message_id += 1
        self.client.server.send_to_websocket({
            "id": message_id,
            "type": "message",
            "channel": channel_id,
            "text": sendable_text,
        })
        return message_id

    def say_complete(self, channel_id, sendable_text, timeout = 5):
        """Say `sendable_text` in the channel with ID `channel_id`, waiting for the message to finish sending (raising a `TimeoutError` if this takes more than `timeout` seconds), returning the message timestamp."""
        assert float(timeout) > 0, "`timeout` must be a positive number rather than \"{}\"".format(timeout)
        message_id = self.say(channel_id, sendable_text)
        message_timestamp = None
        start_time = time.time()
        while message_timestamp is None and time.time() - start_time < timeout:
            # peek at new messages to see if the response is written
            for message in self.peek_new_messages():
                if "ok" in message and message.get("reply_to") == message_id: # received reply for the sent message
                    if not message["ok"]: raise ValueError("Message sending error: {}".format(message.get("error", {}).get("msg")))
                    assert isinstance(message.get("ts"), str), "Invalid message timestamp: {}".format(message.get("ts"))
                    message_timestamp = message["ts"]
                    break
            else:
                time.sleep(0.01)
        if message_timestamp is None: raise TimeoutError("Message sending timed out")
        return message_timestamp

    def react(self, channel_id, timestamp, emoticon):
        """React with `emoticon` to the message with timestamp `timestamp` in channel with ID `channel_id`."""
        assert self.get_channel_name_by_id(channel_id) is not None, "`channel_id` must be a valid channel ID rather than \"{}\"".format(channel_id)
        assert isinstance(timestamp, str), "`timestamp` must be a string rather than \"{}\"".format(sendable_text)
        assert isinstance(emoticon, str), "`emoticon` must be a string rather than \"{}\"".format(sendable_text)
        emoticon = emoticon.strip(":")
        self.logger.info("adding reaction :{}: to message with timestamp {} in channel {}".format(emoticon, timestamp, self.get_channel_name_by_id(channel_id)))
        response = self.client.api_call("reactions.add", name=emoticon, channel=channel_id, timestamp=timestamp)
        assert "ok" in response and response["ok"], "Reaction addition failed"

    def unreact(self, channel_id, timestamp, emoticon):
        """React with `emoticon` to the message with timestamp `timestamp` in channel with ID `channel_id`."""
        assert self.get_channel_name_by_id(channel_id) is not None, "`channel_id` must be a valid channel ID rather than \"{}\"".format(channel_id)
        assert isinstance(timestamp, str), "`timestamp` must be a string rather than \"{}\"".format(sendable_text)
        assert isinstance(emoticon, str), "`emoticon` must be a string rather than \"{}\"".format(sendable_text)
        emoticon = emoticon.strip(":")
        self.logger.info("removing reaction :{}: to message with timestamp {} in channel {}".format(emoticon, timestamp, self.get_channel_name_by_id(channel_id)))
        response = self.client.api_call("reactions.remove", name=emoticon, channel=channel_id, timestamp=timestamp)
        assert "ok" in response and response["ok"], "Reaction removal failed"

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

        user_name = user_name.strip().lstrip("@")

        # check for user reference (these are formatted like `<@USER_ID>` or `<@USER_ID|USER_NAME>`)
        match = re.match(r"^<@(\w+)(?:\|[^>]+)?>$", user_name)
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
        listing = self.client.api_call("im.list")["ims"]
        for entry in listing:
            if entry["user"] == user_id: return entry["id"]
        return None

    def server_text_to_sendable_text(self, server_text):
        """Returns `server_text`, a string in Slack server message format, converted into a string in Slack sendable message format."""
        assert isinstance(server_text, str), "`server_text` must be a string rather than \"{}\"".format(server_text)
        text_without_special_sequences = re.sub(r"<[^<>]*>", "", server_text)
        assert "<" not in text_without_special_sequences and ">" not in text_without_special_sequences, "Invalid special sequence in server text \"{}\", perhaps some text needs to be escaped"

        # process link references
        def process_special_sequence(match):
            original, body = match.group(0), match.group(1).split("|")[0]
            if body.startswith("#C"): return original # channel reference, should send unchanged
            if body.startswith("@U"): return original # user reference, should send unchanged
            if body.startswith("!"): return original # special command, should send unchanged
            return body # link, should remove angle brackets and label in order to allow it to linkify
        return re.sub(r"<(.*?)>", process_special_sequence, server_text)

    def text_to_sendable_text(self, text):
        """Returns `text`, a plain text string, converted into a string in Slack sendable message format."""
        assert isinstance(text, str), "`text` must be a string rather than \"{}\"".format(text)
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def sendable_text_to_text(self, sendable_text):
        """Returns `sendable_text`, a string in Slack sendable message format, converted into a plain text string. The transformation can lose some information for escape sequences, such as link labels."""
        assert isinstance(sendable_text, str), "`sendable_text` must be a string rather than \"{}\"".format(sendable_text)
        text_without_special_sequences = re.sub(r"<[^<>]*>", "", sendable_text)
        assert "<" not in text_without_special_sequences and ">" not in text_without_special_sequences, "Invalid special sequence in sendable text \"{}\", perhaps some text needs to be escaped"

        # process link references
        def process_special_sequence(match):
            original, body = match.group(0), match.group(1).split("|")[0]
            if body.startswith("#C"): # channel reference
                channel_name = self.get_channel_name_by_id(body[1:])
                if channel_name is None: return ""
                return "#" + channel_name
            if body.startswith("@U"): # user reference
                user_name = self.get_user_name_by_id(body[1:])
                if user_name is None: return ""
                return "@" + user_name
            if body.startswith("!"): # special command
                if body == "!channel": return "@channel"
                if body == "!group": return "@group"
                if body == "!everyone": return "@everyone"
            return original
        raw_text = re.sub(r"<(.*?)>", process_special_sequence, sendable_text)

        return raw_text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")

    def administrator_console(self, namespace):
        """Start an interactive administrator Python console with namespace `namespace`."""
        import threading
        import readline # this makes arrow keys work for input()
        import code
        def start_console():
            code.interact(
                "##########################################\n" +
                "#   Botty Administrator Python Console   #\n" +
                "##########################################\n",
                local=namespace
            )
        console_thread = threading.Thread(target=start_console)
        console_thread.daemon = True  # thread dies when main thread (only non-daemon thread) exits.
        console_thread.start()

class SlackDebugBot(SlackBot):
    def __init__(self, token, logger=None):
        assert isinstance(token, str), "`token` must be a valid Slack API token"
        assert logger is None or not isinstance(logger, logging.Logger), "`logger` must be `None` or a logging function"

        if logger is None: self.logger = logging.getLogger(self.__class__.__name__)
        else: self.logger = logger

        self.max_message_id = 1
        self.channel_name = "general"
        self.bot_user_id = "botty"

    def start_loop(self): self.start()

    def start(self):
        import threading, queue
        import readline # this makes arrow keys work for input()

        incoming_message_queue = queue.Queue()
        def accept_input():
            while True:
                text = input("{:<12}| Me: ".format("#" + self.channel_name)) # clear the current line using Erase in Line ANSI escape code
                time.sleep(0.1) # allow time for the enter keystroke to show up in the terminal
                incoming_message_queue.put({
                    "type": "message",
                    "channel": "C" + self.channel_name,
                    "user": "UMe",
                    "text": self.text_to_sendable_text(text),
                    "ts": str(time.time()),
                })
        input_thread = threading.Thread(target=accept_input)
        input_thread.daemon = True  # thread dies when main thread (only non-daemon thread) exits.
        input_thread.start()

        try:
            while True:
                self.on_step()
                while not incoming_message_queue.empty():
                    self.on_message(incoming_message_queue.get())
                    incoming_message_queue.task_done()
                time.sleep(0.01)
        except KeyboardInterrupt: pass

    def say(self, channel_id, sendable_text):
        """Say `sendable_text` in the channel with ID `channel_id`, returning the message ID (unique within each `SlackBot` instance)."""
        assert self.get_channel_name_by_id(channel_id) is not None, "`channel_id` must be a valid channel ID rather than \"{}\"".format(channel_id)
        assert isinstance(sendable_text, str), "`sendable_text` must be a string rather than \"{}\"".format(sendable_text)

        self.logger.info("sending message to channel {}: {}".format(self.get_channel_name_by_id(channel_id), sendable_text))
        print("\r\033[K" + "{:<12}| Botty: {}".format(self.get_channel_name_by_id(channel_id), sendable_text)) # clear the current line using Erase in Line ANSI escape code
        print("{:<12}| Me: ".format(self.channel_name), end="", flush=True)

        message_id = self.max_message_id
        self.max_message_id += 1
        return message_id

    def say_complete(self, channel_id, sendable_text):
        """Say `sendable_text` in the channel with ID `channel_id`, waiting for the message to finish sending (raising a `TimeoutError` if this takes more than `timeout` seconds), returning the message timestamp."""
        self.say(channel_id, sendable_text)
        return time.time()

    def react(self, channel_id, timestamp, emoticon):
        """React with `emoticon` to the message with timestamp `timestamp` in channel with ID `channel_id`."""
        assert self.get_channel_name_by_id(channel_id) is not None, "`channel_id` must be a valid channel ID rather than \"{}\"".format(channel_id)
        assert isinstance(timestamp, str), "`timestamp` must be a string rather than \"{}\"".format(sendable_text)
        assert isinstance(emoticon, str), "`emoticon` must be a string rather than \"{}\"".format(sendable_text)
        self.logger.info("adding reaction :{}: to message with timestamp {} in channel {}".format(emoticon, timestamp, self.get_channel_name_by_id(channel_id)))
        print("\r\033[K" + "{:<12}| Botty reacts with :{}:".format(self.get_channel_name_by_id(channel_id), emoticon)) # clear the current line using Erase in Line ANSI escape code
        print("{:<12}| Me: ".format(self.channel_name), end="", flush=True)

    def unreact(self, channel_id, timestamp, emoticon):
        """React with `emoticon` to the message with timestamp `timestamp` in channel with ID `channel_id`."""
        assert self.get_channel_name_by_id(channel_id) is not None, "`channel_id` must be a valid channel ID rather than \"{}\"".format(channel_id)
        assert isinstance(timestamp, str), "`timestamp` must be a string rather than \"{}\"".format(sendable_text)
        assert isinstance(emoticon, str), "`emoticon` must be a string rather than \"{}\"".format(sendable_text)
        self.logger.info("removing reaction :{}: to message with timestamp {} in channel {}".format(emoticon, timestamp, self.get_channel_name_by_id(channel_id)))
        print("\r\033[K" + "{:<12}| Botty unreacts with {}".format(self.get_channel_name_by_id(channel_id), emoticon)) # clear the current line using Erase in Line ANSI escape code
        print("{:<12}| Me: ".format(self.channel_name), end = "", flush=True)

    def get_channel_name_by_id(self, channel_id):
        """Returns the name of the channel with ID `channel_id`, or `None` if the ID is invalid. Channels include public channels, direct messages with other users, and private groups."""
        assert isinstance(channel_id, str) and channel_id[0] in {"C", "D"}, "`channel_id` must be a valid channel ID rather than \"{}\"".format(channel_id)
        return channel_id[1:]

    def get_channel_id_by_name(self, channel_name):
        """Returns the ID of the channel with name `channel_name`, or `None` if there is no such channel. Channels include public channels, direct messages with other users, and private groups."""
        assert isinstance(channel_name, str), "`channel_name` must be a valid channel name rather than \"{}\"".format(channel_name)
        channel_name = channel_name.strip().lstrip("#")
        return "C{}".format(channel_name)

    def get_user_name_by_id(self, user_id):
        """Returns the username of the user with ID `user_id`."""
        assert isinstance(user_id, str) and user_id[0] == "U", "`user_id` must be a valid user ID rather than \"{}\"".format(user_id)
        return user_id[1:]

    def get_user_id_by_name(self, user_name):
        """Returns the ID of the user with username `user_name`, or `None` if the ID is invalid."""
        assert isinstance(user_name, str), "`user_name` must be a valid username rather than \"{}\"".format(user_name)
        user_name = user_name.strip().lstrip("@")
        return "U{}".format(user_name)

    def get_direct_message_channel_id_by_user_id(self, user_id):
        """Returns the channel ID of the direct message with the user with ID `user_id`, or `None` if the ID is invalid."""
        return "D{}".format(user_id)

    def administrator_console(self, namespace):
        """Start an interactive administrator Python console with namespace `namespace`."""
        raise NotImplementedError("The administrator console is not supported in the debug Slack bot.")
