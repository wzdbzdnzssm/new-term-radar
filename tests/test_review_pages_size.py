import io
from email.message import Message

from radar.ai_monitor import review_pages, templates


class Response(io.BytesIO):
    status = 200

    def __init__(self, body):
        super().__init__(body)
        self.headers = Message()
        self.headers['Content-Type'] = 'text/html; charset=utf-8'

    def geturl(self):
        return 'https://example.com/motion/large'


class Opener:
    def __init__(self, body):
        self.body = body

    def open(self, request, timeout):
        return Response(self.body)


def test_large_inline_images_preserve_complete_tail_evidence():
    body = (b'<html><img src="data:image/webp;base64,' + b'A' * 5000000
            + b'" alt="Preview"><title>Large video</title><h1>Tail Motion</h1>'
            + b'<script type="application/ld+json">{"@type":"VideoObject","name":"Structured Motion"}</script>'
            + b'<p>' + b'Complete video instructions. ' * 10 + b'</p>'
            + b'<a href="/motion/tail"><img alt="Tail Card"><video src="/tail.mp4"></video></a></html>')
    record = review_pages.read_page('https://example.com/motion/large', Opener(body))
    assert record['valid'] and record['raw_html'].encode() == body
    assert 'Complete video instructions.' in record['text']
    parser = templates.Cards(record['final_url'])
    parser.feed(record['raw_html'])
    assert parser.h1 == ['Tail Motion']
    assert templates.structured_name(parser.structured) == 'Structured Motion'
    assert parser.cards[0]['name'] == 'Tail Card'
    assert parser.references == ['https://example.com/tail.mp4']


def test_evidence_html_limit_rejects_incomplete_document():
    body = b'x' * (review_pages.MAX_HTML_BYTES + 1)
    record = review_pages.read_page('https://example.com/motion/large', Opener(body))
    assert not record['valid']
    assert record['error'] == 'Evidence HTML exceeds 15MB'
    assert 'raw_html' not in record and 'content_hash' not in record
