"""Reproduce the released 100-problem dataset without API calls.

The original generator and solver live in the dataset_generation package.
Default execution generates all 100 problems afresh, validates every solution,
and publishes only missing files. Existing different dataset files are never
overwritten. --verify-only checks the existing dataset without regeneration.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from contextlib import redirect_stdout
import hashlib
import io
import json
import multiprocessing
from pathlib import Path
import shutil
import tempfile

from dataset_generation import core
from dataset_generation.geometry import geometry_key
from dataset_validation import validateDataset

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / 'dataset'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(folder):
    dataset = read_json(folder / 'private/dataset.json')
    witnesses = read_json(folder / 'private/solution_witnesses.json')
    proof = read_json(folder / 'exclusivity-proof.json')
    report = validateDataset(dataset, witnesses, proof)
    manifest = read_json(folder / 'manifest.json')
    if manifest.get('prompt_file') != 'prompt_en.txt' or manifest.get('prompt_language') != 'en':
        raise ValueError('The dataset must use the current English prompt.')
    for key, relative in [
        ('dataset_sha256', 'private/dataset.json'),
        ('witness_sha256', 'private/solution_witnesses.json'),
        ('proof_sha256', 'exclusivity-proof.json'),
        ('prompt_sha256', 'prompt_en.txt'),
    ]:
        if sha256(folder / relative) != manifest[key]:
            raise ValueError('Manifest mismatch: ' + relative)
    public_count = 0
    for problem in dataset['problems']:
        for round_ in problem['rounds']:
            expected = {key: round_['board'][key] for key in ('grid_size', 'source', 'filters', 'sensors')}
            path = folder / 'public_boards' / f"problem_{problem['problem_number']:03d}" / f"round_{round_['round_index']}.json"
            if read_json(path) != expected:
                raise ValueError('Public board mismatch: ' + str(path))
            public_count += 1
    return {**report, 'public_boards_verified': public_count, 'dataset_sha256': manifest['dataset_sha256']}


def publish_identical_or_missing(generated):
    """Preflight every file before writing any missing files."""
    missing = []
    identical = 0
    for source in sorted(generated.rglob('*')):
        if not source.is_file():
            continue
        relative = source.relative_to(generated)
        target = DATASET / relative
        if target.exists():
            same = target.read_bytes() == source.read_bytes()
            # Manifest field ordering is not part of the dataset identity.
            if not same and relative.as_posix() == 'manifest.json':
                same = read_json(target) == read_json(source)
            if not same:
                raise ValueError(f'Existing file differs; nothing was published: {relative}')
            identical += 1
        else:
            missing.append((source, target))
    for source, target in missing:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return {'existing_files_preserved': identical, 'missing_files_created': len(missing)}


def main():
    parser = argparse.ArgumentParser(description='Generate or verify the current seeded 100-problem dataset (no API calls).')
    parser.add_argument('--count', type=int, choices=[100], default=100, help='This release contains exactly 100 problems.')
    parser.add_argument('--workers', type=int, default=4, help='Parallel generation workers (default: 4).')
    parser.add_argument('--verify-only', action='store_true', help='Verify the existing 100 problems without generating them.')
    args = parser.parse_args()
    if not 1 <= args.workers <= 32:
        parser.error('--workers must be between 1 and 32')
    if args.verify_only:
        report = verify(DATASET)
        print(json.dumps({'mode': 'verify-only', 'paid_api_calls': 0, **report}, ensure_ascii=False, indent=2))
        return
    prompt = DATASET / 'prompt_en.txt'
    if not prompt.is_file():
        raise FileNotFoundError('Keep dataset/prompt_en.txt with the generator.')
    print(f'Generating {args.count} problems / 500 rounds; seed={core.SEED}; workers={args.workers}; no cache, no API.', flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        items = []
        for item in pool.map(core.candidate, range(1, args.count + 1)):
            items.append(item)
            if len(items) % 10 == 0:
                print(f'Generated {len(items)}/{args.count}', flush=True)
    seen = set()
    for index, item in enumerate(items):
        retry = 0
        number = item['problem']['problem_number']
        while any(geometry_key(round_['board']) in seen for round_ in item['problem']['rounds']):
            retry += 1
            item = core.candidate(number, retry)
        seen.update(geometry_key(round_['board']) for round_ in item['problem']['rounds'])
        items[index] = item
    # A dedicated temporary folder keeps generation separate from the experiment files.
    with tempfile.TemporaryDirectory(prefix='mirror-generate-') as work:
        work = Path(work).resolve()
        if work.parent != Path(tempfile.gettempdir()).resolve() or not work.name.startswith('mirror-generate-'):
            raise ValueError('Unexpected temporary generation directory')
        (work / 'dataset').mkdir()
        shutil.copyfile(prompt, work / 'dataset/prompt_en.txt')
        previous_root = core.ROOT
        try:
            core.ROOT = work
            with redirect_stdout(io.StringIO()):
                core.package(items)
        finally:
            core.ROOT = previous_root
        report = verify(work / 'dataset')
        publication = publish_identical_or_missing(work / 'dataset')
        summary = {
            'passed': True, 'seed': core.SEED, 'problems': 100, 'rounds': 500,
            'solution_witnesses_verified': report['solution_witnesses_verified'],
            'public_boards_verified': report['public_boards_verified'],
            'unique_round_geometries': report['unique_round_geometries'],
            'candidate_attempts': sum(item['generation']['candidates'] for item in items),
            'dataset_sha256': report['dataset_sha256'], 'output_folder': str(DATASET),
            'cache_used': False, 'paid_api_calls': 0, **publication,
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
