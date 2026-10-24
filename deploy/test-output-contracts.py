"""Live read-only text probes; credentials come from an operator-only environment file.

No cookies, keys or full responses are logged. This is not an agent executor.
"""
import argparse
import json
import os
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base-url', default='http://127.0.0.1:8083/v1')
parser.add_argument('--model', required=True)
args = parser.parse_args()
key = os.environ.get('GEMINI_TEST_API_KEY')
if not key:
    raise SystemExit('Set GEMINI_TEST_API_KEY privately')
opener = build_opener(ProxyHandler({}))
cases = [
    ('default', 'Return exactly RESPONSE_READY', None),
    ('json', 'Return a JSON object with ready set to true.', {'output_mode': 'json'}),
    ('code-only', 'Write Python code that prints READY.', {'output_mode': 'code-only'}),
]
for label, prompt, options in cases:
    body = {'model': args.model, 'stream': False, 'messages': [{'role': 'user', 'content': prompt}]}
    if options is not None:
        body['gemini_web_options'] = options
    request = Request(args.base_url.rstrip('/') + '/chat/completions', data=json.dumps(body).encode(),
                      headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    start = time.monotonic()
    try:
        with opener.open(request, timeout=200) as response:
            result = json.load(response)
        text = result.get('choices', [{}])[0].get('message', {}).get('content', '') or ''
        passed = text.strip() == 'RESPONSE_READY' if label == 'default' else bool(text.strip())
        if label == 'json':
            passed = isinstance(json.loads(text), dict)
        if label == 'code-only':
            passed = passed and '```' not in text
        print(json.dumps({'case': label, 'seconds': round(time.monotonic()-start, 2), 'passed': passed, 'responseChars': len(text)}))
    except HTTPError as error:
        print(json.dumps({'case': label, 'seconds': round(time.monotonic()-start, 2), 'http': error.code, 'passed': False}))
    except Exception as error:
        print(json.dumps({'case': label, 'errorType': type(error).__name__, 'passed': False}))
