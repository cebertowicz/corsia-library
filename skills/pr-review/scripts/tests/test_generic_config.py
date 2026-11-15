from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))
import review_gate_config as config
import review_gate_evaluate as evaluate
import review_gate_publish as publish


def test_all_configured_contexts_are_excluded_but_similarly_named_checks_are_not():
    cfg = config.load()
    cfg['status_contexts'] = {'live': {'verdict': 'review-custom', 'presence': 'presence-custom'},
                              'shadow': {'verdict': 'shadow-custom', 'presence': None}}
    contexts = config.all_contexts(cfg)
    runs = [{'name': n, 'conclusion': 'failure'} for n in contexts]
    runs.append({'name': 'ci', 'conclusion': 'success'})
    assert evaluate.combined_check_state(runs, contexts) == 'all_green'
    assert evaluate.combined_check_state(runs + [{'name': 'review-custom-extra', 'conclusion': 'failure'}], contexts) == 'failing'


def test_publish_clients_exclude_all_configured_contexts(tmp_path):
    class Github:
        def json(self, path):
            return {'check_runs': [{'name': 'shadow-custom', 'conclusion': 'failure'},
                                   {'name': 'ci', 'conclusion': 'failure'}]}
    contexts = {'review-custom', 'presence-custom', 'shadow-custom'}
    shadow = publish.ShadowClient(Github(), tmp_path, 'shadow-custom', contexts)
    live = publish.LiveClient(login='bot', contexts={'review-custom'}, review_contexts=contexts)
    live.github = Github()
    assert shadow.failed_checks('org/repo', 'abc') == ['ci']
    assert live.failed_checks('org/repo', 'abc') == ['ci']


def test_rule_link_uses_configured_standards_path():
    settings = publish.Settings(model='model', standards=('policies/team/review.md',), rules={'R1': {'file': 'review.md', 'text': 'Check it.'}})
    finding = {'severity': 'must_fix', 'category': 'standards/rule', 'text': 'Broken', 'fingerprint': 'abc',
               'repo': 'example-org/example-repo', 'head_sha': 'head', 'base_sha': 'base', 'rule_id': 'R1'}
    body = publish.render_finding(finding, settings)
    assert '/blob/base/policies/team/review.md#r1' in body


def test_optional_extended_base_selects_exactly_one_worker_rule():
    cfg = config.load()
    cfg['worker_workflow'] = 'custom-reviewer'
    cfg['extended_window_base'] = None
    rules = config.configured_rules(cfg)
    starts = [r for r in rules if r['action'] == 'start_worker']
    assert len(starts) == 1 and starts[0]['workflow'] == 'custom-reviewer'
    assert all(m['field'] != 'base_ref' for m in starts[0]['match'])
    assert all(m['value'] in {'custom-reviewer', 'custom-reviewer@'} for r in rules for m in r['match']
               if m.get('field') == 'workflow' and m.get('value', '').startswith('custom-reviewer'))
    cfg['extended_window_base'] = 'release'
    starts = [r for r in config.configured_rules(cfg) if r['action'] == 'start_worker']
    assert len(starts) == 2
    assert {(m['op'], m['value']) for r in starts for m in r['match'] if m['field'] == 'base_ref'} == {('eq', 'release'), ('not_eq', 'release')}
