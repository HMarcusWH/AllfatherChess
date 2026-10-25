"""Pinned upstream hook: independently refuse any challenge not in signed lease."""
import os
import re
from lib.lichess_types import OPTIONS_TYPE
from lib import model

def game_specific_options(game: model.Game) -> OPTIONS_TYPE:
    return {}

def is_supported_extra(challenge: model.Challenge) -> bool:
    approved=os.environ.get("ALLFATHER_APPROVED_OPPONENT","")
    return (
        bool(re.fullmatch(r"[A-Za-z0-9_-]{2,30}",approved))
        and challenge.variant=="standard"
        and challenge.initial_fen=="startpos"
        and challenge.speed=="rapid"
        and type(challenge.base) is int and challenge.base==600
        and type(challenge.increment) is int and challenge.increment==5
        and challenge.rated is False
        and challenge.challenger.is_bot is True
        and challenge.challenger.name.casefold()==approved.casefold()
    )
