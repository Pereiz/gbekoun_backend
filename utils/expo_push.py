import json
import os
from urllib.request import Request, urlopen


EXPO_PUSH_URL = 'https://exp.host/--/api/v2/push/send'


def send_expo_messages(messages):
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
    access_token = os.getenv('EXPO_ACCESS_TOKEN')
    if access_token:
        headers['Authorization'] = f'Bearer {access_token}'

    request = Request(
        EXPO_PUSH_URL,
        data=json.dumps(messages).encode('utf-8'),
        headers=headers,
        method='POST',
    )
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode('utf-8'))
