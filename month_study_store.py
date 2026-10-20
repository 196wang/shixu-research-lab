"""Verify immutable month-study artifacts; reads never calculate or refresh data."""
from pathlib import Path
import hashlib
import json
import re

HERE = Path(__file__).resolve().parent
FILES = {'report': 'report.json', 'details': 'details.json',
         'protocol': 'protocol.json', 'registration': 'registration.json'}
SNAPSHOT_PATH = 'exploration_v16/run-001/snapshot.json'


class MonthStudyIntegrityError(RuntimeError):
    pass


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def within(base, relative):
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()):
        raise MonthStudyIntegrityError('月份研究文件路径超出资料包')
    return path


def read_month_artifact(kind='report', base=None):
    base = Path(base or HERE)
    if kind not in FILES:
        raise ValueError('未知月份研究文件')
    folder = base / 'month_study_v17'
    if not (folder / 'index.json').exists():
        return {'status': 'not_run', 'version': '0.17.0', 'marketValidated': False,
                'message': '月份与寅日的固定研究尚未生成。'}
    try:
        manifest = json.loads((folder/'index.json').read_text(encoding='utf-8'))
        run = manifest['run']
        if not isinstance(run, str) or not re.fullmatch(r'run-[0-9]{3}', run):
            raise MonthStudyIntegrityError('月份研究批次标识无效')
        target = folder / run
        if set(manifest['artifacts']) != set(FILES.values()):
            raise MonthStudyIntegrityError('月份研究清单不完整')
        for name, expected in manifest['artifacts'].items():
            if digest(within(target, name)) != expected:
                raise MonthStudyIntegrityError('月份研究文件校验失败：' + name)
        registration = json.loads((target/'registration.json').read_text(encoding='utf-8'))
        for relative, expected in registration['inputHashes'].items():
            if digest(within(base, relative)) != expected:
                raise MonthStudyIntegrityError('月份研究计算依据已变化：' + relative)
        if registration['snapshotPath'] != SNAPSHOT_PATH:
            raise MonthStudyIntegrityError('月份研究行情快照引用错误')
        if digest(within(base, SNAPSHOT_PATH)) != registration['snapshotFileSha256']:
            raise MonthStudyIntegrityError('月份研究行情快照已变化')
        if registration['protocolFileSha256'] != manifest['artifacts']['protocol.json']:
            raise MonthStudyIntegrityError('月份研究协议与登记不一致')
        return json.loads((target/FILES[kind]).read_text(encoding='utf-8'))
    except MonthStudyIntegrityError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MonthStudyIntegrityError('月份研究资料缺失或格式错误，暂停显示') from exc
