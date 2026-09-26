"""Render a small Markdown subset as safe, independently valid Telegram HTML."""
from html import escape
import re

# Match code first so Markdown inside code remains literal. Unsupported or
# unmatched markup stays readable as text rather than becoming invalid HTML.
TOKEN = re.compile(
    r'(?P<fence>```[^\n`]*\n(?P<block>[\s\S]*?)```)' 
    r'|(?P<inline>`(?P<code>[^`\n]+)`)' 
    r'|(?P<bold>\*\*(?P<strong>\S(?:[\s\S]*?\S)?)\*\*)'
    r'|(?P<strike>~~(?P<deleted>\S(?:[^\n]*?\S)?)~~)'
    r'|(?P<italic>(?<![\w*])\*(?P<em>[^*\n]+)\*(?!\w))'
)


def spans(text):
    position = 0
    for match in TOKEN.finditer(text):
        if match.start() > position:
            yield text[position:match.start()], ''
        for group, tag in [('block', 'pre'), ('code', 'code'),
                           ('strong', 'b'), ('deleted', 's'), ('em', 'i')]:
            if match.group(group) is not None:
                yield match.group(group), tag
                break
        position = match.end()
    if position < len(text):
        yield text[position:], ''


def formatted_chunks(text, limit=4000):
    """Yield (HTML, plain text) pairs; split by displayed UTF-16 length.

    Escape each fragment after splitting so neither HTML entities nor tags get
    cut in half. Close/reopen styling when a span crosses a message boundary.
    """
    if limit < 2:
        raise ValueError('Chunk limit must be at least 2.')
    html_parts, plain_parts, size = [], [], 0
    for value, tag in spans(text):
        fragment = []

        def append_fragment():
            if fragment:
                plain = ''.join(fragment)
                encoded = escape(plain, quote=False)
                html_parts.append(f'<{tag}>{encoded}</{tag}>' if tag else encoded)
                plain_parts.append(plain)
                fragment.clear()

        for char in value:
            width = 2 if ord(char) > 0xFFFF else 1
            if size + width > limit:
                append_fragment()
                yield ''.join(html_parts), ''.join(plain_parts)
                html_parts, plain_parts, size = [], [], 0
            fragment.append(char)
            size += width
        append_fragment()
    if plain_parts:
        yield ''.join(html_parts), ''.join(plain_parts)
