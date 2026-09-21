from .parser import parse_event
from .serializer import as_json, text
from .validator import validate


def render(raw, *, json_mode=False):
    event = validate(parse_event(raw))
    return as_json(event) if json_mode else text(event)
