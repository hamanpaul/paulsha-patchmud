"""Deck 子套件：issue card 契約、fixture 物化、provenance。"""

from patchmud.deck.loader import load_card
from patchmud.deck.materialize import FrozenRepo, materialize_repo
from patchmud.deck.model import DeckError, IssueCard

__all__ = ["DeckError", "FrozenRepo", "IssueCard", "load_card", "materialize_repo"]
