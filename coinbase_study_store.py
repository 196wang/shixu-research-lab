"""Read only the frozen Coinbase retrospective artifacts after hash checks."""
from pathlib import Path
import hashlib
import json

HERE = Path(__file__).resolve().parent
RUN = HERE / 'coinbase_study_v21' / 'run-001'
FILES = {'report': 'report.json', 'details': 'details.json', 'protocol': 'protocol.json',
         'registration': 'registration.json', 'manifest': 'manifest.json'}


class CoinbaseStudyIntegrityError(RuntimeError):
    status_code = 503


def read(kind, base=RUN):
    if kind not in FILES:
        raise CoinbaseStudyIntegrityError('未知的 Coinbase 回测文件')
    folder = Path(base)
    try:
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        name = FILES[kind]
        body = (folder / name).read_bytes()
        if kind != 'manifest' and hashlib.sha256(body).hexdigest() != manifest['fileSha256'][name]:
            raise CoinbaseStudyIntegrityError('Coinbase 固定文件哈希不符，拒绝展示')
        value = json.loads(body)
        if value.get('studyId') != 'CB-USD-HX-001' and kind not in ('details',):
            raise CoinbaseStudyIntegrityError('Coinbase 固定研究编号不符')
        return value
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise CoinbaseStudyIntegrityError('Coinbase 固定报告缺失或结构损坏') from exc
