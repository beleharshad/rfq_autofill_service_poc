"""Reproducible reference evaluation. Run from backend in the geometry environment.

Example: python -m scripts.validate_automatic_corpus --input /drawings --report /tmp/report.json
Optional --expected is a human-verified JSON mapping filename -> measurement key ->
{lower: millimetres, upper: millimetres}. Never generate expected values from model outputs.
Exit 0 requires every source to complete; exit 2 means blocked/review/mismatch.
"""
import argparse
import hashlib
import json
import tempfile
import uuid
from collections import Counter
from pathlib import Path
import fitz
from app.storage.file_storage import FileStorage
from app.storage.automatic_runs import AutomaticRuns
from app.services.automatic_runner import run_automatic


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--expected',type=Path)
    args=parser.parse_args()
    expectations=json.loads(args.expected.read_text()) if args.expected else {}
    with tempfile.TemporaryDirectory(prefix='rfq-corpus-') as temp:
        files=FileStorage(Path(temp));job=str(uuid.uuid4());preflight=[]
        for source in sorted(args.input.rglob('*')):
            if not source.is_file() or source.name.startswith('~$'):continue
            if source.suffix.lower() not in ('.pdf','.step','.stp'):continue
            data=source.read_bytes();key=source.relative_to(args.input).as_posix()
            entry={'file':key,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
            if source.suffix.lower()=='.pdf':
                with fitz.open(source) as pdf:
                    entry.update(pages=len(pdf),text_characters=sum(len(p.get_text().strip()) for p in pdf))
            preflight.append(entry)
            files.save_bytes_file(job,source.name,data)
        path=files.get_job_path(job);runs=AutomaticRuns(path);run=runs.enqueue()
        run_automatic(path,run['run_id']);result=runs.get()
        comparisons=[]
        for item in result['result']['items']:
            expected=expectations.get(item['name'])
            if not expected:continue
            failures=[]
            for key,bounds in expected.items():
                actual=(item.get('measurements') or {}).get(key)
                if actual is None or not bounds['lower']<=actual<=bounds['upper']:failures.append(key)
            comparisons.append({'file':item['name'],'passed':not failures,'failed_metrics':failures})
        report={'source_count':len(preflight),'preflight':preflight,'result_counts':dict(Counter(i['status'] for i in result['result']['items'])),
                'run':result,'reference_comparisons':comparisons,
                'accuracy_validation':'not_performed' if not expectations else 'human_expected_values_supplied'}
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,indent=2))
        print(json.dumps({'source_count':report['source_count'],'result_counts':report['result_counts'],
                          'accuracy_validation':report['accuracy_validation'],'report':str(args.report)}))
        return 0 if result['status']=='complete' and all(c['passed'] for c in comparisons) else 2


if __name__=='__main__':raise SystemExit(main())
