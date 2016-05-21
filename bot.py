import logging
import inspect
import os
from time import time
from getpass import getpass
from argparse import ArgumentParser

import discord
import asyncio

from pcbot import utils
import plugins


# Add all command-line arguments
parser = ArgumentParser(description="Run PCBOT.")
parser.add_argument("--version", help="Return the current version (placebo command; only tells you to git status).",
                    action="version", version="Try: git status")
parser.add_argument("--token", "-t", help="The token to login with. Prompts if omitted.")
parser.add_argument("--email", "-e", help="The email to login to. Token prompt is default.")
parser.add_argument("--new-pass", "-n", help="Always prompts for password.", action="store_true")
parser.add_argument("--log-level", "-l", help="Use the specified logging level (see the docs on logging for values).",
                    type=lambda s: getattr(logging, s.upper()), default=logging.INFO)
start_args = parser.parse_args()

# Setup logger with level specified in start_args or logging.INFO
logging.basicConfig(level=start_args.log_level, format="%(levelname)s [%(module)s] %(asctime)s: %(message)s")


# Setup our client
client = discord.Client()
autosave_interval = 60 * 30

plugins.load_plugin("builtin", "pcbot")  # Load plugin for builtin commands
plugins.load_plugins()  # Load all plugins in plugins/


@asyncio.coroutine
def autosave():
    """ Sleep for set time (default 30 minutes) before saving. """
    while not client.is_closed:
        yield from asyncio.sleep(autosave_interval)
        yield from plugins.save_plugins()
        logging.debug("Plugins saved")


def log_message(message: discord.Message, prefix: str=""):
    """ Logs a command/message. """
    logging.info("{prefix}@{0.author} -> {0.content}".format(message, prefix=prefix))


@asyncio.coroutine
def on_plugin_message(function, message: discord.Message, args: list):
    """ Run the given plugin function (either on_message() or on_command()).
    If the function returns True, log the sent message. """
    success = yield from function(client, message, args)

    if success:
        log_message(message, prefix="... ")


def parse_annotation(param: inspect.Parameter, arg: str, index: int, message: discord.Message):
    """ Parse annotations and return the command to use.

    index is basically the arg's index in shelx.split(message.content) """
    if param.annotation:  # Any annotation is a function or Annotation enum
        anno = param.annotation

        # Valid enum checks
        if anno is utils.Annotate.Content:
            return utils.split(message.content, maxsplit=index)[-1]
        elif anno is utils.Annotate.LowerContent:  # Lowercase of above check
            return utils.split(message.content, maxsplit=index)[-1].lower()
        elif anno is utils.Annotate.CleanContent:
            return utils.split(message.clean_content, maxsplit=index)[-1]
        elif anno is utils.Annotate.LowerCleanContent:  # Lowercase of above check
            return utils.split(message.clean_content, maxsplit=index)[-1].lower()
        elif anno is utils.Annotate.Member:  # Checks bot .Member and .User
            return utils.find_member(message.server, arg)
        elif anno is utils.Annotate.Channel:
            return utils.find_channel(message.server, arg)
        elif anno is utils.Annotate.Code:  # Works like Content but extracts code
            code = utils.split(message.content, maxsplit=index)[-1]
            return utils.get_formatted_code(code)

        try:  # Try running as a method
            return anno(arg)
        except TypeError:
            raise TypeError("Command parameter annotation must be either pcbot.utils.Annotate or a function")
        except:  # On error, eg when annotation is int and given argument is str
            return None
        
    return arg  # Return if there was no annotation


def parse_command_args(command: plugins.Command, cmd_args: list, start_index: int, message: discord.Message):
    """ Parse commands from chat and return args and kwargs to pass into the
    command's function. """
    signature = inspect.signature(command.function)
    args, kwargs = [], {}
    index = -1
    num_kwargs = sum(1 for param in signature.parameters.values() if param.kind is param.KEYWORD_ONLY)
    has_pos = False
    num_pos_args = 0

    # Parse all arguments
    for arg, param in signature.parameters.items():
        index += 1

        if index == 0:  # Param should have a Client annotation
            if param.annotation is not discord.Client:
                raise Exception("First command parameter must be of type discord.Client")

            continue
        elif index == 1:  # Param should have a Client annotation
            if param.annotation is not discord.Message:
                raise Exception("Second command parameter must be of type discord.Message")

            continue

        # Any argument to fetch
        if index <= len(cmd_args):  # If there is an argument passed
            cmd_arg = cmd_args[index - 1]
        else:
            if param.default is not param.empty:
                if param.kind is param.POSITIONAL_OR_KEYWORD:
                    args.append(param.default)
                elif param.kind is param.KEYWORD_ONLY:
                    kwargs[arg] = param.default

                continue  # Move onwards once we find a default
            else:
                index -= 1  # Decrement index since there was no argument
                break  # We're done when there is no default argument and none passed

        if param.kind is param.POSITIONAL_OR_KEYWORD:  # Parse the regular argument
            tmp_arg = parse_annotation(param, cmd_arg, (index - 1) + start_index, message)

            if tmp_arg is not None:
                args.append(tmp_arg)
            else:
                if param.default is not param.empty:
                    args.append(param.default)
                else:
                    return args, kwargs, False  # Force quit
        elif param.kind is param.KEYWORD_ONLY:  # Parse a regular arg as a kwarg
            tmp_arg = parse_annotation(param, cmd_arg, (index - 1) + start_index, message)

            if tmp_arg is not None:
                kwargs[arg] = tmp_arg
            else:
                if param.default is not param.empty:
                    kwargs[arg] = param.default
                else:
                    return args, kwargs, False  # Force quit
        elif param.kind is param.VAR_POSITIONAL:  # Parse all positional arguments
            has_pos = True

            for cmd_arg in cmd_args[index - 1:-num_kwargs]:
                tmp_arg = parse_annotation(param, cmd_arg, index + start_index, message)

                # Add an option if it's not None. Since positional arguments are optional,
                # it will not matter that we don't pass it.
                if tmp_arg is not None:
                    args.append(tmp_arg)
                    num_pos_args += 1

            index += (num_pos_args - 1) if num_pos_args else 0  # Update the new index

    # Number of required arguments are: signature variables - client and message
    # If there are no positional arguments, subtract one from the required arguments
    num_args = len(signature.parameters.items()) - 2
    if has_pos:
        num_args -= int(not bool(num_pos_args))

    num_given = index - 1  # Arguments parsed
    complete = (num_given == num_args)
    return args, kwargs, complete


def get_sub_command(command: plugins.Command, cmd_args: list):
    """ Go through all arguments and return any group command function.

    This function returns the found command and all arguments starting from
    any specified sub command. """
    new_index = 0

    for arg in cmd_args[1:]:
        names = [cmd.name for cmd in command.sub_commands]

        if arg in names:
            command = command.sub_commands[names.index(arg)]
            new_index += 1

    return command, cmd_args[new_index:], new_index


@asyncio.coroutine
def parse_command(command: plugins.Command, cmd: str, cmd_args: list, message: discord.Message):
    """ Try finding a command """
    command, cmd_args, start_index = get_sub_command(command, cmd_args)

    # Parse the command and return the parsed arguments
    args, kwargs, complete = parse_command_args(command, cmd_args, start_index, message)

    # If command parsing failed, display help for the command or the error message
    if not complete:
        log_message(message)  # Log the command

        if command.error:
            yield from client.send_message(message.channel, command.error)
        else:
            yield from plugins.get_plugin("builtin").cmd_help(client, message, cmd)

        command = None

    return command, args, kwargs


@client.async_event
def on_ready():
    """ Create any tasks for plugins' on_ready() coroutine and create task
    for autosaving. """
    logging.info("\nLogged in as\n"
                 "{0.user.name}\n"
                 "{0.user.id}\n".format(client) +
                 "-" * len(client.user.id))

    # Call any on_ready function in plugins
    for plugin in plugins.all_values():
        if getattr(plugin, "on_ready", False):
            client.loop.create_task(plugin.on_ready(client))

    client.loop.create_task(autosave())


@client.async_event
def on_message(message: discord.Message):
    """ What to do on any message received.

    The bot will handle all commands in plugins and send on_message to plugins using it. """
    start_time = time()

    if message.author == client.user:
        return

    if not message.content:
        return

    # Split content into arguments by space (surround with quotes for spaces)
    cmd_args = utils.split(message.content)

    # Get command name
    cmd = ""

    if cmd_args[0].startswith(utils.command_prefix) and len(cmd_args[0]) > 1:
        cmd = cmd_args[0][1:]

    # Handle commands
    for plugin in plugins.all_values():
        if cmd:
            command = utils.get_command(plugin, cmd)

            if command:
                command, args, kwargs = yield from parse_command(command, cmd, cmd_args, message)

                if command:
                    log_message(message)  # Log the command
                    client.loop.create_task(command.function(client, message, *args, **kwargs))  # Run command

                    # Log time spent parsing the command
                    stop_time = time()
                    time_elapsed = (stop_time - start_time) * 1000
                    logging.debug("Time spent parsing comand: {elapsed:.6f}".format(elapsed=time_elapsed))

        # Always run the on_message function if it exists
        if getattr(plugin, "on_message", False):
            client.loop.create_task(on_plugin_message(plugin.on_message, message, cmd_args))


def main():
    if not start_args.email:
        # Login with the specified token if specified
        token = start_args.token or input("Token: ")

        login = [token]
    else:
        # Get the email from commandline argument
        email = start_args.email

        password = ""
        cached_path = client._get_cache_filename(email)  # Get the name of the would-be cached email

        # If the --new-pass command-line argument is specified, remove the cached password.
        # Useful for when you have changed the password.
        if start_args.new_pass:
            if os.path.exists(cached_path):
                os.remove(cached_path)

        # Prompt for password if the cached file does not exist (the user has not logged in before or
        # they they entered the --new-pass argument.
        if not os.path.exists(cached_path):
            password = getpass()

        login = [email, password]

    try:
        client.run(*login)
    except discord.errors.LoginFailure as e:
        logging.error(e)


if __name__ == "__main__":
    main()
