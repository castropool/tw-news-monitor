import json
import time
import logging
from datetime import datetime
from typing import Any
from pathlib import Path
from urllib.parse import urlparse

from . import ai_usage

logger = logging.getLogger(__name__)


def parse_json_content(content: str) -> dict:
    """Model output (optionally fenced) as a JSON object; raises JSONDecodeError."""
    content_clean = (content or '').strip()
    if content_clean.startswith('```'):
        content_clean = content_clean.split('\n', 1)[-1] if '\n' in content_clean else content_clean[3:]
        if content_clean.endswith('```'):
            content_clean = content_clean[:-3].strip()
    return json.loads(content_clean)


class DeepSeekClient:
    """The single entry point for model calls (OpenAI-compatible chat API).

    Each call is checked against the monthly AI budget first and logged to
    the usage ledger afterwards (see app/ai_usage.py).  ``task`` labels the
    ledger rows (e.g. ``summary``, ``translation``).
    """

    def __init__(self, api_key: str, base_url: str = 'https://api.deepseek.com',
                 model: str = 'deepseek-chat', timeout: int = 180, max_retries: int = 2,
                 task: str = 'unspecified'):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.task = task
        self._last_request: dict = {}
        self._last_content = ''

    @property
    def provider(self) -> str:
        host = urlparse(self.base_url).hostname or self.base_url
        return 'local-gateway' if host in ('127.0.0.1', 'localhost', '::1') else host

    def analyze(self, system_prompt: str, user_prompt: str) -> dict:
        key = ai_usage.cache_key(self.model, system_prompt, user_prompt) if ai_usage.cache_enabled() else None
        if key:
            cached = ai_usage.cache_get(key)
            if cached is not None:
                try:
                    result = parse_json_content(cached)
                except (json.JSONDecodeError, TypeError):
                    result = None
                if isinstance(result, dict):
                    ai_usage.record_call(task=self.task, provider=self.provider, model=self.model,
                                         status='cache_hit')
                    result.update(status='success', input_tokens=0, output_tokens=0, cached=True)
                    return result
        try:
            ai_usage.check_budget()
        except ai_usage.BudgetExceeded as exc:
            logger.warning('AI call skipped (%s): %s', self.task, exc)
            ai_usage.record_call(task=self.task, provider=self.provider, model=self.model,
                                 status='blocked', error=str(exc))
            return {'status': 'error', 'error': 'budget_exceeded'}
        self._last_content = ''
        started = time.monotonic()
        result = self._analyze(system_prompt, user_prompt)
        ai_usage.record_call(
            task=self.task, provider=self.provider, model=self.model,
            status='success' if result.get('status') == 'success' else 'error',
            prompt_text=system_prompt + user_prompt, output_text=self._last_content,
            input_tokens=int(result.get('input_tokens') or 0),
            output_tokens=int(result.get('output_tokens') or 0),
            duration_ms=int((time.monotonic() - started) * 1000),
            error=None if result.get('status') == 'success' else str(result.get('error')),
        )
        if key and result.get('status') == 'success' and self._last_content:
            ai_usage.cache_put(key, self.model, self._last_content)
        return result

    def _analyze(self, system_prompt: str, user_prompt: str) -> dict:
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
                self._last_content = content or ''
                usage = data.get('usage', {})
                result = parse_json_content(content)
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
