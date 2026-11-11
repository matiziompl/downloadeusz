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
    const panel = html.match(/<dialog id="normalizer-dialog"[\s\S]*?<\/dialog>/)?.[0] || '';
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

test('automatic ReplayGain can be changed while Normalizeusz is offline and saves only once', async () => {
    const { app, context } = createApp();
    assert.equal(app.canSetNormalizerEnabled, false);
    app.normalizerStatus = { ...status(), enabled: true, available: false };
    assert.equal(app.canSetNormalizerEnabled, true);
    const calls = [];
    let finish;
    context.fetch = async (url, options) => {
        calls.push({ url, options });
        await new Promise(resolve => { finish = resolve; });
        return { ok: true, json: async () => ({ enabled: false }) };
    };
    const save = app.setNormalizerEnabled(false);
    assert.equal(app.savingNormalizerSettings, true);
    assert.equal(app.normalizerStatus.enabled, true);
    assert.equal(app.canSetNormalizerEnabled, false);
    await app.setNormalizerEnabled(false);
    finish();
    await save;
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, '/api/normalizer/settings');
    assert.equal(calls[0].options.method, 'PUT');
    assert.deepEqual(JSON.parse(calls[0].options.body), { enabled: false });
    assert.equal(app.normalizerStatus.enabled, false);
    assert.equal(app.savingNormalizerSettings, false);
    assert.match(app.normalizerSettingsMessage, /Wyłączono/);
    assert.equal(app.normalizerSettingsError, '');
});

test('failed setting save preserves state and refreshes uncertain server outcome', async () => {
    const { app, context } = createApp();
    app.normalizerStatus = { ...status(), enabled: true };
    let reads = 0;
    context.fetch = async (_, options) => {
        if (options.method === 'PUT') return { ok: false, json: async () => ({ detail: 'Nie udało się zapisać ustawienia' }) };
        reads++;
        return { ok: true, json: async () => ({ ...status(), enabled: true }) };
    };
    await app.setNormalizerEnabled(false);
    assert.equal(app.normalizerStatus.enabled, true);
    assert.equal(reads, 1);
    assert.equal(app.normalizerSettingsMessage, '');
    assert.equal(app.normalizerSettingsError, 'Nie udało się zapisać ustawienia');
    assert.equal(app.savingNormalizerSettings, false);
    app.normalizerError = 'Stan nieaktualny';
    assert.equal(app.canSetNormalizerEnabled, false);
});

test('stale polling response cannot overwrite saved setting and polling pauses during save', async () => {
    const { app, context } = createApp();
    app.normalizerStatus = { ...status(), enabled: true };
    let finishRead, finishSave, readSignal;
    let reads = 0;
    context.fetch = async (_, options) => {
        if (options.method === 'PUT') {
            await new Promise(resolve => { finishSave = resolve; });
            return { ok: true, json: async () => ({ enabled: false }) };
        }
        reads++;
        readSignal = options.signal;
        await new Promise(resolve => { finishRead = resolve; });
        return { ok: true, json: async () => ({ ...status(), enabled: true }) };
    };
    const read = app.fetchNormalizer();
    const save = app.setNormalizerEnabled(false);
    assert.equal(readSignal.aborted, true);
    await app.fetchNormalizer(true);
    assert.equal(reads, 1);
    finishSave();
    await save;
    finishRead();
    await read;
    assert.equal(app.normalizerStatus.enabled, false);
    assert.equal(app.fetchingNormalizer, false);
});

test('invalid setting response is an error and destroy aborts save without late updates', async () => {
    const { app, context } = createApp();
    app.normalizerStatus = { ...status(), enabled: true };
    context.fetch = async (_, options) => options.method === 'PUT'
        ? { ok: true, json: async () => ({ enabled: 'false' }) }
        : { ok: true, json: async () => ({ ...status(), enabled: true }) };
    await app.setNormalizerEnabled(false);
    assert.ok(app.normalizerSettingsError);
    assert.equal(app.normalizerStatus.enabled, true);
    let finish, signal;
    context.fetch = async (_, options) => {
        signal = options.signal;
        await new Promise(resolve => { finish = resolve; });
        return { ok: true, json: async () => ({ enabled: false }) };
    };
    const save = app.setNormalizerEnabled(false);
    app.destroy();
    assert.equal(signal.aborted, true);
    finish();
    await save;
    assert.equal(app.normalizerStatus.enabled, true);
    assert.equal(app.normalizerSettingsMessage, '');
});

test('automatic setting control has stable name, switch semantics and associated explanation', () => {
    const html = readFileSync(join(__dirname, '../index.html'), 'utf8');
    assert.match(html, /role="switch"[\s\S]*?aria-labelledby="normalizer-setting-label"/);
    assert.match(html, /id="normalizer-setting-label">ReplayGain po imporcie/);
    assert.match(html, /aria-describedby="normalizer-setting-help"/);
    assert.match(html, /id="normalizer-setting-help"/);
    assert.match(html, /:disabled="!canSetNormalizerEnabled"/);
});

function popupApp() {
    const { app, context } = createApp();
    const body = { style: { overflow: 'auto' } };
    context.document = { body };
    let shows = 0, closes = 0, focuses = 0, refreshes = 0;
    app.$refs = {
        normalizerDialog: {
            open: false,
            showModal() { this.open = true; shows++; },
            close() { this.open = false; closes++; },
        },
        normalizerTrigger: { focus() { focuses++; } },
    };
    app.fetchNormalizer = force => { assert.equal(force, true); refreshes++; };
    return { app, body, counts: () => ({ shows, closes, focuses, refreshes }) };
}

test('normalizer popup opens once, refreshes state and closes errors', () => {
    const { app, body, counts } = popupApp();
    app.errorsOpen = true;
    app.openNormalizer();
    app.openNormalizer();
    assert.equal(app.normalizerOpen, true);
    assert.equal(app.$refs.normalizerDialog.open, true);
    assert.equal(body.style.overflow, 'hidden');
    assert.equal(app.settingsOpen, false);
    assert.equal(app.errorsOpen, false);
    assert.deepEqual(counts(), { shows: 1, closes: 0, focuses: 0, refreshes: 1 });
});

test('native popup close restores scroll and focus without cancelling normalizer work', () => {
    const { app, body, counts } = popupApp();
    const action = new AbortController();
    const save = new AbortController();
    app.normalizerActionController = action;
    app.normalizerSettingsController = save;
    app.submittingNormalizer = 'scan_library';
    app.savingNormalizerSettings = true;
    app.normalizerStatus = status([preview()]);
    app.openNormalizer();
    app.closeNormalizer();
    app.normalizerClosed(); // The same @close handler runs for Escape or backdrop close.
    app.normalizerClosed();
    assert.equal(app.normalizerOpen, false);
    assert.equal(body.style.overflow, 'auto');
    assert.equal(counts().focuses, 1);
    assert.equal(action.signal.aborted, false);
    assert.equal(save.signal.aborted, false);
    assert.equal(app.submittingNormalizer, 'scan_library');
    assert.equal(app.savingNormalizerSettings, true);
    assert.equal(app.normalizerRequests.length, 1);
});

test('documentation and normalizer popup cannot overlap or overwrite the scroll restore value', () => {
    const { app, body, counts } = popupApp();
    app.documentationOpen = true;
    app.openNormalizer();
    assert.equal(counts().shows, 0);
    assert.equal(body.style.overflow, 'auto');
    app.documentationOpen = false;
    app.openNormalizer();
    app.openDocumentation();
    assert.equal(app.documentationOpen, false);
    assert.equal(body.style.overflow, 'hidden');
});

test('destroy closes the popup and releases scrolling without focusing a destroyed trigger', () => {
    const { app, body, counts } = popupApp();
    app.openNormalizer();
    app.destroy();
    app.normalizerClosed();
    assert.equal(app.normalizerOpen, false);
    assert.equal(app.$refs.normalizerDialog.open, false);
    assert.equal(body.style.overflow, 'auto');
    assert.equal(counts().focuses, 0);
    app.openNormalizer();
    assert.equal(counts().shows, 1);
});

test('all normalizer controls live in one native dialog with its trigger under documentation', () => {
    const html = readFileSync(join(__dirname, '../index.html'), 'utf8');
    const dialog = html.match(/<dialog id="normalizer-dialog"[\s\S]*?<\/dialog>/)?.[0] || '';
    assert.ok(dialog);
    assert.match(dialog, /@close="normalizerClosed\(\)"/);
    assert.match(dialog, /\$event.target === \$el\) closeNormalizer\(\)/);
    assert.match(dialog, /aria-label="Zamknij Normalizeusza"/);
    for (const marker of ['id="normalizer-setting-label"', "runNormalizer('scan_library')", "runNormalizer('preview_albums')", "runNormalizer('repair_albums')", 'Ostatnie zadania Normalizeusza']) {
        assert.ok(dialog.includes(marker), marker);
        assert.equal(html.split(marker).length - 1, 1, marker);
    }
    const trigger = html.match(/<button[^>]*x-ref="normalizerTrigger"[\s\S]*?<\/button>/)?.[0] || '';
    assert.match(trigger, /top-24 left-6/);
    assert.match(trigger, /aria-controls="normalizer-dialog"/);
    assert.ok(html.indexOf('x-ref="documentationTrigger"') < html.indexOf('x-ref="normalizerTrigger"'));
    assert.match(html, /aria-label="Pokaż błędy"[^>]*top-\[10\.5rem\]/);
});

test('left widgets give the open error panel space and return when it closes or empties', () => {
    const html = readFileSync(join(__dirname, '../index.html'), 'utf8');
    const buttons = html.match(/<button\b(?:"[^"]*"|'[^']*'|[^'">])*>/g) || [];
    const button = marker => buttons.find(tag => tag.includes(marker)) || '';
    const visibility = markup => {
        const expression = markup.match(/x-show="([^"]*)"/)?.[1];
        assert.ok(expression, markup);
        return state => vm.runInNewContext(expression, state);
    };
    const documentationVisible = visibility(button('x-ref="documentationTrigger"'));
    const normalizerVisible = visibility(button('x-ref="normalizerTrigger"'));
    const errorsVisible = visibility(button('aria-label="Pokaż błędy"'));
    for (const visible of [documentationVisible, normalizerVisible]) {
        assert.equal(visible({ errorsOpen: true, errorsList: [{}] }), false);
        assert.equal(visible({ errorsOpen: false, errorsList: [{}] }), true);
        assert.equal(visible({ errorsOpen: true, errorsList: [] }), true);
        assert.equal(visible({ errorsOpen: false, errorsList: [] }), true);
    }
    assert.equal(errorsVisible({ errorsOpen: true, errorsList: [{}] }), false);
    assert.equal(errorsVisible({ errorsOpen: false, errorsList: [{}] }), true);
    assert.equal(errorsVisible({ errorsOpen: true, errorsList: [] }), false);
    assert.equal(errorsVisible({ errorsOpen: false, errorsList: [] }), false);
});
