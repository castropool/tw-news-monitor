import json
import time
import logging
from datetime import datetime
from typing import Any
from pathlib import Path

logger = logging.getLogger(__name__)

class DeepSeekClient:
    def __init__(self, api_key: str, base_url: str = 'https://api.deepseek.com',
                 model: str = 'deepseek-chat', timeout: int = 180, max_retries: int = 2):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self._last_request: dict = {}

    def analyze(self, system_prompt: str, user_prompt: str) -> dict:
        import httpx
        url = f'{self.base_url}/chat/completions'
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }
        body = {
            'model': self.model,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            'temperature': 0.3,
        }
        for attempt in range(self.max_retries + 1):
            try:
                resp = httpx.post(url, headers=headers, json=body, timeout=self.timeout)
                if resp.status_code == 401:
                    logger.error('DeepSeek 401: invalid API key')
                    return {'status': 'error', 'error': 'unauthorized'}
                if resp.status_code == 403:
                    logger.error('DeepSeek 403: forbidden')
                    return {'status': 'error', 'error': 'forbidden'}
                if resp.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    logger.warning('DeepSeek 429, retrying in %ds', wait)
                    time.sleep(wait)
                    continue
                if resp.status_code >= 500 and attempt < self.max_retries:
                    wait = 2 ** (attempt + 1)
                    logger.warning('DeepSeek %d, retrying in %ds', resp.status_code, wait)
                    time.sleep(wait)
                    continue
                if resp.status_code == 400:
                    logger.error('DeepSeek 400: %s', resp.text[:500])
                    return {'status': 'error', 'error': f'bad_request: {resp.text[:200]}'}
                resp.raise_for_status()
                data = resp.json()
                content = data['choices'][0]['message']['content']
                usage = data.get('usage', {})
                content_clean = content.strip()
                if content_clean.startswith('```'):
                    content_clean = content_clean.split('\n', 1)[-1] if '\n' in content_clean else content_clean[3:]
                    if content_clean.endswith('```'):
                        content_clean = content_clean[:-3].strip()
                result = json.loads(content_clean)
                result['status'] = 'success'
                result['input_tokens'] = usage.get('prompt_tokens', 0)
                result['output_tokens'] = usage.get('completion_tokens', 0)
                return result
            except httpx.TimeoutException:
                logger.error('DeepSeek timeout')
                if attempt < self.max_retries:
                    time.sleep(2 ** (attempt + 1))
                    continue
                return {'status': 'error', 'error': 'timeout'}
            except json.JSONDecodeError:
                logger.error('DeepSeek JSON parse error')
                return {'status': 'error', 'error': 'json_parse'}
            except Exception as e:
                logger.error('DeepSeek error: %s', e)
                if attempt < self.max_retries:
                    time.sleep(2 ** (attempt + 1))
                    continue
                return {'status': 'error', 'error': str(e)}
        return {'status': 'error', 'error': 'max_retries_exceeded'}
