"""OneBot message input for the upstream media parser."""

import re


def find_match(text, patterns):
    for keyword, pattern in patterns:
        if keyword in text and (matched := pattern.search(text)):
            return keyword, matched
    return None


def session_key(event):
    group = getattr(event, 'group_id', None)
    return f'group:{group}' if group is not None else f'private:{event.user_id}'


def message_text(event, extract_json_url, reply=False):
    segments = getattr(event, 'original_message', None) or event.message
    mentions = {str(s.data.get('qq')) for s in segments if s.type == 'at'}
    for segment in segments:
        if segment.type == 'text':
            mentions.update(re.findall(r'<@!?([^>\s]+)>', segment.data.get('text', '')))
    if mentions and str(event.self_id) not in mentions and 'qq_official' not in mentions:
        return None

    def extract(items):
        text = ''.join(str(s.data.get('text', '')) for s in items if s.type == 'text')
        for segment in items:
            if segment.type == 'json':
                url = extract_json_url(segment.data.get('data', ''))
                if url:
                    return url
        return text

    text = extract(segments)
    quoted = getattr(event, 'reply', None)
    if reply and quoted and getattr(quoted, 'message', None):
        text = extract(quoted.message) or text
    return text or None
