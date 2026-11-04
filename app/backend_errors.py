"""Safe, typed backend failures; never hide definitive HTTP rejections."""
import logging
import re
import requests

logger = logging.getLogger(__name__)


def safe_detail(value):
    text = str(value)
    text = re.sub(r'https?://\S+', '[URL omesso]', text)
    text = re.sub(r'(?i)(bearer\s+)\S+', r'\1[omesso]', text)
    text = re.sub(r'(?i)(project_context_token|authorization|access_token|secret|internal.token)[\s\"\x27:=]+[^\s,}\"]+', r'\1=[omesso]', text)
    return text[:800]


class BackendAgentError(RuntimeError):
    def __init__(self, code, detail, uncertain=False, http_status=None):
        self.code, self.detail = code, safe_detail(detail)
        self.uncertain, self.http_status = uncertain, http_status
        super().__init__(self.detail)

    def result(self):
        return {'error':self.detail, 'code':self.code, 'http_status':self.http_status,
                'uncertain':self.uncertain, 'retryable':False}


def post_backend(url, payload, headers, endpoint):
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=20)
    except requests.RequestException as error:
        logger.warning('backend_agent_transport endpoint=%s error_type=%s', endpoint, type(error).__name__)
        raise BackendAgentError('BACKEND_OUTCOME_UNKNOWN',
            'Conferma dell’avvio non ricevuta. Verificare il job prima di riprovare.', True) from error
    if not response.ok:
        try:
            body = response.json()
            detail = body.get('detail') or body.get('error') or 'Richiesta rifiutata dal backend'
        except (ValueError, AttributeError):
            detail = 'Risposta di errore non JSON'
        uncertain = response.status_code >= 500 or response.status_code in (408, 504)
        logger.warning('backend_agent_rejected endpoint=%s http_status=%s detail=%s',
                       endpoint, response.status_code, safe_detail(detail))
        raise BackendAgentError('BACKEND_HTTP_ERROR',
            f'Backend HTTP {response.status_code}: {safe_detail(detail)}', uncertain, response.status_code)
    try:
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError('Expected object')
        return result
    except ValueError as error:
        raise BackendAgentError('BACKEND_INVALID_RECEIPT',
            'Il backend ha risposto senza una conferma valida. Verificare il job prima di riprovare.', True) from error
