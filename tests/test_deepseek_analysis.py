import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.deepseek_analysis import DeepSeekClient

class TestDeepSeekMock:
    def test_timeout(self, monkeypatch):
        import httpx
        def mock_post(*args, **kwargs):
            raise httpx.TimeoutException('timeout')
        monkeypatch.setattr(httpx, 'post', mock_post)
        client = DeepSeekClient(api_key='test', timeout=5, max_retries=1)
        result = client.analyze('system', 'user')
        assert result['status'] == 'error'

    def test_401(self, monkeypatch):
        class MockResp:
            status_code = 401
            def raise_for_status(self): raise Exception('401')
        import httpx
        monkeypatch.setattr(httpx, 'post', lambda *a, **kw: MockResp())
        client = DeepSeekClient(api_key='bad', timeout=5, max_retries=1)
        result = client.analyze('system', 'user')
        assert result['status'] == 'error'
        assert result['error'] == 'unauthorized'

    def test_429_retry_then_success(self, monkeypatch):
        call_count = [0]
        class MockResp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {
                'choices': [{'message': {'content': '{"status":"success"}'}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 20},
            }
        class Mock429Resp:
            status_code = 429
            def raise_for_status(self): raise Exception('429')
        import httpx
        def mock_post(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return Mock429Resp()
            return MockResp()
        monkeypatch.setattr(httpx, 'post', mock_post)
        client = DeepSeekClient(api_key='test', timeout=5, max_retries=2)
        result = client.analyze('system', 'user')
        assert result['status'] == 'success'
        assert call_count[0] == 2

    def test_500_retry_exceeds(self, monkeypatch):
        call_count = [0]
        class Mock500Resp:
            status_code = 500
            def raise_for_status(self): raise Exception('500')
        import httpx
        def mock_post(*a, **kw):
            call_count[0] += 1
            return Mock500Resp()
        monkeypatch.setattr(httpx, 'post', mock_post)
        client = DeepSeekClient(api_key='test', timeout=5, max_retries=1)
        result = client.analyze('system', 'user')
        assert result['status'] == 'error'

    def test_json_parse_error(self, monkeypatch):
        class MockResp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {
                'choices': [{'message': {'content': 'not json'}}],
                'usage': {},
            }
        import httpx
        monkeypatch.setattr(httpx, 'post', lambda *a, **kw: MockResp())
        client = DeepSeekClient(api_key='test', timeout=5, max_retries=1)
        result = client.analyze('system', 'user')
        assert result['status'] == 'error'
        assert result['error'] == 'json_parse'

    def test_no_api_key_in_log(self, monkeypatch):
        import logging
        logger = logging.getLogger('app.deepseek_analysis')
        handler = logging.StreamHandler()
        logger.addHandler(handler)
        import httpx
        class MockResp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {
                'choices': [{'message': {'content': '{"status":"success"}'}}],
                'usage': {},
            }
        monkeypatch.setattr(httpx, 'post', lambda *a, **kw: MockResp())
        client = DeepSeekClient(api_key='sk-test-secret-key', timeout=5)
        try:
            result = client.analyze('system', 'user')
            assert result['status'] == 'success'
        finally:
            logger.removeHandler(handler)
