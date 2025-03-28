# -*- coding: utf-8 -*-
# BSD 3-Clause License
#
# Copyright (c) 2020-2025, Faster Speeding
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
from __future__ import annotations

__all__: list[str] = ["DatabaseConfig", "FullConfig", "Tokens"]

import abc
import dataclasses
import logging
import os
import pathlib
import types
import typing
from collections import abc as collections

import dotenv
import hikari

if typing.TYPE_CHECKING:
    from typing import Self

ConfigT = typing.TypeVar("ConfigT", bound="Config")
DefaultT = typing.TypeVar("DefaultT")
ValueT = typing.TypeVar("ValueT")


@typing.overload
def _cast_or_else(
    data: collections.Mapping[str, typing.Any], key: str, cast: collections.Callable[[typing.Any], ValueT]
) -> ValueT: ...


@typing.overload
def _cast_or_else(
    data: collections.Mapping[str, typing.Any],
    key: str,
    cast: collections.Callable[[typing.Any], ValueT],
    default: DefaultT,
) -> ValueT | DefaultT: ...


def _cast_or_else(
    data: collections.Mapping[str, typing.Any],
    key: str,
    cast: collections.Callable[[typing.Any], ValueT],
    default: DefaultT | types.EllipsisType = ...,
) -> ValueT | DefaultT:
    try:
        return cast(data[key])
    except KeyError:
        if default is not ...:
            return default

    raise KeyError(f"{key!r} required environment/config key missing")


class Config(abc.ABC):
    __slots__ = ()

    @classmethod
    @abc.abstractmethod
    def from_env(cls) -> Self:
        raise NotImplementedError

    @classmethod
    @abc.abstractmethod
    def from_mapping(cls, mapping: collections.Mapping[str, typing.Any], /) -> Self:
        raise NotImplementedError


def _maybe_up(string: str, up: bool) -> str:
    return string.upper() if up else string


@dataclasses.dataclass(kw_only=True, repr=False, slots=True)
class DatabaseConfig(Config):
    password: str
    database: str = "postgres"
    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"

    @classmethod
    def from_env(cls) -> Self:
        return cls.from_mapping(os.environ, _up_case=True)

    @classmethod
    def from_mapping(cls, mapping: collections.Mapping[str, typing.Any], /, *, _up_case: bool = False) -> Self:
        return cls(
            password=_cast_or_else(mapping, _maybe_up("database_password", _up_case), str),
            database=_cast_or_else(mapping, _maybe_up("database", _up_case), str, "postgres"),
            host=_cast_or_else(mapping, _maybe_up("database_host", _up_case), str, "localhost"),
            port=_cast_or_else(mapping, _maybe_up("database_port", _up_case), int, 5432),
            user=_cast_or_else(mapping, _maybe_up("database_user", _up_case), str, "postgres"),
        )


@dataclasses.dataclass(kw_only=True, repr=False, slots=True)
class PTFConfig(Config):
    auth_service: str
    file_service: str
    message_service: str
    password: str
    username: str

    @classmethod
    def from_env(cls) -> Self:
        return cls.from_mapping(os.environ, _up_case=True)

    @classmethod
    def from_mapping(cls, mapping: collections.Mapping[str, typing.Any], /, *, _up_case: bool = False) -> Self:
        return cls(
            auth_service=_cast_or_else(mapping, _maybe_up("auth_service", _up_case), str),
            file_service=_cast_or_else(mapping, _maybe_up("file_service", _up_case), str),
            message_service=_cast_or_else(mapping, _maybe_up("message_service", _up_case), str),
            username=_cast_or_else(mapping, _maybe_up("ptf_username", _up_case), str),
            password=_cast_or_else(mapping, _maybe_up("ptf_password", _up_case), str),
        )


@dataclasses.dataclass(kw_only=True, repr=False, slots=True)
class Tokens(Config):
    bot: str
    google: str | None = None
    spotify_id: str | None = None
    spotify_secret: str | None = None

    @classmethod
    def from_env(cls) -> Self:
        return cls.from_mapping(os.environ, _up_case=True)

    @classmethod
    def from_mapping(cls, mapping: collections.Mapping[str, typing.Any], /, *, _up_case: bool = False) -> Self:
        return cls(
            bot=str(mapping[_maybe_up("token", _up_case)]),
            google=_cast_or_else(mapping, _maybe_up("google", _up_case), str, None),
            spotify_id=_cast_or_else(mapping, _maybe_up("spotify_id", _up_case), str, None),
            spotify_secret=_cast_or_else(mapping, _maybe_up("spotify_secret", _up_case), str, None),
        )


DEFAULT_CACHE: typing.Final[hikari.api.CacheComponents] = (
    hikari.api.CacheComponents.GUILDS
    | hikari.api.CacheComponents.GUILD_CHANNELS
    | hikari.api.CacheComponents.ROLES
    # | hikari.CacheComponents.ME
)

DEFAULT_INTENTS: typing.Final[hikari.Intents] = hikari.Intents.GUILDS | hikari.Intents.ALL_MESSAGES


@typing.overload
def _str_to_bool(value: str, /) -> bool: ...


@typing.overload
def _str_to_bool(value: str, /, *, default: ValueT) -> bool | ValueT: ...


def _str_to_bool(value: str, /, *, default: ValueT | types.EllipsisType = ...) -> bool | ValueT:
    if value in ("true", "True", "1"):
        return True

    if value in ("false", "False", "0"):
        return False

    if default is not ...:
        return default

    raise ValueError(f"{value!r} is not a valid boolean")


def _parse_ids(values: collections.Sequence[int] | str) -> set[hikari.Snowflake]:
    if isinstance(values, str):
        return {hikari.Snowflake(value.strip()) for value in values.split(",")}

    return {hikari.Snowflake(value) for value in values}


_DEFAULT_EVAL_GUILDS = frozenset((hikari.Snowflake(561884984214814744), hikari.Snowflake(574921006817476608)))


@dataclasses.dataclass(kw_only=True, repr=False, slots=True)
class FullConfig(Config):
    database: DatabaseConfig
    tokens: Tokens
    cache: hikari.api.CacheComponents = DEFAULT_CACHE
    emoji_guild: hikari.Snowflake | None = None
    intents: hikari.Intents = DEFAULT_INTENTS
    log_level: int | str | None = logging.INFO
    mention_prefix: bool = True
    owner_only: bool = False
    prefixes: collections.Set[str] = frozenset()
    ptf: PTFConfig | None = None
    declare_global_commands: bool | hikari.Snowflake = True
    hot_reload: bool = False
    eval_guilds: collections.Set[hikari.Snowflake] = _DEFAULT_EVAL_GUILDS

    @classmethod
    def from_env(cls) -> Self:
        dotenv.load_dotenv()

        return cls(
            cache=_cast_or_else(os.environ, "CACHE", hikari.api.CacheComponents, DEFAULT_CACHE),
            database=DatabaseConfig.from_env(),
            emoji_guild=_cast_or_else(os.environ, "EMOJI_GUILD", hikari.Snowflake, None),
            intents=_cast_or_else(os.environ, "INTENTS", hikari.Intents, DEFAULT_INTENTS),
            log_level=_cast_or_else(os.environ, "LOG_LEVEL", lambda v: int(v) if v.isdigit() else v, logging.INFO),
            mention_prefix=_cast_or_else(os.environ, "MENTION_PREFIX", _str_to_bool, True),
            owner_only=_cast_or_else(os.environ, "OWNER_ONLY", _str_to_bool, False),
            prefixes=_cast_or_else(os.environ, "PREFIXES", lambda v: set(map(str, v)), set[str]()),
            ptf=PTFConfig.from_env() if os.getenv("PTF_USERNAME") else None,
            tokens=Tokens.from_env(),
            declare_global_commands=_cast_or_else(
                os.environ,
                "DECLARE_GLOBAL_COMMANDS",
                lambda v: nv if (nv := _str_to_bool(v, default=None)) is not None else hikari.Snowflake(v),
                True,
            ),
            hot_reload=_cast_or_else(os.environ, "HOT_RELOAD", _str_to_bool, False),
            eval_guilds=_cast_or_else(os.environ, "EVAL_GUILDS", _parse_ids, _DEFAULT_EVAL_GUILDS),
        )

    @classmethod
    def from_mapping(cls, mapping: collections.Mapping[str, typing.Any], /) -> Self:
        log_level = mapping.get("log_level", logging.INFO)
        if not isinstance(log_level, (str, int)):
            raise TypeError("Invalid log level found in config")

        elif isinstance(log_level, str):
            log_level = log_level.upper()

        declare_global_commands = mapping.get("declare_global_commands", True)
        if not isinstance(declare_global_commands, bool):
            declare_global_commands = hikari.Snowflake(declare_global_commands)

        return cls(
            cache=_cast_or_else(mapping, "cache", hikari.api.CacheComponents, DEFAULT_CACHE),
            database=DatabaseConfig.from_mapping(mapping["database"]),
            emoji_guild=_cast_or_else(mapping, "emoji_guild", hikari.Snowflake, None),
            intents=_cast_or_else(mapping, "intents", hikari.Intents, DEFAULT_INTENTS),
            log_level=log_level,
            mention_prefix=bool(mapping.get("mention_prefix", True)),
            owner_only=bool(mapping.get("owner_only", False)),
            prefixes=set(map(str, mapping["prefixes"])) if "prefixes" in mapping else set(),
            ptf=_cast_or_else(mapping, "ptf", PTFConfig.from_mapping, None),
            tokens=Tokens.from_mapping(mapping["tokens"]),
            declare_global_commands=declare_global_commands,
            eval_guilds=_cast_or_else(mapping, "eval_guilds", _parse_ids, _DEFAULT_EVAL_GUILDS),
        )


def get_config_from_file(path: pathlib.Path | None = None, /) -> FullConfig:
    import yaml

    if path is None:
        path = pathlib.Path("config.json")
        path = pathlib.Path("config.yaml") if not path.exists() else path

        if not path.exists():
            raise RuntimeError("Couldn't find valid yaml or json configuration file")

    data = path.read_text()
    return FullConfig.from_mapping(yaml.safe_load(data))


def load_config() -> FullConfig:
    config_location = os.getenv("REINHARD_CONFIG_FILE")
    config_path = pathlib.Path(config_location) if config_location else None

    if config_path and not config_path.exists():
        raise RuntimeError("Invalid configuration given in environment variables")

    return get_config_from_file(config_path)


hikari.Snowflake(123321)
