"""Preserve the single-image contract and send paginated cards as one message."""
def image_message(pages):
    from nonebot.adapters.onebot.v11 import Message, MessageSegment
    segments=[MessageSegment.image(page) for page in pages]
    if not segments:
        raise ValueError('empty rendered card')
    return segments[0] if len(segments)==1 else Message(segments)
