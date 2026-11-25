const test = require('node:test');
const assert = require('node:assert/strict');
const deploy = require('../../scripts/deploy_pages.cjs');

function fixture(statuses = ['succeed']) {
  const calls = [], logs = [], outputs = {};
  const artifact = { id: 10, name: 'github-pages', expired: false, workflow_run: { id: 100, head_sha: 'abc' } };
  const github = {
    rest: { actions: { listWorkflowRunArtifacts: async () => ({ data: { artifacts: [artifact] } }) } },
    request: async (route, args) => {
      calls.push({ route, args });
      if (route.endsWith('/cancel')) return { data: {} };
      if (route.startsWith('POST')) return { data: { id: 'deploy-1', page_url: 'https://site.test' } };
      const next = statuses.shift();
      if (next instanceof Error) throw next;
      return { data: { status: next || 'deployment_in_progress' } };
    }
  };
  return { github, artifact, calls, logs, outputs,
    context: { repo: { owner: 'o', repo: 'r' }, runId: 100, sha: 'abc' },
    core: { getIDToken: async () => 'private-oidc', setSecret: value => logs.push('masked'),
      setOutput: (key, value) => { outputs[key] = value; }, info: value => logs.push(value), warning: value => logs.push(value) },
    sleep: async () => {} };
}

test('REST-selected current artifact uses the standard OIDC Pages deployment API', async () => {
  const env = fixture(['deployment_in_progress', 'succeed']);
  await deploy(env);
  assert.equal(env.calls[0].args.artifact_id, 10);
  assert.equal(env.calls[0].args.oidc_token, 'private-oidc');
  assert.equal(env.calls[0].args.pages_build_version, 'abc');
  assert.equal(env.outputs.page_url, 'https://site.test');
  assert.ok(!JSON.stringify(env.logs).includes('private-oidc'));
});

for (const damage of ['wrong-run', 'wrong-commit', 'expired', 'wrong-name', 'duplicate']) {
  test(`reject ${damage} before requesting a deployment`, async () => {
    const env = fixture();
    if (damage === 'wrong-run') env.artifact.workflow_run.id = 99;
    if (damage === 'wrong-commit') env.artifact.workflow_run.head_sha = 'other';
    if (damage === 'expired') env.artifact.expired = true;
    if (damage === 'wrong-name') env.artifact.name = 'other';
    if (damage === 'duplicate') env.github.rest.actions.listWorkflowRunArtifacts = async () => ({ data: { artifacts: [env.artifact, env.artifact] } });
    await assert.rejects(deploy(env));
    assert.deepEqual(env.calls, []);
  });
}

test('temporary API errors retry and terminal content errors fail', async () => {
  const transient = Object.assign(new Error('private payload'), { status: 503 });
  const env = fixture([transient, 'succeed']);
  await deploy(env);
  const failed = fixture(['deployment_content_failed']);
  await assert.rejects(deploy(failed), /content_failed/);
});

test('timeout cancels the deployment instead of reporting success', async () => {
  const env = fixture(['deployment_in_progress']);
  await assert.rejects(deploy({ ...env, attempts: 1 }), /timed out/);
  assert.ok(env.calls.at(-1).route.endsWith('/cancel'));
});

test('API errors never expose request payload or OIDC credentials', async () => {
  const env = fixture([Object.assign(new Error('private-oidc'), { status: 403 })]);
  await assert.rejects(deploy(env), error => error.message === 'Pages API request failed (HTTP 403)');
  assert.ok(!JSON.stringify(env.logs).includes('private-oidc'));
});
