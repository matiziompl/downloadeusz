const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function createApp() {
    const context = vm.createContext({
        localStorage: { getItem: () => null },
        AbortController,
        Date,
    });
    vm.runInContext(readFileSync(join(__dirname, '../js/script.js'), 'utf8'), context);
    return { app: context.appData(), context };
}

test('current task follows the API independently of the selected history entry', async () => {
    const { app, context } = createApp();
    app.activeJobId = 'old-history-entry';
    const selected = [];
    app.selectJob = async id => selected.push(id);
    context.fetch = async () => ({ ok: true, json: async () => ({
        items: [{ id: 'old-history-entry', status: 'done' }], counts: { running: 1 },
        current_job: { id: 'actual-worker-job', status: 'running', stage: 'searching' },
    }) });
    await app.fetchJobs();
    assert.equal(app.currentJob.id, 'actual-worker-job');
    assert.equal(app.activeJobId, 'old-history-entry');
    assert.deepEqual(selected, ['old-history-entry']);
});

test('progress uses whole-job counts instead of the selected details page', () => {
    const { app } = createApp();
    app.currentJob = { tracks_total: 501, track_counts: {
        imported: 210, existing: 98, done: 1, skipped: 2, failed: 3, needs_review: 4,
    } };
    app.jobDetails = { tracks_total: 2, tracks_offset: 100, tracks: [{ status: 'done' }] };
    assert.equal(app.currentTaskProgress, 'Zakończone utwory: 311 z 501');
    assert.equal(app.currentTaskIssues, 'Błędy: 3 · Do sprawdzenia: 4');
    app.currentJob = { tracks_total: 0, track_counts: {} };
    assert.equal(app.currentTaskProgress, '');
    assert.equal(app.currentTaskIssues, '');
});

test('waiting describes the real stage and the next check time', () => {
    const { app } = createApp();
    app.currentJob = { status: 'waiting', stage: 'normalizing', next_attempt: Date.now() / 1000 + 60 };
    assert.equal(app.jobStatusText(app.currentJob), 'Oczekiwanie · Wyrównywanie głośności · ReplayGain');
    assert.match(app.currentTaskRetryText, /^Następne sprawdzenie:/);
    app.currentJob.next_attempt = 0;
    assert.equal(app.currentTaskRetryText, '');
});

test('search and current track are explicit API data, never inferred from paginated tracks', () => {
    const { app } = createApp();
    app.currentJob = { status: 'running', stage: 'searching', tracks_total: 501,
        current_track: { position: 420, title: 'Song', artist: 'Artist' },
        search: { query: '<img src=x onerror=alert(1)>', attempt: 2, total: 3 } };
    assert.equal(app.currentTaskTrackText, 'Artist — Song');
    assert.equal(app.currentTaskTrackPosition, 'Utwór 421 z 501');
    assert.equal(app.currentTaskSearch.query, '<img src=x onerror=alert(1)>');
    app.currentJob.stage = 'importing';
    assert.equal(app.currentTaskSearch, null);
    app.currentJob.current_track = null;
    assert.equal(app.currentTaskTrackText, '');
});

test('null and failed queue responses clear the automatic card', async () => {
    const { app, context } = createApp();
    app.currentJob = { id: 'old' };
    context.fetch = async () => ({ ok: true, json: async () => ({ items: [], counts: {}, current_job: null }) });
    await app.fetchJobs();
    assert.equal(app.currentJob, null);
    app.currentJob = { id: 'stale' };
    context.fetch = async () => { throw new Error('offline'); };
    await app.fetchJobs();
    assert.equal(app.currentJob, null);
    assert.ok(app.jobsError);
});

test('details action selects the worker job and keeps automatic tracking separate', async () => {
    const { app } = createApp();
    app.currentJob = { id: 'worker' };
    app.queueOpen = true;
    let selected;
    const calls = [];
    app.selectJob = async id => { selected = id; };
    app.$refs = { queueHeading: { scrollIntoView: () => calls.push('scroll'), focus: () => calls.push('focus') } };
    await app.showCurrentJobDetails();
    assert.equal(selected, 'worker');
    assert.equal(app.currentJob.id, 'worker');
    assert.deepEqual(calls, ['scroll', 'focus']);
});

test('current task lives inside queue popup before history and renders search values as text', () => {
    const html = readFileSync(join(__dirname, '../index.html'), 'utf8');
    const dialog = html.match(/<dialog id="queue-dialog"[\s\S]*?<\/dialog>/)?.[0] || '';
    assert.ok(dialog);
    const cardStart = dialog.indexOf('aria-labelledby="current-task-heading"');
    const queueStart = dialog.indexOf('aria-labelledby="queue-heading"');
    assert.ok(cardStart >= 0);
    assert.ok(cardStart < queueStart);
    const card = dialog.slice(cardStart, queueStart);
    assert.match(card, /x-show="currentJob"/);
    assert.match(card, /x-text="currentTaskSearch\?\.query/);
    assert.doesNotMatch(card, /x-html/);
});
