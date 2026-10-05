"""Upload snapshot -> source interpretation -> checked recipe -> real CAD artifacts.

Runs in the isolated geometry environment. Every document gets an explicit result.
All source associations are resolved before building so siblings share a stable snapshot.
"""
import hashlib
from pathlib import Path
from app.models.accepted_part import AcceptedPartRequest
from app.models.document_registry import AssociationRequest
from app.services.drawing_interpreter import interpret_drawing, normalize_proposal, ProviderUnavailable
from app.services.generic_geometry import build_geometry
from app.storage.accepted_parts import AcceptedParts
from app.storage.automatic_runs import AutomaticRuns


def execute(job_path, run_id, interpreter=None):
    runs = AutomaticRuns(job_path)
    run = runs.get(run_id)
    if run['status'] != 'running': return
    registry = runs.registry
    version = run['registry_version']
    result = {'phase': 'interpreting', 'items': [], 'validation': 'automated; not engineering certification'}
    prepared = []
    store = AcceptedParts(job_path)
    try:
        runs.ensure_current(run_id, version)
        documents = registry.read()['documents']
        if len(documents) > 64: raise ValueError('Split batches larger than 64 documents')
        if not documents: raise ValueError('Upload at least one supported source')
        for doc in documents:
            runs.ensure_current(run_id, version)
            item = {'document_id': doc['document_id'], 'name': doc['original_name'], 'status': 'interpreting', 'issues': []}
            result['items'].append(item)
            runs.update(run_id, result=result)
            source = registry.root / 'blobs' / doc['sha256']
            try:
                if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != doc['sha256']:
                    raise ValueError('Retained source integrity check failed')
                suffix = Path(doc['path']).suffix.lower()
                if suffix == '.pdf':
                    proposal, audit, pages = (interpreter or interpret_drawing)(source)
                    item['proposal'] = proposal.model_dump()
                    item['audit'] = audit.model_dump() if audit else None
                    normalized, issues = normalize_proposal(proposal, audit, pages)
                    if issues:
                        item.update(status='review_required', issues=issues)
                        continue
                    item['part_number'], item['revision'] = proposal.part_number, proposal.revision
                    payload = dict(part_number=proposal.part_number, revision=proposal.revision,
                        governing_document_id=doc['document_id'], base=normalized['base'], features=normalized['features'],
                        evidence=[{'document_id': doc['document_id'], 'page': e.page, 'locator': (e.target+': '+e.callout)[:500]} for e in proposal.evidence],
                        review_note='Automatically interpreted and checked in two vision passes; no human review.',
                        completeness='complete', unresolved=[], material=proposal.material,
                        acceptance_origin='automatic_drawing', dimension_checks=[d.model_dump() for d in audit.dimensions])
                    role = 'finished_drawing'
                elif suffix in ('.step', '.stp'):
                    import cadquery as cq
                    solids = cq.importers.importStep(str(source)).solids().vals()
                    if len(solids) != 1:
                        raise ValueError(f'STEP contains {len(solids)} solids; select the intended body in review')
                    # Source identity is explicitly content-addressed, not inferred drawing identity.
                    association = doc['association']
                    part = association['part_number'] if association else 'CAD-'+doc['sha256'][:16]
                    revision = association['revision'] if association else 'source-'+doc['sha256'][:12]
                    if not revision: raise ValueError('CAD source revision is unresolved')
                    item['part_number'], item['revision'] = part, revision
                    payload = dict(part_number=part, revision=revision, governing_document_id=doc['document_id'],
                        base={'kind':'step','document_id':doc['document_id'],'body_index':0}, features=[],
                        evidence=[{'document_id':doc['document_id'],'page':None,'locator':'Exact single solid imported from retained STEP bytes'}],
                        review_note='Automatic source CAD import. Source identity is not a verified drawing part number; manufacturing state is not independently verified.',
                        completeness='complete', unresolved=[], acceptance_origin='source_cad', dimension_checks=[])
                    role = 'cad'
                else:
                    item.update(status='unsupported', issues=['Automatic geometry supports PDF and STEP/STP; this document is retained for review'])
                    continue
                # Validate normalized values before mutating any source association.
                AcceptedPartRequest(**payload, expected_version=0, expected_registry_version=version)
                state = 'finished' if role == 'finished_drawing' else (doc['association']['manufacturing_state'] if doc['association'] else 'unresolved')
                if role == 'cad' and state not in ('finished', 'unresolved'):
                    raise ValueError('CAD is associated with raw/intermediate manufacturing state; review before importing')
                wanted = dict(part_number=payload['part_number'], revision=payload['revision'], role=role, manufacturing_state=state)
                if doc['association'] and doc['association'] != wanted:
                    raise ValueError('Extracted identity/state conflicts with the saved association; manual association is preserved')
                if any(s['payload']['part_number']==payload['part_number'] and s['payload']['revision']==payload['revision'] for s in store.list()):
                    raise ValueError('A specification already exists for this part/revision; use its review/build controls to preserve corrections')
                prepared.append((doc, item, payload, wanted))
                item['status'] = 'validated_proposal'
            except ProviderUnavailable as exc:
                item.update(status='blocked', issues=[str(exc)])
            except Exception as exc:
                item.update(status='review_required', issues=[f'{type(exc).__name__}: {str(exc)[:1500]}'])
            finally:
                runs.update(run_id, result=result)
        # Never pick a winner from competing sources for the same part revision.
        groups = {}
        for row in prepared:
            groups.setdefault((row[2]['part_number'], row[2]['revision']), []).append(row)
        unique = []
        for group in groups.values():
            if len(group)>1:
                for _, item, _, _ in group:
                    item.update(status='review_required', issues=['Multiple governing sources claim this part/revision; select the authority in review'])
            else: unique.extend(group)
        result['phase'] = 'associating'
        for doc, item, payload, wanted in unique:
            runs.ensure_current(run_id, version)
            if not doc['association']:
                version = registry.associate(doc['document_id'], AssociationRequest(**wanted, expected_version=version))['version']
                runs.update(run_id, result=result, registry_version=version)
        result['phase'] = 'building'
        for doc, item, payload, _ in unique:
            runs.ensure_current(run_id, version)
            task = None
            try:
                spec = store.save(AcceptedPartRequest(**payload, expected_version=0, expected_registry_version=version))
                task = store.enqueue(spec['spec_id'])
                item.update(spec_id=spec['spec_id'], build_id=task['build_id'], status='building')
                runs.update(run_id, result=result)
                if not store.claim(task['build_id']): raise RuntimeError('Geometry task was claimed by another worker')
                manifest = build_geometry(spec, registry, job_path/'outputs'/'accepted'/task['build_id'])
                runs.ensure_current(run_id, version)
                store.get(spec['spec_id'])
                store.finish(task['build_id'], manifest=manifest)
                if store.build(task['build_id'])['status'] != 'complete': raise RuntimeError('Geometry build was cancelled')
                item.update(status='complete', measurements=manifest['measurements'], warnings=manifest['warnings'])
            except Exception as exc:
                if task: store.finish(task['build_id'], error=str(exc))
                item.update(status='review_required', issues=[f'Geometry validation failed: {str(exc)[:1500]}'])
            runs.update(run_id, result=result)
        runs.ensure_current(run_id, version)
        result['phase'] = 'finished'
        status = 'complete' if all(i['status']=='complete' for i in result['items']) else 'review_required'
        runs.update(run_id, result=result, status=status, registry_version=version)
    except Exception as exc:
        runs.update(run_id, result=result, status='failed', error=str(exc)[:1500])
