from __future__ import annotations

__all__ = [
    "command",
    "Command",
    "CommandClient",
    "CommandClientOptions",
    "CommandError",
    "CommandCluster",
]

import abc
import asyncio
import contextlib
import dataclasses
import enum
import importlib
import inspect
import logging
import types
import typing


from hikari.internal_utilities import aio
from hikari.internal_utilities import assertions
from hikari.internal_utilities import containers
from hikari.internal_utilities import loggers
from hikari.internal_utilities import unspecified
from hikari.orm.gateway import event_types as discord_event_types
from hikari.orm.models import bases
from hikari.orm.models import media
from hikari.orm.models import permissions
from hikari.orm import client
from hikari import errors


from reinhard.util import arg_parser


if typing.TYPE_CHECKING:
    from hikari.internal_utilities import type_hints
    from hikari.orm.http import base_http_adapter
    from hikari.orm.models import embeds
    from hikari.orm.models import guilds
    from hikari.orm.models import messages
    from hikari.orm.state import base_registry
    from hikari.orm import fabric

SEND_MESSAGE_PERMISSIONS = permissions.VIEW_CHANNEL | permissions.SEND_MESSAGES
ATTACH_FILE_PERMISSIONS = SEND_MESSAGE_PERMISSIONS | permissions.ATTACH_FILES
CHARACTERS_TO_SANITIZE = {"@": ""}

# TODO: use command hooks instead of specific stuff like get_guild_prefixes?


class CommandEvents(enum.Enum):
    ERROR = "error"
    LOAD = "load"
    UNLOAD = "unload"

    def __str__(self) -> str:
        return self.value


def sanitize_content(content: str) -> str:
    return content  # TODO: This.


class TriggerTypes(enum.Enum):
    PREFIX = enum.auto()
    MENTION = enum.auto()  # TODO: trigger commands with a mention


class PermissionError(errors.HikariError):  # TODO: don't shadow
    __slots__ = ("missing_permissions",)

    missing_permissions: permissions.Permission

    def __init__(
        self, required_permissions: permissions.Permission, actual_permissions: permissions.Permission
    ) -> None:
        pass
        # self.missing_permissions =
        # for permission in m


class Context:
    __slots__ = ("command", "content", "fabric", "message", "trigger", "trigger_type", "triggering_name")

    command: AbstractCommand

    content: str

    fabric: fabric.Fabric

    #: The message that triggered this command.
    #:
    #: :type: :class:`hikari.orm.models.messages.Message`
    message: messages.Message

    #: The string prefix or mention that triggered this command.
    #:
    #: :type: :class:`str`
    trigger: str

    #: The mention or prefix that triggered this event.
    #:
    #: :type: :class:`TriggerTypes`
    trigger_type: TriggerTypes

    #: The command alias that triggered this command.
    #:
    #: :type: :class:`str`
    triggering_name: str

    def __init__(
        self,
        fabric_obj: fabric.Fabric,
        content: str,
        message: messages.Message,
        trigger: str,
        trigger_type: TriggerTypes,
    ) -> None:
        self.fabric = fabric_obj
        self.content = content
        self.message = message
        self.trigger = trigger
        self.trigger_type = trigger_type

    @property
    def http(self) -> base_http_adapter.BaseHTTPAdapter:
        return self.fabric.http_adapter

    def prune_content(self, length: int):
        self.content = self.content[length:]

    @property
    def state(self) -> base_registry.BaseRegistry:
        return self.fabric.state_registry

    async def reply(
        self,
        *,
        content: type_hints.NotRequired[str] = unspecified.UNSPECIFIED,
        tts: bool = False,
        files: type_hints.NotRequired[typing.Collection[media.AbstractFile]] = unspecified.UNSPECIFIED,
        embed: type_hints.NotRequired[embeds.Embed] = unspecified.UNSPECIFIED,
        soft_send: bool = False,  # TODO: what was this?
        sanitize: bool = False,
    ) -> messages.Message:
        """Used to handle response length and permission checks for command responses."""
        if content is not unspecified.UNSPECIFIED and len(content) > 2000:
            files = files or containers.EMPTY_SEQUENCE
            files.append(media.InMemoryFile("message.txt", bytes(content, "utf-8")))
            content = "This response is too large to send, see attached file."
        elif content is not unspecified.UNSPECIFIED and sanitize:
            content = sanitize_content(content)

        # TODO: this needs to be easier to do on hikari's level.
        # if not files and not SEND_MESSAGE_PERMISSIONS or files and ATTACH_FILE_PERMISSIONS:
        #     raise PermissionError(ATTACH_FILE_PERMISSIONS if files else SEND_MESSAGE_PERMISSIONS)

        return await self.fabric.http_adapter.create_message(
            self.message.channel_id, content=content, tts=tts, embed=embed, files=files
        )

    def set_command_trigger(self, trigger: str):
        self.triggering_name = trigger

    def set_command(self, command_obj: AbstractCommand):
        self.command = command_obj


@dataclasses.dataclass()
class CommandClientOptions(client.client_options.ClientOptions, bases.MarshalMixin):
    access_levels: typing.MutableMapping[int, int] = dataclasses.field(default_factory=dict)
    prefixes: typing.List[str] = dataclasses.field(default_factory=list)
    # TODO: handle modules (plus maybe other stuff) here?

    def __post_init__(self) -> None:
        self.access_levels = {int(key): value for key, value in self.access_levels.items()}


class CommandError(Exception):
    __slots__ = ("response",)

    #: The string response that the client should send in chat if it has send messages permission.
    #:
    #: :type: :class:`str`
    response: str

    def __init__(self, response: str) -> None:
        self.response = response

    def __str__(self) -> str:
        return self.response


class Executable(abc.ABC):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @abc.abstractmethod
    async def check(self, ctx: Context) -> bool:
        """
        Used to check if this entity should be executed based on a Context.

        Args:
            ctx:
                The :class:`Context` object to check.

        Returns:
            The :class:`bool` of whether this executable is a match for the given context.
        """

    @abc.abstractmethod
    async def execute(self, ctx: Context) -> None:
        """
        Used to execute an entity based on a :class:`Context` object.

        Args:
            ctx:
                The :class:`Context` object to execute this with.
        """


class AbstractCommand(Executable, abc.ABC):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    #: The user access level that'll be required to execute this command, defaults to 0.
    #:
    #: :type: :class:`int`
    level: int

    meta: typing.Optional[typing.MutableMapping[typing.Any, typing.Any]]

    #: The triggers used to activate this command in chat along with a prefix.
    #:
    #: :type: :class:`typing.Tuple` of :class:`int`
    triggers: typing.Tuple[str]

    @abc.abstractmethod
    def bind_cluster(self, cluster: AbstractCommandCluster) -> None:
        ...

    @abc.abstractmethod
    def check_prefix(self, content: str) -> str:
        ...

    @abc.abstractmethod
    def check_prefix_from_context(self, ctx: Context) -> bool:
        ...

    @abc.abstractmethod
    def deregister_check(self, check: CheckLikeT) -> None:
        ...

    @abc.abstractmethod
    def register_check(self, check: CheckLikeT) -> None:
        ...

    @property
    @abc.abstractmethod
    def name(self) -> str:
        ...


class Command(AbstractCommand):
    __slots__ = ("_checks", "_cluster", "_func", "level", "meta", "parser", "triggers")

    _checks: typing.List[CheckLikeT]

    _cluster: typing.Optional[AbstractCommandCluster]

    _func: aio.CoroutineFunctionT

    parser: arg_parser.AbstractCommandParser

    def __init__(
        self,
        func: typing.Optional[aio.CoroutineFunctionT] = None,
        trigger: typing.Optional[str] = None,
        *,
        aliases: typing.Optional[typing.List[str]] = None,
        level: int = 0,
        meta: typing.Optional[typing.MutableMapping[typing.Any, typing.Any]] = None,
        cluster: typing.Optional[AbstractCommandCluster] = None,
        greedy: bool = False,
    ) -> None:
        self._checks = [self.check_prefix_from_context]
        self._func = func
        self.level = level
        self.meta = meta
        self.parser = arg_parser.CommandParser(self._func, greedy=greedy)
        if cluster:
            self.bind_cluster(cluster)
        else:
            self._cluster = None
        if not trigger:
            trigger = self.generate_trigger()
        self.triggers = tuple(trig for trig in (trigger, *(aliases or containers.EMPTY_COLLECTION)) if trig is not None)

    def __repr__(self) -> str:
        return f"Command({'|'.join(self.triggers)})"

    def bind_cluster(self, cluster: AbstractCommandCluster) -> None:
        # This ensures that `self` will automatically be passed through.
        self._func = types.MethodType(self._func, cluster)
        # This allows for calling the raw function as an attribute of the cluster.
        setattr(cluster, self._func.__name__, self._func)
        self._cluster = cluster
        # Now that we know self will automatically be passed, we need to trim the parameters again.
        self.parser.trim_parameters(1)

    def deregister_check(self, check: CheckLikeT) -> None:
        try:
            self._checks.remove(check)
        except ValueError:
            raise ValueError("Command Check not found.")

    def register_check(self, check: CheckLikeT) -> None:
        self._checks.append(check)

    async def check(self, ctx: Context) -> bool:
        result: bool = False
        ctx.set_command(self)  # TODO: is this the best way to do this?
        for check in self._checks:
            try:
                if asyncio.iscoroutinefunction(check):
                    result = await check(ctx)
                else:
                    result = check(ctx)
            except Exception:
                result = False
            else:
                if not result:
                    break
        return result

    def check_prefix(self, content: str) -> str:
        for trigger in self.triggers:
            if content.startswith(trigger):
                return trigger

    def check_prefix_from_context(self, ctx: Context) -> bool:
        for trigger in self.triggers:
            if ctx.content.startswith(trigger):
                ctx.set_command_trigger(trigger)
                return True
        return False

    async def execute(self, ctx: Context) -> None:
        try:
            args, kwargs = await self.parser.parse(ctx)
            await self._func(ctx, *args, **kwargs)
        except CommandError as exc:
            with contextlib.suppress(PermissionError):
                await ctx.reply(content=str(exc))
        except Exception as exc:
            if self._cluster:
                await self._cluster.dispatch_cluster_event(CommandEvents.ERROR, ctx, exc)  # TODO: move
            raise exc

    def generate_trigger(self) -> str:
        """Get a trigger for this command based on it's function's name."""
        return self.name.replace("_", " ")

    @property
    def name(self) -> str:
        """Get the name of this command."""
        return self._func.__name__


def command(
    __arg=..., cls: type(AbstractCommand) = Command, group: typing.Optional[str] = None, **kwargs
):  # TODO: handle groups...
    def decorator(coro_fn):
        return cls(coro_fn, **kwargs)

    return decorator if __arg is ... else decorator(__arg)


class CommandGroup(Executable):
    ...


class AbstractCommandCluster(abc.ABC):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

    @abc.abstractmethod
    def add_cluster_event(
        self, event_name: typing.Union[str, CommandEvents], coroutine_function: aio.CoroutineFunctionT
    ) -> None:
        ...

    @abc.abstractmethod
    async def get_command_from_context(self, ctx: Context) -> typing.AsyncIterator[AbstractCommand]:
        ...

    @abc.abstractmethod
    def get_command_from_name(self, content: str) -> typing.Iterator[typing.Tuple[AbstractCommand, str]]:
        """
        Get a command based on a message's content (minus prefix) from the loaded commands if any command triggers are
        found in the content.

        Args:
            content:
                The string content to try and find a command for (minus the triggering prefix).

        Returns:
            A :class:`typing.AsyncIterator` of a :class:`typing.Tuple` of a :class:`AbstractCommand`
            derived object and the :class:`str` trigger that was matched.
        """

    @abc.abstractmethod
    @typing.overload
    def register_command(self, func: AbstractCommand, bind: bool = False,) -> None:
        ...

    @abc.abstractmethod
    @typing.overload
    def register_command(
        self, func: aio.CoroutineFunctionT, *aliases: str, trigger: typing.Optional[str] = None, bind: bool = False,
    ) -> None:
        ...

    @abc.abstractmethod
    def register_command(self, func, *aliases, trigger=None, bind=False,) -> None:
        """
        Register a command in this cluster.

        Args:
            func:
                The Coroutine Function to be called when executing this command.
            *aliases:
                More string triggers for this command.
            trigger:
                The string that will be this command's main trigger.
            bind:
                If this command should be binded to the cluster. Meaning that
                self will be passed to it and it will be added as an attribute.
        """

    @abc.abstractmethod
    def unregister_command(self, command_obj: AbstractCommand) -> None:
        """
        Unregister a command in this cluster.

        Args:
            command_obj:
                The command object to remove.

        Raises:
            ValueError:
                If the passed command object wasn't found.
        """


class CommandCluster(AbstractCommandCluster):
    __slots__ = ("_event_dispatcher", "_fabric", "client", "logger", "cluster_commands")

    _event_dispatcher: aio.EventDelegate

    _fabric: typing.Optional[fabric.Fabric]

    #: The command client this cluster is loaded in.
    #:
    #: :type: :class:`AbstractCommandClient` or :class:`None`
    client: typing.Optional[AbstractCommandClient]

    #: The class wide logger.
    #:
    #: :type: :class:`logging.Logger`
    logger: logging.Logger

    #: A list of the commands that are loaded in this cluster.
    #:
    #: :type: :class:`typing.Sequence` of :class:`AbstractCommand`
    cluster_commands: typing.List[AbstractCommand]

    def __init__(
        self, command_client: typing.Optional[AbstractCommandClient] = None, bind: bool = True, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.client = command_client
        if command_client:
            self._fabric = command_client._fabric
        self._event_dispatcher = aio.EventDelegate()
        self.logger = loggers.get_named_logger(self)
        self.cluster_commands = []
        if bind:
            self.bind_commands()
            self.bind_listeners()
        self.dispatch_cluster_event(CommandEvents.LOAD, self)  # TODO: unload and do this somewhere better

    def add_cluster_event(
        self, event_name: typing.Union[str, CommandEvents], coroutine_function: aio.CoroutineFunctionT
    ) -> None:
        self.logger.debug(
            "Subscribing %s%s to %s event in %s cluster.",
            coroutine_function.__name__,
            inspect.signature(coroutine_function),
            event_name,
            self.__class__.__name__,
        )
        self._event_dispatcher.add(str(event_name), coroutine_function)

    def bind_client(self, command_client: AbstractCommandClient) -> None:
        self.client = command_client
        self._fabric = command_client._fabric

    def bind_commands(self) -> None:
        """
        Loads any commands that are attached to this class into `cluster_commands`.

        Raises:
            ValueError:
                if the commands for this cluster have already been binded or if any duplicate triggers are found while
                loading commands.
        """
        assertions.assert_that(
            not self.cluster_commands,
            f"Cannot bind commands in cluster '{self.__class__.__name__}' when commands have already been binded.",
        )
        for name, command_obj in inspect.getmembers(self, predicate=lambda attr: isinstance(attr, AbstractCommand)):
            command_obj.bind_cluster(self)
            for trigger in command_obj.triggers:
                if list(self.get_command_from_name(trigger)):
                    self.logger.warning(
                        f"Possible overlapping trigger '%s' found in %s cluster.", trigger, self.__class__.__name__,
                    )
            self.logger.debug(
                "Binded command %s in %s cluster.", command_obj.name, self.__class__.__name__,
            )
            self.cluster_commands.append(command_obj)
        self.cluster_commands.sort(key=lambda comm: comm.name, reverse=True)

    def bind_listeners(self) -> None:
        for name, function in self.get_cluster_event_listeners():
            if name not in discord_event_types.EventType.__members__.values():
                self.add_cluster_event(name, function)

    def dispatch_cluster_event(self, event: typing.Union[CommandEvents, str], *args) -> asyncio.Future:
        self.logger.debug("Dispatching %s command event in %s cluster.", str(event), self.__class__.__name__)
        return self._event_dispatcher.dispatch(str(event), *args)

    async def get_command_from_context(self, ctx: Context) -> typing.AsyncIterator[AbstractCommand]:
        for command_obj in self.cluster_commands:
            if await command_obj.check(ctx):
                yield command_obj

    def get_command_from_name(self, content: str) -> typing.Iterator[typing.Tuple[AbstractCommand, str]]:
        for command_obj in self.cluster_commands:
            if prefix := command_obj.check_prefix(content):
                yield command_obj, prefix

    def get_cluster_event_listeners(self) -> typing.Generator[typing.Tuple[str, aio.CoroutineFunctionT]]:
        """Get a generator of the event listeners attached to this cluster."""
        return (
            (name[3:], function)
            for name, function in inspect.getmembers(self, predicate=asyncio.iscoroutinefunction)
            if name.startswith("on_")
        )

    def register_command(
        self,
        func: typing.Union[aio.CoroutineFunctionT, AbstractCommand],
        *aliases: str,
        trigger: typing.Optional[str] = None,
        bind: bool = False,
    ) -> None:
        if isinstance(func, AbstractCommand):
            command_obj = func
            if bind:
                command_obj.bind_cluster(self)
        else:
            command_obj = Command(func=func, cluster=self if bind else None, trigger=trigger, aliases=list(aliases))
        for trigger in command_obj.triggers:
            if list(self.get_command_from_name(trigger)):
                self.logger.warning(
                    f"Possible overlapping trigger '%s' found in %s cluster.", trigger, self.__class__.__name__,
                )
        self.cluster_commands.append(command_obj)

    def unregister_command(self, command_obj: AbstractCommand) -> None:
        try:
            self.cluster_commands.remove(command_obj)
        except ValueError:
            raise ValueError("Invalid command passed for this cluster.") from None


class AbstractCommandClient(AbstractCommandCluster, abc.ABC):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

    @abc.abstractmethod
    async def get_global_command_from_context(self, ctx: Context) -> typing.AsyncIterator[AbstractCommand]:
        """
        Used to get a command from on a messages create event's context.

        Args:
            ctx:
                The :class:`Context` for this message create event.

        Returns:
            An async iterator of :class:`AbstractCommand` derived objects that matched this context.
        """

    @abc.abstractmethod
    def get_global_command_from_name(self, content: str) -> typing.Iterator[typing.Tuple[AbstractCommand, str]]:
        """
        Used to get a command from on a string.

        Args:
            content:
                The :class:`str` (without any prefixes) to get a command from.

        Returns:
            A :class:`typing.Iterator` of :class:`typing.Tuple` of matching
            :class:`AbstractCommand` derived objects and the :class:`str` trigger that was matched.
        """

    @abc.abstractmethod
    def load_from_modules(self, *modules: str) -> None:
        """
        Used to load modules based on string paths.

        Args:
            *modules:
                The :class:`str` paths of modules to load from (in the format of `root.dir.module`)
        """


class CommandClient(AbstractCommandClient, CommandCluster, client.Client):
    """
    The central client that all command clusters will be binded to. This extends :class:`hikari.client.Client` and
    handles registering event listeners attached to the loaded clusters and the listener(s) required for commands.

    Note:
        This inherits from :class:`CommandCluster` and can act as an independent Command Cluster for small bots.
    """

    __slots__ = ("clusters", "get_guild_prefix")

    _client_options: CommandClientOptions

    #: The command clusters that are loaded in this client.
    #:
    #: :type: :class:`typing.MutableMapping` of :class:`str` to :class:`AbstractCommandCluster`
    clusters: typing.MutableMapping[str, AbstractCommandCluster]

    get_guild_prefix: typing.Union[aio.CoroutineFunctionT, None]  # TODO: or normal method.

    def __init__(
        self,
        *,
        loop: typing.Optional[asyncio.AbstractEventLoop] = None,
        modules: typing.List[str] = None,
        options: typing.Optional[CommandClientOptions] = None,
    ) -> None:
        super().__init__(bind=False, loop=loop, options=options)
        self.clusters = {}
        self.bind_commands()
        self.load_from_modules(*(modules or containers.EMPTY_SEQUENCE))
        self.bind_listeners()

    def add_event(self, event_name: str, coroutine_function: aio.CoroutineFunctionT) -> None:
        if event_name in discord_event_types.EventType.__members__.values():
            client.Client.add_event(self, event_name, coroutine_function)
        else:
            self.add_cluster_event(event_name, coroutine_function)

    async def access_check(self, command_obj: AbstractCommand, message: messages.Message) -> bool:
        """
        Used to check if a command can be accessed by the calling user and in the calling channel/guild.

        Args:
            command_obj:
                The :class:`AbstractCommand` derived object to check access levels for.
            message:
                The :class:`messages.Message` object to check access levels for.

        Returns:
            A :class:`bool` representation of whether this command can be accessed.
        """
        return self._client_options.access_levels.get(message.author.id, 0) >= command_obj.level  # TODO: sql filter.

    def bind_listeners(self) -> None:
        """Used to add event listeners from all loaded command clusters to hikari's internal event listener."""
        for cluster in (self, *self.clusters.values()):
            for name, function in cluster.get_cluster_event_listeners():
                if name in discord_event_types.EventType.__members__.values():
                    self.add_event(name, function)
        super().bind_listeners()

    async def check_prefix(self, message: messages.Message) -> typing.Optional[str]:
        """
        Used to check if a message's content match any currently registered prefix (including any prefixes registered
        for the guild if this is being called from one.

        Args:
            message:
                The :class:`messages.Message` object that we're checking for a prefix in it's content.

        Returns:
            A :class:`str` representation of the triggering prefix if found, else :class:`None`
        """
        trigger_prefix = None
        for prefix in await self._get_prefixes(message.guild_id):
            if message.content.startswith(prefix):
                trigger_prefix = prefix
                break
        return trigger_prefix

    @property
    def config(self) -> CommandClientOptions:
        return self._client_options

    async def get_global_command_from_context(self, ctx: Context) -> typing.AsyncIterator[AbstractCommand]:
        for cluster in (self, *self.clusters.values()):
            async for command_obj in cluster.get_command_from_context(ctx):
                yield command_obj

    def get_global_command_from_name(self, content: str) -> typing.Iterator[typing.Tuple[AbstractCommand, str]]:
        yield from self.get_command_from_name(content)
        for cluster in self.clusters.values():
            yield from cluster.get_command_from_name(content)

    async def _get_prefixes(self, guild: typing.Optional[guilds.GuildLikeT]) -> typing.List[str]:
        """
        Used to get the registered global prefixes and a guild's prefix from the function `get_guild_prefix` if this is
        being called from a guild and `get_guild_prefix` has been implemented on this object.

        Args:
            guild:
                The object or ID of the guild to check or :class:`None`.

        Returns:
            An :class:`typing.Sequence` of :class:`str` representation of the applicable prefixes.
        """
        if guild is None or not hasattr(self, "get_guild_prefix"):
            return self._client_options.prefixes

        if asyncio.iscoroutinefunction(self.get_guild_prefix):
            guild_prefix = await self.get_guild_prefix(int(guild))  # TODO: maybe don't
        else:
            guild_prefix = self.get_guild_prefix(int(guild))

        return [guild_prefix, *self._client_options.prefixes] if guild_prefix else self._client_options.prefixes

    def load_from_modules(self, *modules: str) -> None:
        for module_path in modules:
            module = importlib.import_module(module_path)
            module.setup(self)

    async def on_message_create(self, message: messages.Message) -> None:
        """Handles command triggering based on message creation."""
        prefix = await self.check_prefix(message)  # TODO: maybe one day we won't have to await this.
        mention = None  # TODO: mention at end of message?
        if prefix or mention:
            command_args = message.content[len(prefix or mention) :]
        else:
            return

        ctx = Context(
            self._fabric,
            command_args,
            message,
            prefix or mention,
            TriggerTypes.PREFIX if prefix else TriggerTypes.MENTION,
        )
        async for command_obj in self.get_global_command_from_context(ctx):
            if await self.access_check(command_obj, message):
                break
            else:
                command_obj = None
        else:
            command_obj = None

        if command_obj is None:
            return

        ctx.prune_content(len(ctx.triggering_name) + 1)

        await command_obj.execute(ctx)

    def register_cluster(self, cluster: typing.Union[AbstractCommandCluster, type(AbstractCommandCluster)]):
        if inspect.isclass(cluster):
            cluster = cluster(self)
        else:
            cluster.bind_client(self)
        self.clusters[cluster.__class__.__name__] = cluster


class ReinhardCommandClient(CommandClient):
    """A custom command client with some reinhard modifications."""

    def _consume_client_loadable(self, loadable: typing.Any) -> bool:
        if inspect.isclass(loadable) and issubclass(loadable, AbstractCommandCluster):
            self.register_cluster(loadable)
        elif isinstance(loadable, AbstractCommand):  # TODO: or executable?
            self.register_command(loadable)
        elif callable(loadable):
            loadable(self)
        else:
            return False
        return True

    def load_from_modules(self, *modules: str) -> None:
        for module_path in modules:
            module = importlib.import_module(module_path)
            exports = getattr(module, "exports", containers.EMPTY_SEQUENCE)
            for item in exports:
                try:
                    item = getattr(module, item)
                except AttributeError as exc:
                    raise RuntimeError(f"`{item}` export not found in `{module_path}` module.") from exc

                if not self._consume_client_loadable(item):
                    self.logger.warning(
                        "Invalid export `%s` found in `%s.exports`", item.__class__.__name__, module_path
                    )
            else:
                self.logger.warning("No exports found in %s", module_path)


CheckLikeT = typing.Callable[[Context], typing.Union[bool, typing.Coroutine[typing.Any, typing.Any, bool]]]
