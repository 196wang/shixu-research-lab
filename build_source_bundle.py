"""Build the website's downloadable source archive from public Git files."""
from pathlib import Path
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT / 'source_bundle.zip'


def main():
    names = subprocess.check_output(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT
    ).decode('utf-8').split('\0')
    with zipfile.ZipFile(ARCHIVE, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for name in sorted(set(names)):
            if not name or name == ARCHIVE.name:
                continue
            path = ROOT / name
            if path.is_file():
                output.write(path, name)
    print(f'{ARCHIVE.name}: {len(zipfile.ZipFile(ARCHIVE).namelist())} public files')


if __name__ == '__main__':
    main()
