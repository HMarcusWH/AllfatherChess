"""Audited offline-only extension for the pinned upstream extra_game_handlers interface.

Copied over upstream's optional extension in a throwaway execution copy; the
original pinned Git checkout remains immutable and verified.
"""
from lib.lichess_types import OPTIONS_TYPE
from lib import model


def game_specific_options(game: model.Game) -> OPTIONS_TYPE:
    return {}


def is_supported_extra(challenge: model.Challenge) -> bool:
    return (
        challenge.variant == "standard"
        and challenge.initial_fen == "startpos"
        and challenge.speed == "rapid"
        and challenge.base == 600
        and challenge.increment == 5
        and not challenge.rated
        and challenge.challenger.is_bot
        and challenge.challenger.name.lower() == "approved_test_bot"
    )
