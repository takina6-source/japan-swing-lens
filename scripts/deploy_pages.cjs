// Use GitHub's authenticated REST artifact listing instead of the failing
// Actions Results list used by deploy-pages@v4. Keep the same Pages/OIDC API.
module.exports = async function deployPages({ github, core, context, artifactName = 'github-pages',
  sleep = ms => new Promise(resolve => setTimeout(resolve, ms)), attempts = 120 }) {
  const { owner, repo } = context.repo;
  const artifacts = await github.rest.actions.listWorkflowRunArtifacts({ owner, repo, run_id: context.runId, per_page: 100 });
  const matches = artifacts.data.artifacts.filter(item => item.name === artifactName && !item.expired);
  if (matches.length !== 1) throw new Error('Exactly one active Pages artifact is required');
  const artifact = matches[0];
  if (artifact.workflow_run?.id !== context.runId || artifact.workflow_run?.head_sha !== context.sha) {
    throw new Error('Pages artifact producer does not match this workflow');
  }
  const idToken = await core.getIDToken();
  core.setSecret(idToken);
  async function request(route, args) {
    try {
      return await github.request(route, { owner, repo, ...args });
    } catch (error) {
      // Do not include request payloads or OIDC credentials in failure logs.
      const safe = new Error(`Pages API request failed (HTTP ${error.status || 'transport'})`);
      safe.status = error.status;
      throw safe;
    }
  }
  const response = await request('POST /repos/{owner}/{repo}/pages/deployments', {
    artifact_id: artifact.id, pages_build_version: context.sha, oidc_token: idToken
  });
  const deployment = response.data;
  const deploymentId = deployment.id || deployment.status_url?.split('/').pop() || context.sha;
  core.setOutput('page_url', deployment.page_url || '');
  core.info(`Created Pages deployment from verified artifact ${artifact.id}`);
  const failures = new Set(['deployment_failed', 'deployment_perms_error', 'deployment_content_failed',
    'deployment_cancelled', 'deployment_lost']);
  let errors = 0;
  for (let attempt = 0; attempt < attempts; attempt++) {
    await sleep(5000);
    let status;
    try {
      const result = await request('GET /repos/{owner}/{repo}/pages/deployments/{deploymentId}', { deploymentId });
      status = result.data.status;
      errors = 0;
    } catch (error) {
      errors++;
      if ((error.status === 429 || error.status >= 500) && errors < 10) continue;
      await cancel();
      throw error;
    }
    if (status === 'succeed') {
      core.info('Pages deployment succeeded');
      return;
    }
    if (failures.has(status)) throw new Error(`Pages deployment failed: ${status}`);
  }
  await cancel();
  throw new Error('Pages deployment timed out');

  async function cancel() {
    try {
      await request('POST /repos/{owner}/{repo}/pages/deployments/{deploymentId}/cancel', { deploymentId });
    } catch (_) {
      core.warning('Could not confirm cancellation of the Pages deployment');
    }
  }
};
