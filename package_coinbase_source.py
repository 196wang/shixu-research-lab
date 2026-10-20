"""Update the public source archive with the Coinbase study, excluding local DBs."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

HERE = Path(__file__).resolve().parent
ARCHIVE = HERE / 'source_bundle.zip'
ADDITIONS = [
    'README.md', 'app_server.py', 'coinbase_feed.py', 'exploration_generators.py',
    'build_coinbase_backfill.py', 'build_coinbase_study.py', 'coinbase_study.py',
    'coinbase_study_protocol.json', 'coinbase_study_store.py', 'test_coinbase_study.py',
    'coinbase_forward.py', 'coinbase_forward_protocol.json', 'test_coinbase_forward.py',
    'coinbase_calendar_backtest.py', 'test_coinbase_calendar_backtest.py',
    'coinbase_rule_compare.py', 'test_coinbase_rule_compare.py',
    'coinbase_intraday.py', 'test_coinbase_intraday.py',
    'checks/gengxu_exploration.py',
    'coinbase_forward_registration_note.md', 'package_coinbase_source.py',
    'web/app.js', 'web/index.html', 'web/tradingview-chart.js', 'web/coinbase-study.js',
    'web/coinbase-forward.js', 'web/coinbase-calendar-backtest.js', 'web/coinbase-rule-compare.js',
    'web/coinbase-intraday.js',
    'web/style.css', 'web/coinbase-feed.js',
]
ADDITIONS += [p.relative_to(HERE).as_posix() for p in (HERE / 'coinbase_study_v21' / 'run-001').glob('*.json')]


def package():
    target = HERE / 'source_bundle.next.zip'
    additions = {Path(name).as_posix() for name in ADDITIONS}
    with ZipFile(ARCHIVE, 'r') as old, ZipFile(target, 'w', compression=ZIP_DEFLATED, compresslevel=6) as new:
        for item in old.infolist():
            if item.filename not in additions:
                new.writestr(item, old.read(item.filename))
        for name in sorted(additions):
            new.write(HERE / name, name)
    target.replace(ARCHIVE)
    return len(additions)


if __name__ == '__main__':
    print(f'Packed {package()} Coinbase/source additions')
