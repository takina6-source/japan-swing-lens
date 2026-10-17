"""Lossless transport for bulk exports; dashboard fetch contracts stay unchanged."""
import gzip
import json
from pathlib import Path

from .state_io import StateError, compress, sha256

# Only bulk downloads that are not fetched by the dashboard/adapters.
NAMES = {
    'validation': ('signals', 'signal_history', 'performance', 'controls', 'control_performance'),
    'experimental': ('signals', 'history', 'performance', 'controls', 'control_performance'),
}
RAW_CONTRACTS = {'validation/signal_history.json', 'experimental/history.json'}


def stage_downloads(root: Path):
    files = {}
    for kind, names in NAMES.items():
        for stem in names:
            for extension in ('json', 'csv'):
                name = f'{kind}/{stem}.{extension}'
                path = root/name
                packed_path = root/(name+'.gz')
                if name in RAW_CONTRACTS:
                    if packed_path.exists() and not path.exists():
                        raise StateError('raw Committee history contract missing; stage original exports')
                    continue
                if path.exists():
                    raw = path.read_bytes()
                    packed = compress(raw)
                elif packed_path.exists():
                    packed = packed_path.read_bytes()
                    raw = gzip.decompress(packed)
                    if packed != compress(raw):
                        raise StateError('bulk export is not deterministic gzip')
                else:
                    continue
                if gzip.decompress(packed) != raw:
                    raise StateError('bulk export failed byte verification')
                packed_path.write_bytes(packed)
                files[name] = {'path': name+'.gz', 'bytes': len(raw), 'sha256': sha256(raw),
                               'compressed_bytes': len(packed), 'compressed_sha256': sha256(packed)}
                path.unlink(missing_ok=True)
        index_path = root/kind/'index.json'
        if index_path.exists():
            index = json.loads(index_path.read_bytes())
            index['available_files'] = [p+'.gz' if f'{kind}/{p}' in files else p
                                        for p in index.get('available_files', [])]
            index['bulk_downloads_manifest'] = '../bulk-downloads.json'
            index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2, allow_nan=False))
    html_path = root/'index.html'
    if html_path.exists():
        html = html_path.read_text()
        for name, info in files.items():
            old = f'href="./{name}"'
            new = (f'href="./{info["path"]}" data-original-name="{Path(name).name}" '
                   f'data-original-sha256="{info["sha256"]}" data-original-bytes="{info["bytes"]}" '
                   f'data-packed-sha256="{info["compressed_sha256"]}"')
            html = html.replace(old, new)
        # Frozen historical artifacts do not yet contain the new download handler.
        if './downloads.js' not in html:
            html = html.replace('</body>', '<script src="./downloads.js" defer></script></body>')
        html_path.write_text(html)
    handler = Path(__file__).parents[1]/'public/dashboard/downloads.js'
    (root/'downloads.js').write_bytes(handler.read_bytes())
    manifest = {'format': 'jsl-bulk-downloads-v1', 'files': files}
    (root/'bulk-downloads.json').write_text(json.dumps(manifest, sort_keys=True, separators=(',', ':')))
    return sha256((root/'bulk-downloads.json').read_bytes())


def restore_downloads(root: Path):
    manifest_path = root/'bulk-downloads.json'
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_bytes())
    for name, info in manifest['files'].items():
        if name not in {f'{k}/{s}.{e}' for k, stems in NAMES.items() for s in stems for e in ('json','csv')}:
            raise StateError('unexpected bulk export path')
        packed = (root/(name+'.gz')).read_bytes()
        raw = gzip.decompress(packed)
        if sha256(packed) != info['compressed_sha256'] or sha256(raw) != info['sha256'] or len(raw) != info['bytes']:
            raise StateError('bulk export checksum mismatch during recovery')
        (root/name).write_bytes(raw)
        (root/(name+'.gz')).unlink()
    # Keep the functioning download handler and re-stageable manifest; rollback
    # rewrites download links to the restored originals.
    html_path=root/'index.html'
    if html_path.exists():
        html=html_path.read_text()
        import re
        for name in manifest['files']:
            html=re.sub(r'href="\./'+re.escape(name)+r'\.gz" data-original-name="[^"]*" data-original-sha256="[^"]*" data-original-bytes="[^"]*" data-packed-sha256="[^"]*"',f'href="./{name}"',html)
        html_path.write_text(html)
    for kind in NAMES:
        path=root/kind/'index.json'
        if path.exists():
            index=json.loads(path.read_bytes())
            index['available_files']=[p[:-3] if p.endswith('.gz') and f'{kind}/{p[:-3]}' in manifest['files'] else p for p in index.get('available_files',[])]
            index.pop('bulk_downloads_manifest',None)
            path.write_text(json.dumps(index,ensure_ascii=False,indent=2,allow_nan=False))
    manifest_path.unlink()
