import secrets
import pytest
from modules.local_agent.repository import LocalAgentRepository


def test_enrollment(tmp_path):
    repo = LocalAgentRepository(tmp_path / 'agent.sqlite3')
    token = secrets.token_urlsafe(48)
    ref = repo.request_enrollment(usuario='jovino', machine_name='PC', token=token, source='test')
    tasks = frozenset({'system.ping'})
    assert repo.collect_enrollment(ref, token=token, tasks=tasks)['state'] == 'PENDING'
    with pytest.raises(LookupError):
        repo.collect_enrollment(ref, token=secrets.token_urlsafe(48), tasks=tasks)
    with pytest.raises(RuntimeError):
        repo.request_enrollment(usuario='jovino', machine_name='PC2', token=secrets.token_urlsafe(48), source='test')
    repo.decide_enrollment(ref, approve=True, actor='admin')
    result = repo.collect_enrollment(ref, token=token, tasks=tasks)
    assert repo.collect_enrollment(ref, token=token, tasks=tasks) == result
    assert repo.authenticate_dynamic_agent(agent_id=result['agent_id'], token=token) == tasks
    assert repo.revoke_agent(result['agent_id'])
    assert repo.collect_enrollment(ref, token=token, tasks=tasks)['state'] == 'REVOKED'
    token2 = secrets.token_urlsafe(48)
    ref2 = repo.request_enrollment(usuario='jovino', machine_name='PC2', token=token2, source='test')
    repo.decide_enrollment(ref2, approve=False, actor='admin')
    assert repo.collect_enrollment(ref2, token=token2, tasks=tasks)['state'] == 'REJECTED'
