from .soap import parse_envelope, parse_items

def decode(body, headers):
    return parse_items(parse_envelope(body))
