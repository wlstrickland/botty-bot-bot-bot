#!/usr/bin/env python3

import re, random, sqlite3
from os import path

from ..utilities import BasePlugin
from .markov import Markov

SQLITE_DATABASE = path.join(path.dirname(path.realpath(__file__)), "chains.db")
LOOKBEHIND_LENGTH = 2

def speak_db(db_connection, lookbehind_length, initial_state = ()):
    # generate a message based on probability chains
    current_key = tuple(initial_state)[-lookbehind_length:]
    token_list = []
    while True:
        row = db_connection.execute("SELECT count FROM counts WHERE key = ?", ("\n".join(current_key),)).fetchone()
        if row is None: raise KeyError("Key not in chain: {}".format(current_key))
        count = row[0]
        random_choice = random.randrange(0, count)
        choices = db_connection.execute("SELECT next_word, occurrences FROM chain WHERE key = ?", ("\n".join(current_key),))
        for current_choice, occurrences in choices:
            random_choice -= occurrences
            if random_choice < 0:
                new_token = current_choice
                break
        else: # couldn't find the choice somehow
            raise ValueError("Bad choice for key: {}".format(current_key)) # this should never happen but would otherwise be hard to detect if it did

        # add the token to the message
        if new_token == None: break
        token_list.append(new_token)

        if len(current_key) < lookbehind_length: current_key += (new_token,) # add current token to key if just starting
        else: current_key = current_key[1:] + (new_token,) # shift token onto key if inside message
    return token_list

class GenerateTextPlugin(BasePlugin):
    """
    Text generation plugin for Botty.

    This is implemented with a Markov chain with 2 token lookbehind.

    Example invocations:

        #general    | Me: botty gotta
        #general    | Botty: gotta be frustrating of course...
        #general    | Me: botty
        #general    | Botty: my friend doens't do that
        #general    | Me: botty don't
        #general    | Botty: don't think i saw the ride
    """
    def __init__(self, bot):
        super().__init__(bot)

        assert os.path.exists(SQLITE_DATABASE), "Markov chain must be trained by running the `src/plugins/generate_text/generate_chains_db.py` script."
        self.connection = sqlite3.connect(SQLITE_DATABASE)

    def on_message(self, message):
        text = self.get_message_text(message)
        if text is None: return False
        match = re.search(r"\bbotty(?:[\s,\.]+(.*)|$)", text, re.IGNORECASE)
        if not match: return False
        query = self.sendable_text_to_text(match.group(1) or "")

        # use markov chain to complete given phrase
        try: self.respond_raw(self.generate_sentence_starting_with(query))
        except KeyError: self.respond_raw(self.generate_sentence_starting_with())
        return True

    def generate_sentence_starting_with(self, first_part = ""):
        first_part = first_part.strip()
        words = Markov.tokenize_text(first_part) if first_part != "" else []
        return Markov.format_words(words + speak_db(self.connection, LOOKBEHIND_LENGTH, words))
