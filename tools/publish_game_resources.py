"""Publish resource packs for byte-range downloads from this repository's Releases."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.parse
import urllib.request


def run(*args):
    return subprocess.check_output(args, text=True)


def download(url, destination, size):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=120) as source, destination.open('wb') as target:
                while data := source.read(1024 * 1024):
                    target.write(data)
            if destination.stat().st_size != size:
                raise ValueError('Resource size mismatch: ' + destination.name)
            return
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def main():
    source = json.loads(Path('game-resource-source.json').read_text())
    repo = os.environ['GITHUB_REPOSITORY']
    release = source['release']
    prefix = f'https://github.com/{repo}/releases/download/{release}'
    try:
        run('gh', 'release', 'view', release)
    except subprocess.CalledProcessError:
        run('gh', 'release', 'create', release, '--title', '游戏资源 ' + release,
            '--notes', '客户端热更分流资源。按需读取资源卷，支持断点续传。', '--latest=false')
    staging = Path('resource-staging')
    staging.mkdir(exist_ok=True)
    index = {'schema': 1, 'release': release, 'packs': [], 'files': {}}
    groups = [[]]
    used = 0
    for name, size in sorted(source['files'].items()):
        if used + size > 1500000000 and groups[-1]:
            groups.append([])
            used = 0
        groups[-1].append((name, size))
        used += size
    for number, group in enumerate(groups):
        pack = staging / f'resources-{number:02d}.bin'
        def fetch(item):
            name, size = item
            target = staging / hashlib.sha256(name.encode()).hexdigest()
            if not target.exists() or target.stat().st_size != size:
                url = source['origin'] + '/updates/' + release + '/' + urllib.parse.quote(name)
                download(url, target, size)
            return name, target
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            paths = dict(pool.map(fetch, group))
        digest = hashlib.sha256()
        with pack.open('wb') as output:
            for name, size in group:
                offset = output.tell()
                with paths[name].open('rb') as resource:
                    while data := resource.read(1024 * 1024):
                        output.write(data)
                        digest.update(data)
                index['files'][name] = [number, offset, size]
                paths[name].unlink()
        index['packs'].append({'url': prefix + '/' + pack.name,
                               'size': pack.stat().st_size, 'sha256': digest.hexdigest()})
        run('gh', 'release', 'upload', release, str(pack), '--clobber')
        pack.unlink()
        print('Published pack', number + 1, '/', len(groups), flush=True)
    index_path = Path('game-resources') / release / 'packs.json'
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, separators=(',', ':')) + '\n')
    version = source['version']
    version['UpdatePrefixUri'] = prefix + '/updates/' + release
    version['GitHubPackIndexUri'] = f'https://raw.githubusercontent.com/{repo}/main/{index_path.as_posix()}'
    Path('version.txt').write_text(json.dumps(version, separators=(',', ':')) + '\n')
    run('git', 'config', 'user.name', 'github-actions[bot]')
    run('git', 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    run('git', 'add', 'version.txt', 'game-resources')
    run('git', 'commit', '-m', 'Publish verified game resource manifest ' + release)
    run('git', 'push', 'origin', 'HEAD:main')


if __name__ == '__main__':
    main()
