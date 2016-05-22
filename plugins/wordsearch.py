""" Script for wordsearch

THIS SCRIPT IS INCOMPLETE.

Commands:
!wordsearch
"""

from re import match
from random import choice

import discord
import asyncio
import aiohttp

import plugins


wordsearch = []
wordsearch_words = []

TUTORIAL = "Write any word ending with `!` to guess the word!"
CHARACTER_MATCH = "[a-z0-9æøå]+"


def valid_word(message: discord.Message):
    """ Check if the word only contains norwegian style letters or numbers. """
    if match("^" + CHARACTER_MATCH + "$", message.content.lower()):
        if len(message.content) < 32:
            return True

    return False


def valid_guess(message: discord.Message):
    """ Check if a message in this channel is a word guess. """
    if match("^" + CHARACTER_MATCH + "!$", message.content.lower()):
        return True

    return False


def format_hint(hint):
    """ Formats the hint string for our messages. """
    return "The word starts with `{0}`.".format(hint) if hint else ""


@asyncio.coroutine
def auto_word(count=1):
    global wordsearch_words

    word = ""
    if count < 1:
        count = 1
    elif count > 5:
        count = 5

    # Download a list of words if not stored in memory
    if not wordsearch_words:
        with aiohttp.ClientSession() as session:
            response = yield from session.get("http://www.mieliestronk.com/corncob_lowercase.txt")

            wordsearch_words = yield from response.text() if response.status == 200 else ""

        wordsearch_words = wordsearch_words.split("\n")

    for _ in range(count):
        word += choice(wordsearch_words).strip()

    return word.lower()


def stop_wordsearch(channel: discord.Channel):
    wordsearch.remove(channel.id)


@asyncio.coroutine
def start_wordsearch(client: discord.Client, channel: discord.Channel, host: discord.Member, word: str=None):
    if channel.id not in wordsearch:
        if not word:
            yield from client.send_message(channel, "Waiting for {0.mention} to choose a word!".format(host))
    else:
        yield from client.send_message(channel, "A wordsearch is already active in this channel!")
        return

    # Initialize the wordsearch
    wordsearch.append(channel.id)

    # Wait for the user to enter a word
    if not word:
        yield from client.send_message(host, "**Please enter a word!**\n"
                                             "The word should be **maximum 32 characters long** and "
                                             "may **only** contain `letters A-Å` and *numbers*.")
        reply = yield from client.wait_for_message(30, author=host, check=valid_word)

        # Stop the wordsearch if the user spent more than 30 seconds writing a valid word
        if not reply:
            stop_wordsearch(channel)
            yield from client.send_message(channel, "{0.mention} failed to enter a valid word.".format(host))
            return

        # Start the wordsearch
        word = reply.content.lower()
        yield from client.send_message(host, "Set the word to `{}`.".format(word))
        yield from client.send_message(channel, "{0.mention} has entered a word! {1}".format(host, TUTORIAL))
    else:
        yield from client.send_message(channel, "{0.mention} made me set a word! {1}".format(host, TUTORIAL))

    tries = 0
    hint = ""

    while channel.id in wordsearch:
        reply = yield from client.wait_for_message(60 * 30, channel=channel, check=valid_guess)

        # Wordsearch expires after 30 minutes
        if not reply:
            stop_wordsearch(channel)
            yield from client.send_message(channel, "**The wordsearch was cancelled after 30 minutes of inactivity.**\n"
                                                    "The word was `{}`.".format(word))
            return

        guessed_word = reply.content.lower()[:-1]
        tries += 1

        # Update hint
        if guessed_word.startswith(hint):
            hint = ""
            for i, c in enumerate(guessed_word):
                if len(word) - 1 < i:
                    break

                if not c == word[i]:
                    break

                hint += c

        # Compare the words
        if guessed_word > word:
            m = "{0.mention} `{1}` is *after* in the dictionary.".format(reply.author, guessed_word) + \
                format_hint(hint)
        elif guessed_word < word:
            m = "{0.mention} `{1}` is *before* in the dictionary.".format(reply.author, guessed_word) + \
                format_hint(hint)
        else:
            m = ""

        if guessed_word.startswith(word):
            # User guessed the right word (kind of)
            m = "{0.mention} ***got it*** after **{tries}** tries! The word was `{word}`.".format(reply.author,
                                                                                                  tries=tries,
                                                                                                  word=word)
            stop_wordsearch(channel)

        asyncio.async(client.send_message(channel, m))


@plugins.command(name="wordsearch", usage="[auto <count>]")
def wordsearch_(client: discord.Client, message: discord.Message):
    """ Start a wordsearch! Enter *any word* ending with `!` to guess the word!
        `auto` automatically sets a word for you. Default is one word, or enter up to 5 with `count`.
        **Example**: `!wordsearch auto 4`"""
    client.loop.create_task(start_wordsearch(client, message.channel, message.author))


@wordsearch_.command()
def auto(client: discord.Client, message: discord.Message, word_count: int=1):
    """ Start an automatic wordsearch. """
    word = yield from auto_word(word_count)
    print(word)
    client.loop.create_task(start_wordsearch(client, message.channel, message.author, word))
