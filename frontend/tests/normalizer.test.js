const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function createApp() {
    const context = vm.createContext({
        localStorage: { getItem: () => null }, AbortController, Date,
        clearInterval: () => {}, clearTimeout: () => {},
    });
    vm.runInContext(readFileSync(join(__dirname, '../js/script.js'), 'utf8'), context);
    return { app: context.appData(), context };
}

function status(requests = []) {
    return { enabled: false, available: true, version: '1', message: '',
        capabilities: ['preview_albums', 'repair_albums', 'scan_library'],
        counts: { pending: 0, processing: 0 }, requests };
}

function preview(overrides = {}) {
    return { id: 'preview', operation: 'preview_albums', status: 'done', created_at: 10,
        result: { processed: 4, changed: 2, skipped: 1, errors: 1, samples: [], error_samples: [] },
        ...overrides };
}

test('manual actions use online capabilities even with automatic normalization disabled', async () => {
    const { app, context } = createApp();
    context.fetch = async () => ({ ok: true, json: async () => status() });
    await app.fetchNormalizer();
    assert.equal(app.canRunNormalizer('scan_library'), true);
    assert.equal(app.canRunNormalizer('repair_albums'), false);
    app.normalizerStatus.capabilities = ['preview_albums'];
    assert.equal(app.canRunNormalizer('scan_library'), false);
    app.normalizerStatus.available = false;
    assert.equal(app.canRunNormalizer('preview_albums'), false);
});

test('repair requires the newest preview to succeed after the last repair', () => {
    const { app } = createApp();
    app.normalizerStatus = status([preview()]);
    assert.equal(app.canRunNormalizer('repair_albums'), true);
    app.normalizerStatus.requests.unshift(preview({ id: 'new', status: 'failed', created_at: 20 }));
    assert.equal(app.canRunNormalizer('repair_albums'), false);
    app.normalizerStatus.requests = [preview(), { id: 'repair', operation: 'repair_albums', status: 'done', created_at: 11 }];
    assert.equal(app.canRunNormalizer('repair_albums'), false);
    app.normalizerStatus.requests = [preview({ result: { changed: 0 } })];
    assert.equal(app.canRunNormalizer('repair_albums'), false);
    app.normalizerStatus.requests = [{ id: 'running', operation: 'preview_albums', status: 'pending', created_at: 20 }, preview()];
    assert.equal(app.canRunNormalizer('repair_albums'), false);
    assert.equal(app.canRunNormalizer('preview_albums'), false);
});

test('offline and invalid responses retain history but disable stale actions until a fresh response', async () => {
    const { app, context } = createApp();
    app.normalizerStatus = status([preview()]);
    context.fetch = async () => { throw new TypeError('offline'); };
    await app.fetchNormalizer();
    assert.equal(app.normalizerRequests.length, 1);
    assert.ok(app.normalizerError);
    assert.equal(app.canRunNormalizer('repair_albums'), false);
    context.fetch = async () => ({ ok: true, json: async () => ({ available: true }) });
    await app.fetchNormalizer();
    assert.ok(app.normalizerError);
    context.fetch = async () => ({ ok: true, json: async () => status([preview()]) });
    await app.fetchNormalizer();
    assert.equal(app.normalizerError, '');
    assert.equal(app.canRunNormalizer('repair_albums'), true);
});

test('explicit operation submits selected Navidrome account and prevents duplicate clicks', async () => {
    const { app, context } = createApp();
    app.normalizerStatus = status([preview()]);
    app.selectedNaviAccount = 'account-2';
    const calls = [];
    let finish;
    context.fetch = async (url, options) => {
        calls.push({ url, options });
        if (options?.method === 'POST') {
            await new Promise(resolve => { finish = resolve; });
            return { ok: true, json: async () => ({ id: 'repair-2', status: 'pending' }) };
        }
        return { ok: true, json: async () => status([{ id: 'repair-2', operation: 'repair_albums', status: 'pending', created_at: 20 }, preview()]) };
    };
    const submitted = app.runNormalizer('repair_albums');
    await app.runNormalizer('repair_albums');
    finish();
    await submitted;
    assert.equal(calls.filter(call => call.options?.method === 'POST').length, 1);
    assert.deepEqual(JSON.parse(calls[0].options.body), { operation: 'repair_albums', navi_account_id: 'account-2' });
    assert.equal(calls[0].url, '/api/normalizer/jobs');
    assert.equal(app.canRunNormalizer('repair_albums'), false);
    assert.match(app.normalizerActionMessage, /kolejki/);
});

test('failed operation can be explicitly retried and API failure is visible', async () => {
    const { app, context } = createApp();
    app.normalizerStatus = status([{ id: 'failed', operation: 'scan_library', status: 'failed' }]);
    assert.equal(app.canRunNormalizer('scan_library'), true);
    context.fetch = async () => ({ ok: false, json: async () => ({ detail: 'Worker nie odpowiada' }) });
    await app.runNormalizer('scan_library');
    assert.equal(app.normalizerActionError, 'Worker nie odpowiada');
    assert.equal(app.submittingNormalizer, '');
});

test('progress reports real counters with unknown totals and bounded samples', () => {
    const { app } = createApp();
    assert.equal(app.normalizerProgressText({ status: 'processing', progress: { processed: 42, total: null, changed: 5, skipped: 30, errors: 1 } }), 'Sprawdzone: 42 · Zmienione: 5 · Pominięte: 30 · Błędy: 1');
    assert.match(app.normalizerProgressText(preview()), /Do uzupełnienia: 2/);
    assert.equal(app.normalizerProgressText({ operation: 'scan_library', status: 'done',
        result: { processed: 42, changed: 0, skipped: 0, errors: 1 } }), 'Sprawdzone: 42 · Błędy: 1');
    assert.equal(app.normalizerProgressText({ status: 'pending' }), 'Czeka na rozpoczęcie przez Normalizeusza.');
    const dangerous = '<img src=x onerror=alert(1)>';
    const request = preview({ result: { samples: Array.from({ length: 30 }, () => ({ path: dangerous, title: dangerous, album: dangerous })) } });
    assert.equal(app.normalizerSamples(request).length, 5);
    assert.equal(app.normalizerSamples(request)[0].path, dangerous);
    const html = readFileSync(join(__dirname, '../index.html'), 'utf8');
    const panel = html.slice(html.indexOf('aria-labelledby="normalizer-heading"'), html.indexOf('<!-- spotdl Advanced Flags -->'));
    assert.ok(panel.length > 0);
    assert.doesNotMatch(panel, /x-html/);
    assert.match(panel, /x-text="sample.path"/);
    assert.match(panel, /x-text="request.progress\?\.current_path"/);
});

test('destroy aborts reads and submits and prevents late status updates', async () => {
    const { app, context } = createApp();
    let finish;
    let signal;
    context.fetch = async (_, options) => {
        signal = options.signal;
        await new Promise(resolve => { finish = resolve; });
        return { ok: true, json: async () => status() };
    };
    const read = app.fetchNormalizer();
    const action = new AbortController();
    app.normalizerActionController = action;
    app.destroy();
    assert.equal(signal.aborted, true);
    assert.equal(action.signal.aborted, true);
    finish();
    await read;
    assert.equal(app.normalizerStatus, null);
});
