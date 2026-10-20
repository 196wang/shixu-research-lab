"""Read verified, frozen exploration artifacts; never execute a backtest on GET."""
from pathlib import Path
import hashlib
import json
import re

HERE = Path(__file__).resolve().parent
FILES = {'report': 'report.json', 'details': 'details.json', 'snapshot': 'snapshot.json',
         'protocol': 'protocol.json', 'registration': 'registration.json'}

class ExplorationIntegrityError(RuntimeError):
    pass

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def within(base, relative):
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ExplorationIntegrityError('回测文件路径不在资料包内')
    return path

def read_artifact(kind='report', base=None):
    base = Path(base or HERE)
    if kind not in FILES:
        raise ValueError('未知回测文件')
    folder = base / 'exploration_v16'
    index = folder / 'index.json'
    if not index.exists():
        return {'status': 'not_run', 'version': '0.16.0', 'marketValidated': False,
                'message': '第一轮固定历史回测尚未生成。'}
    try:
        manifest = json.loads(index.read_text(encoding='utf-8'))
        run = manifest['run']
        if not re.fullmatch(r'run-[0-9]{3}', run):
            raise ExplorationIntegrityError('回测批次标识无效')
        target = folder / run
        if set(manifest['artifacts']) != set(FILES.values()):
            raise ExplorationIntegrityError('回测文件清单不完整')
        for name, expected in manifest['artifacts'].items():
            if digest(within(target, name)) != expected:
                raise ExplorationIntegrityError('回测文件校验不一致：' + name)
        registration = json.loads((target/'registration.json').read_text(encoding='utf-8'))
        for relative, expected in registration['inputHashes'].items():
            if digest(within(base, relative)) != expected:
                raise ExplorationIntegrityError('计算依据已变化：' + relative)
        if registration['snapshotFileSha256'] != manifest['artifacts']['snapshot.json']:
            raise ExplorationIntegrityError('行情快照与登记不一致')
        if registration['protocolFileSha256'] != manifest['artifacts']['protocol.json']:
            raise ExplorationIntegrityError('协议与登记不一致')
        return json.loads((target/FILES[kind]).read_text(encoding='utf-8'))
    except ExplorationIntegrityError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ExplorationIntegrityError('回测资料缺失或格式错误，暂停显示结果') from exc
