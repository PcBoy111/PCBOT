""" Plugin for built-in commands.

This script works just like any of the plugins in plugins/
"""

import random
import logging
from datetime import datetime
import importlib

import discord
import asyncio

from pcbot import utils, Config, Annotate
import plugins


lambdas = Config("lambdas", data={})
lambda_config = Config("lambda-config", data=dict(imports=[], blacklist=[]))

code_globals = {}
code_locals = {}


@plugins.command(name="help", usage="[command]")
def help_(client: discord.Client, message: discord.Message, name: str.lower=None):
    """ Display commands or their usage and description. """
    if name:  # Display the specific command
        if name.startswith(utils.command_prefix):
            name = name[1:]

        usage, desc = "", ""

        for plugin in plugins.all_values():
            command = utils.get_command(plugin, name)

            if not command:
                continue

            usage = command.usage
            desc = command.description

            # Notify the user when a command is owner specific
            if getattr(command.function, "__owner__", False):
                desc += "\n:information_source:`Only the bot owner can execute this command.`"

        if usage:
            yield from client.say(message, "**Usage**: ```{}```**Description**: {}".format(usage, desc))
        else:
            yield from client.say(message, "Command `{}` does not exist.".format(name))
    else:  # Display every command
        commands = []

        for plugin in plugins.all_values():
            if getattr(plugin, "__commands", False):  # Massive pile of shit that works (so sorry)
                commands.extend(
                    cmd.usage.split()[0] for cmd in plugin.__commands
                    if not cmd.hidden and
                    (not getattr(getattr(cmd, "function"), "__owner__", False) or
                     utils.is_owner(message.author))
                )

        commands = ", ".join(sorted(commands))

        m = "**Commands:**```{}```Use `!help <command>` for command specific help.".format(commands)
        yield from client.say(message, m)


@plugins.command(hidden=True)
def setowner(client: discord.Client, message: discord.Message):
    """ Set the bot owner. Only works in private messages. """
    if not message.channel.is_private:
        return

    if utils.owner_cfg.data:
        yield from client.say(message, "An owner is already set.")
        return

    owner_code = str(random.randint(100, 999))
    logging.critical("Owner code for assignment: {}".format(owner_code))

    yield from client.client.say(message,
                                 "A code has been printed in the console for you to repeat within 60 seconds.")
    user_code = yield from client.wait_for_message(timeout=60, channel=message.channel, content=owner_code)

    if user_code:
        yield from client.client.say(message, "You have been assigned bot owner.")
        utils.owner_cfg.data = message.author.id
        utils.owner_cfg.save()
    else:
        yield from client.client.say(message, "You failed to send the desired code.")


@plugins.command()
@utils.owner
def stop(client: discord.Client, message: discord.Message):
    """ Stops the bot. """
    yield from client.say(message, ":boom: :gun:")
    yield from plugins.save_plugins()
    yield from client.logout()


@plugins.command(usage="[name ...]")
@utils.owner
def game(client: discord.Client, message: discord.Message, name: Annotate.Content=""):
    """ Stop playing or set game to `game`. """
    yield from client.change_status(discord.Game(name=name))

    if name:
        yield from client.say(message, "*Set the game to* **{}**.".format(name))
    else:
        yield from client.say(message, "*No longer playing.*")


@plugins.command(usage="<python code ...>")
@utils.owner
def do(client: discord.Client, message: discord.Message, script: Annotate.Code):
    """ Execute python code. Coroutines do not work, although you can run `say(msg, c=message.channel)`
        to send a message, optionally to a channel. Eg: `say("Hello!")`. """
    def say(msg, m=message):
        asyncio.async(client.say(m, msg))

    code_locals.update(dict(say=say, message=message))

    try:
        exec(script, code_globals, code_locals)
    except Exception as e:
        say("```" + utils.format_exception(e) + "```")


@plugins.command(name="eval", usage="<python code ...>")
@utils.owner
def eval_(client: discord.Client, message: discord.Message,
             script: Annotate.Code):
    """ Evaluate a python expression. Can be any python code on one line that returns something. """
    code_locals["message"] = message

    try:
        result = eval(script, code_globals, code_locals)
    except Exception as e:
        result = utils.format_exception(e)

    yield from client.say(message, "**Result:** \n```{}\n```".format(result))


@plugins.command(name="plugin", usage="[reload | load | unload] [plugin]")
def plugin_(client: discord.Client, message: discord.Message):
    """ Manage plugins.
        **Owner command unless no argument is specified.** """
    yield from client.say(message,
                          "**Plugins:** ```\n" "{}```".format(",\n".join(plugins.all_keys())))


@plugin_.command()
@utils.owner
def reload(client: discord.Client, message: discord.Message, name: str.lower=None):
    """ Reloads a plugin. """
    if name:
        if plugins.get_plugin(name):
            yield from plugins.save_plugin(name)
            plugins.reload_plugin(name)

            yield from client.say(message, "Reloaded plugin `{}`.".format(name))
        else:
            # No such plugin
            yield from client.say(message, "`{}` is not a plugin. See `!plugin`.".format(name))
    else:
        # Reload all plugins
        yield from plugins.save_plugins()

        for plugin_name in plugins.all_keys():
            plugins.reload_plugin(plugin_name)

        yield from client.say(message, "All plugins reloaded.")


@plugin_.command(error="You need to specify the name of the plugin to load.")
@utils.owner
def load(client: discord.Client, message: discord.Message, name: str.lower):
    """ Loads a plugin. """
    if not plugins.get_plugin(name):
        loaded = plugins.load_plugin(name)

        if loaded:
            yield from client.say(message, "Plugin `{}` loaded.".format(name))
        else:
            yield from client.say(message, "Plugin `{}` could not be loaded.".format(name))
    else:
        yield from client.say(message, "Plugin `{}` is already loaded.".format(name))


@plugin_.command(error="You need to specify the name of the plugin to unload.")
@utils.owner
def unload(client: discord.Client, message: discord.Message, name: str.lower):
    """ Unloads a plugin. """
    if plugins.get_plugin(name):
        yield from plugins.save_plugin(name)
        plugins.unload_plugin(name)

        yield from client.say(message, "Plugin `{}` unloaded.".format(name))
    else:
        yield from client.say(message, "`{}` is not a plugin. See `!plugin`.".format(name))


@plugins.command(name="lambda", usage="[add <trigger> <python code> | [remove | enable | disable | source | "
                                      "import <module> [attribute]] <trigger>]")
def lambda_(client: discord.Client, message: discord.Message):
    """ Create commands. See `!help do` for information on how the code works.

        **In addition**, there's the `arg(i, default=0)` function for getting arguments in positions,
        where the default argument is what to return when the argument does not exist.
        **Owner command unless no argument is specified.**"""
    yield from client.say(message,
                          "**Lambdas:** ```\n" "{}```".format(", ".join(lambdas.data.keys())))


@lambda_.command()
@utils.owner
def add(client: discord.Client, message: discord.Message, trigger: str.lower, script: Annotate.Code):
    """ Add a command that runs the specified script. """
    if trigger not in lambdas.data:
        lambdas.data[trigger] = script
        lambdas.save()
        yield from client.say(message, "Command `{}` set.".format(trigger))
    else:
        yield from client.say(message, "Command `{}` already exists.".format(trigger))


@lambda_.command()
@utils.owner
def remove(client: discord.Client, message: discord.Message, trigger: str.lower):
    """ Remove a command. """
    if trigger in lambdas.data:
        lambdas.data.pop(trigger)
        lambdas.save()
        yield from client.say(message, "Command `{}` removed.".format(trigger))
    else:
        yield from client.say(message, "Command `{}` does not exist.".format(trigger))


@lambda_.command()
@utils.owner
def enable(client: discord.Client, message: discord.Message, trigger: str.lower):
    """ Enable a command. """
    if trigger in lambda_config.data["blacklist"]:
        del lambda_config.data["blacklist"][trigger]
        lambda_config.save()
        yield from client.say(message, "Command `{}` enabled.".format(trigger))
    else:
        if trigger in lambdas.data:
            yield from client.say(message, "Command `{}` is already enabled.".format(trigger))
        else:
            yield from client.say(message, "Command `{}` does not exist.".format(trigger))


@lambda_.command()
@utils.owner
def disable(client: discord.Client, message: discord.Message, trigger: str.lower):
    """ Disable a command. """
    if trigger in lambda_config.data["blacklist"]:
        lambda_config.data["blacklist"].append(trigger)
        lambda_config.save()
        yield from client.say(message, "Command `{}` disabled.".format(trigger))
    else:
        if trigger in lambdas.data:
            yield from client.say(message, "Command `{}` is already disabled..".format(trigger))
        else:
            yield from client.say(message, "Command `{}` does not exist.".format(trigger))


def import_module(module: str, attr: str=None):
    """ Remotely import a module or attribute from module into code_globals. """
    try:
        imported = importlib.import_module(module)
    except ImportError:
        e = "Unable to import module {}.".format(module)
        logging.error(e)
        raise ImportError(e)
    else:
        if attr:
            if hasattr(imported, attr):
                code_globals[attr] = getattr(imported, attr)
            else:
                e = "Module {} has no attribute {}.".format(module, attr)
                logging.error(e)
                raise KeyError(e)
        else:
            code_globals[module] = imported


@lambda_.command(name="import")
@utils.owner
def import_(client: discord.Client, message: discord.Message, module: str, attr: str=None):
    """ Things to import. """
    try:
        import_module(module, attr)
    except ImportError:
        yield from client.say(message, "Unable to import `{}`.".format(module))
    except KeyError:
        yield from client.say(message, "Unable to import `{}` from `{}`.".format(attr, module))
    else:
        lambda_config.data["imports"].append((module, attr))
        lambda_config.save()
        yield from client.say(message, "Imported and setup `{}` for import.".format(attr or module))


@lambda_.command()
def source(client: discord.Client, message: discord.Message, trigger: str.lower):
    """ Disable source of a command """
    if trigger in lambdas.data:
        yield from client.say(message, "Source for `{}`:\n{}".format(trigger, lambdas.data[trigger]))
    else:
        yield from client.say(message, "Command `{}` does not exist.".format(trigger))


@plugins.command()
def ping(client: discord.Client, message: discord.Message):
    """ Tracks the time spent parsing the command and sending a message. """
    # Track the time it took to receive a message and send it.
    start_time = datetime.now()
    first_message = yield from client.say(message, "Pong!")
    stop_time = datetime.now()

    # Edit our message with the tracked time (in ms)
    time_elapsed = (stop_time - start_time).total_seconds() / 1000
    yield from client.edit_message(first_message,
                                   "Pong! `{elapsed:.4f}ms`".format(elapsed=time_elapsed))


@plugins.command()
def uptime(client: discord.Client, message: discord.Message):
    """ Return the time this bot started. """
    yield from client.say(message, "I've been running since `{}`.".format(client.time_started.ctime()))


@asyncio.coroutine
def on_ready(client: discord.Client):
    """ Import any imports for lambdas. """
    global code_locals

    for module, attr in lambda_config.data["imports"]:
        import_module(module, attr)

    code_locals = dict(
        client=client,
        utils=utils,
        datetime=datetime,
        random=random
    )


@asyncio.coroutine
def on_message(client: discord.Client, message: discord.Message, args: list):
    """ Perform lambda commands. """
    if args[0] in lambdas.data and args[0] not in lambda_config.data["blacklist"]:
        def say(msg, m=message):
            asyncio.async(client.say(m, msg))

        def arg(i, default=0):
            if len(args) > i:
                return args[i]
            else:
                return default

        code_locals.update(dict(arg=arg, say=say, args=args))

        try:
            exec(lambdas.data[args[0]], code_globals, code_locals)
        except Exception as e:
            if utils.is_owner(message.author):
                say("```" + utils.format_exception(e) + "```")

        return True

    return False
