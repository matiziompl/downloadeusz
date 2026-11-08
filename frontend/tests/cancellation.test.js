const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function createApp() {
    const context = vm.createContext({
        localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
        AbortController,
        Date,
    });
    vm.runInContext(readFileSync(join(__dirname, '../js/script.js'), 'utf8'), context);
    return { app: context.appData(), context };
}

test('cancelled jobs remain visible, terminal and available for explicit retry', () => {
    const { app } = createApp();
    const job = { id: 'stopped', status: 'cancelled', stage: 'downloading' };
    assert.equal(app.jobStatusText(job), 'Zatrzymano');
    assert.equal(app.trackStatusText(job), 'Zatrzymano');
    assert.equal(app.jobIsActive(job), false);
    assert.equal(app.jobCanRetry(job), true);
});

test('single cancellation sends only that job and closes its logs without deleting history', async () => {
    const { app, context } = createApp();
    const job = { id: 'job /1', status: 'waiting' };
    const other = { id: 'other', status: 'queued' };
    app.jobs = [job, other];
    app.currentJob = job;
    app.jobDetails = job;
    app.logsTaskId = job.id;
    let closed = 0;
    app.eventSource = { close() { closed++; } };
    const calls = [];
    context.fetch = async (url, options) => {
        calls.push([url, options.method]);
        return { ok: true, json: async () => ({ status: 'cancelled', cancelled: 1 }) };
    };
    app.fetchJobs = async () => {};
    await app.cancelJobs(job);
    assert.deepEqual(calls, [['/api/jobs/job%20%2F1/cancel', 'POST']]);
    assert.equal(job.status, 'cancelled');
    assert.equal(other.status, 'queued');
    assert.equal(app.jobs.length, 2);
    assert.equal(app.currentJob, null);
    assert.equal(closed, 1);
    assert.ok(app.jobsActionMessage);
    assert.equal(app.cancellingJobs, '');
});

test('stop all cancels active jobs and preserves completed history', async () => {
    const { app, context } = createApp();
    app.jobs = [{ id: 'a', status: 'running' }, { id: 'b', status: 'waiting' }, { id: 'c', status: 'done' }];
    let endpoint;
    context.fetch = async url => {
        endpoint = url;
        return { ok: true, json: async () => ({ status: 'cancelled', cancelled: 2, message: 'Zatrzymano 2 zadania.' }) };
    };
    app.fetchJobs = async () => {};
    await app.cancelJobs();
    assert.equal(endpoint, '/api/jobs/cancel');
    assert.deepEqual(Array.from(app.jobs, job => job.status), ['cancelled', 'cancelled', 'done']);
    assert.equal(app.jobsActionMessage, 'Zatrzymano 2 zadania.');
});

test('cancellation error persists independently of successful queue polling', async () => {
    const { app, context } = createApp();
    const job = { id: 'a', status: 'running' };
    app.jobs = [job];
    context.fetch = async () => ({ ok: false, json: async () => ({ detail: 'Zadanie zakończyło się wcześniej.' }) });
    await app.cancelJobs(job);
    assert.equal(job.status, 'running');
    assert.equal(app.jobsActionError, 'Zadanie zakończyło się wcześniej.');
    context.fetch = async () => ({ ok: true, json: async () => ({ items: [], counts: {}, current_job: null }) });
    await app.fetchJobs();
    assert.equal(app.jobsActionError, 'Zadanie zakończyło się wcześniej.');
    assert.equal(app.cancellingJobs, '');
});

test('pending cancellation ignores repeated clicks and restores controls after network failure', async () => {
    const { app, context } = createApp();
    let fail;
    let requests = 0;
    context.fetch = () => {
        requests++;
        return new Promise((resolve, reject) => { fail = reject; });
    };
    const pending = app.cancelJobs();
    assert.equal(app.cancellingJobs, '*');
    await app.cancelJobs();
    assert.equal(requests, 1);
    fail(new Error('offline'));
    await pending;
    assert.equal(app.cancellingJobs, '');
    assert.ok(app.jobsActionError);
});

test('queue response from before cancellation cannot resurrect a stopped job', async () => {
    const { app, context } = createApp();
    const job = { id: 'a', status: 'running' };
    app.jobs = [job];
    let releaseOld;
    let queueReads = 0;
    context.fetch = async url => {
        if (url === '/api/jobs/a/cancel') return { ok: true, json: async () => ({ status: 'cancelled', cancelled: 1 }) };
        queueReads++;
        if (queueReads === 1) return new Promise(resolve => { releaseOld = resolve; });
        return { ok: true, json: async () => ({ items: [{ id: 'a', status: 'cancelled' }], counts: { cancelled: 1 } }) };
    };
    app.selectJob = async () => {};
    const oldRead = app.fetchJobs();
    await app.cancelJobs(job);
    releaseOld({ ok: true, json: async () => ({ items: [{ id: 'a', status: 'running' }], counts: { running: 1 } }) });
    await oldRead;
    assert.equal(app.jobs[0].status, 'cancelled');
    assert.equal(queueReads, 2);
});

test('queue controls expose stop all beside refresh and a labelled trash action for active jobs', () => {
    const html = readFileSync(join(__dirname, '../index.html'), 'utf8');
    const queue = html.slice(html.indexOf('aria-labelledby="queue-heading"'), html.indexOf('<!-- Spotify Metadata'));
    assert.match(queue, /@click="cancelJobs\(\)"/);
    assert.match(queue, /Zatrzymaj wszystko/);
    assert.match(queue, /@click="cancelJobs\(job\)"/);
    assert.match(queue, /ph-trash/);
    assert.match(queue, /:aria-label="`Zatrzymaj zadanie:/);
    assert.match(queue, /x-show="jobsActionError" role="alert"/);
});
