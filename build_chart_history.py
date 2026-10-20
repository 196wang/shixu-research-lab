"""Rebuild chart_history.json from frozen local inputs; no network or scoring."""
import argparse
import json
from pathlib import Path
from historical_chart import HERE, ChartIntegrityError, build_history


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-dir', type=Path, default=HERE)
    args = parser.parse_args()
    try:
        artifact = build_history(args.base_dir)
        destination = args.base_dir / 'chart_history.json'
        temporary = destination.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(artifact, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n', encoding='utf-8')
        temporary.replace(destination)
        print(json.dumps({'file': str(destination), 'coverage': artifact['coverage'],
                          'transitions': len(artifact['transitions']), 'contentSha256': artifact['contentSha256']}, ensure_ascii=False))
    except (ChartIntegrityError, ValueError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
