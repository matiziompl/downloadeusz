const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function createApp(saved) {
    const stored = new Map(saved ? [['downloadeusz_settings', JSON.stringify(saved)]] : []);
    const body = { style: { overflow: 'auto' } };
    const context = vm.createContext({
        localStorage: {
            getItem: key => stored.get(key) ?? null,
            setItem: (key, value) => stored.set(key, value),
            removeItem: key => stored.delete(key),
        },
        document: { body, getElementById: () => null }, AbortController, Date,
        clearInterval() {}, clearTimeout() {}, setInterval() { return 1; },
    });
    vm.runInContext(readFileSync(join(__dirname, '../js/script.js'), 'utf8'), context);
    const app = context.appData();
    app.$nextTick = callback => callback();
    let shows = 0, focuses = 0, refreshes = 0;
    app.$refs = {
        queueDialog: {
            open: false,
            showModal() { this.open = true; shows++; },
            close() { this.open = false; },
        },
        queueTrigger: { focus() { focuses++; } },
    };
    app.fetchJobs = async () => { refreshes++; };
    return { app, context, stored, body, counts: () => ({ shows, focuses, refreshes }) };
}

test('queue popup opens once, refreshes jobs, hides errors and locks scrolling', () => {
    const { app, body, counts } = createApp();
    app.errorsOpen = true;
    app.openQueue();
    app.openQueue();
    assert.equal(app.queueOpen, true);
    assert.equal(app.$refs.queueDialog.open, true);
    assert.equal(app.errorsOpen, false);
    assert.equal(body.style.overflow, 'hidden');
    assert.deepEqual(counts(), { shows: 1, focuses: 0, refreshes: 1 });
});

test('reopening queue scrolls terminal to retained output after visible layout', () => {
    const { app, context } = createApp();
    const terminal = { scrollTop: 0, scrollHeight: 0 };
    context.document.getElementById = id => id === 'log-container' ? terminal : null;
    const afterLayout = [];
    app.$nextTick = callback => afterLayout.push(callback);
    app.openQueue();
    app.closeQueue();
    app.queueClosed();
    terminal.scrollHeight = 0; // Native closed dialog has no layout box.
    app.logs = ['last output'];
    app.openQueue();
    terminal.scrollHeight = 640;
    for (const callback of afterLayout) callback();
    assert.equal(terminal.scrollTop, 640);
});

test('closing queue restores focus and scrolling once while retaining active work', () => {
    const { app, body, counts } = createApp();
    const controller = new AbortController();
    let disconnected = false;
    app.jobsController = controller;
    app.eventSource = { close() { disconnected = true; } };
    app.jobs = [{ id: 'active', status: 'running' }];
    app.logs = ['live output'];
    app.activeJobId = 'active';
    app.jobTrackOffset = 100;
    app.openQueue();
    app.closeQueue();
    app.queueClosed(); // Native close, including Escape, uses this handler.
    app.queueClosed();
    assert.equal(app.queueOpen, false);
    assert.equal(app.$refs.queueDialog.open, false);
    assert.equal(body.style.overflow, 'auto');
    assert.equal(counts().focuses, 1);
    assert.equal(controller.signal.aborted, false);
    assert.equal(disconnected, false);
    assert.equal(app.jobs[0].id, 'active');
    assert.equal(app.logs[0], 'live output');
    assert.equal(app.activeJobId, 'active');
    assert.equal(app.jobTrackOffset, 100);
});

test('queue cannot overlap settings, documentation or normalizer in either direction', () => {
    for (const other of ['settings', 'documentation', 'normalizer']) {
        const { app, body, counts } = createApp();
        app[`${other}Open`] = true;
        app.openQueue();
        assert.equal(counts().shows, 0);
        assert.equal(body.style.overflow, 'auto');
        app[`${other}Open`] = false;
        app.openQueue();
        app[`open${other[0].toUpperCase()}${other.slice(1)}`]();
        assert.equal(app[`${other}Open`], false);
        assert.equal(body.style.overflow, 'hidden');
    }
});

test('destroy releases queue scrolling without focusing or reopening removed UI', () => {
    const { app, body, counts } = createApp();
    app.openQueue();
    app.destroy();
    app.queueClosed();
    app.openQueue();
    assert.equal(app.queueOpen, false);
    assert.equal(app.$refs.queueDialog.open, false);
    assert.equal(body.style.overflow, 'auto');
    assert.deepEqual(counts(), { shows: 1, focuses: 0, refreshes: 1 });
});

test('current task details opens queue and focuses the selected worker task', async () => {
    const { app, counts } = createApp();
    app.currentJob = { id: 'worker' };
    const calls = [];
    app.selectJob = async id => calls.push(`select:${id}`);
    app.$refs.queueHeading = { scrollIntoView: () => calls.push('scroll'), focus: () => calls.push('focus') };
    await app.showCurrentJobDetails();
    assert.equal(app.queueOpen, true);
    assert.equal(counts().shows, 1);
    assert.deepEqual(calls, ['select:worker', 'scroll', 'focus']);
});

test('closing queue during a details request prevents focus moving to hidden content', async () => {
    const { app } = createApp();
    app.currentJob = { id: 'worker' };
    let finish;
    app.selectJob = () => new Promise(resolve => { finish = resolve; });
    let focused = false;
    app.$refs.queueHeading = { scrollIntoView() {}, focus() { focused = true; } };
    const pending = app.showCurrentJobDetails();
    app.closeQueue();
    app.queueClosed();
    finish();
    await pending;
    assert.equal(focused, false);
});

test('details action respects another open modal and does not select invisible queue content', async () => {
    const { app, counts } = createApp();
    app.settingsOpen = true;
    app.currentJob = { id: 'worker' };
    let selected = false;
    app.selectJob = async () => { selected = true; };
    await app.showCurrentJobDetails();
    assert.equal(selected, false);
    assert.equal(counts().shows, 0);
});

test('download display preferences retain existing defaults for new and older saved settings', () => {
    for (const saved of [undefined, { showTitle: false }]) {
        const { app } = createApp(saved);
        app.loadSettings();
        assert.equal(app.showCommandPreview, true);
        assert.equal(app.showMetadataButton, true);
    }
});

test('queue widget contains task monitoring, controls, pagination and terminal before settings in dock order', () => {
    const html = readFileSync(join(__dirname, '../index.html'), 'utf8');
    const dialog = html.match(/<dialog id="queue-dialog"[\s\S]*?<\/dialog>/)?.[0] || '';
    assert.ok(dialog);
    assert.match(dialog, /@close="queueClosed\(\)"/);
    assert.match(dialog, /\$event.target === \$el\) closeQueue\(\)/);
    assert.match(dialog, /aria-label="Zamknij kolejkę"/);
    for (const marker of ['id="current-task-heading"', 'id="queue-heading"', 'id="job-track-list"', 'id="log-container"', '@click="cancelJobs()"', '@click="changeJobTrackPage(1)"']) {
        assert.ok(dialog.includes(marker), marker);
        assert.equal(html.split(marker).length - 1, 1, marker);
    }
    const trigger = html.match(/<button[^>]*x-ref="queueTrigger"[\s\S]*?<\/button>/)?.[0] || '';
    assert.match(trigger, /top-24 right-6/);
    assert.match(trigger, /aria-controls="queue-dialog"/);
    assert.ok(html.indexOf('x-ref="queueTrigger"') < html.indexOf('x-ref="settingsTrigger"'));
});

test('download visibility switches share one settings tile and gate controls independently', () => {
    const html = readFileSync(join(__dirname, '../index.html'), 'utf8');
    const settings = html.match(/<dialog id="settings-dialog"[\s\S]*?<\/dialog>/)?.[0] || '';
    const tile = settings.match(/<div[^>]*aria-labelledby="download-tools-heading"[\s\S]*?(?=<!-- Theme Color -->)/)?.[0] || '';
    assert.match(tile, /x-model="showCommandPreview"/);
    assert.match(tile, /x-model="showMetadataButton"/);
    // Find the actual command-preview container rather than another earlier div.
    const preview = html.slice(html.indexOf('<!-- Command Preview -->'));
    const commandExpression = preview.match(/x-show="([^"]+)"/)?.[1];
    const metadata = html.slice(html.indexOf('<!-- SpotDL Fetch Metadata Only Button -->'));
    const metadataExpression = metadata.match(/x-show="([^"]+)"/)?.[1];
    assert.ok(commandExpression);
    assert.ok(metadataExpression);
    for (const [tool, command, button, expectedCommand, expectedButton] of [
        ['spotdl', false, true, false, true], ['spotdl', true, false, true, false],
        ['ytdl', true, true, true, false], ['soulseek', true, true, false, false],
    ]) {
        const context = { selectedTool: tool, showCommandPreview: command, showMetadataButton: button };
        assert.equal(vm.runInNewContext(commandExpression, context), expectedCommand);
        assert.equal(vm.runInNewContext(metadataExpression, context), expectedButton);
    }
});

test('submission feedback remains visible on the main form in either log mode', () => {
    const html = readFileSync(join(__dirname, '../index.html'), 'utf8');
    const form = html.slice(html.indexOf('<!-- Action Button -->'));
    const expression = form.match(/<div x-show="([^"]+)"/)?.[1];
    assert.ok(expression);
    for (const logMode of ['simple', 'advanced']) {
        assert.ok(vm.runInNewContext(expression, { statusMsg: 'Request failed', logMode, queueOpen: false }));
        assert.ok(!vm.runInNewContext(expression, { statusMsg: '', logMode, queueOpen: false }));
    }
});

test('command preview and metadata button can be saved and restored independently', () => {
    for (const [command, metadata] of [[false, true], [true, false], [false, false]]) {
        const { app, stored } = createApp();
        app.showCommandPreview = command;
        app.showMetadataButton = metadata;
        app.saveSettings();
        const saved = JSON.parse(stored.get('downloadeusz_settings'));
        assert.equal(saved.showCommandPreview, command);
        assert.equal(saved.showMetadataButton, metadata);
        const { app: reloaded } = createApp(saved);
        reloaded.loadSettings();
        assert.equal(reloaded.showCommandPreview, command);
        assert.equal(reloaded.showMetadataButton, metadata);
    }
});

test('both download display changes trigger preference persistence without cancelling polling', () => {
    const { app, context, stored } = createApp();
    const watchers = new Map();
    let polls = 0;
    context.setInterval = () => { polls++; return polls; };
    app.$watch = (key, handler) => watchers.set(key, handler);
    app.$nextTick = () => {};
    for (const method of ['fetchVersion', 'fetchAccounts', 'fetchWallpapers', 'fetchErrors', 'fetchBeetsStatus', 'fetchNormalizer']) app[method] = () => {};
    app.initApp();
    assert.equal(polls, 2);
    for (const key of ['showCommandPreview', 'showMetadataButton']) {
        assert.equal(typeof watchers.get(key), 'function');
        app[key] = false;
        watchers.get(key)(false);
        assert.equal(JSON.parse(stored.get('downloadeusz_settings'))[key], false);
    }
});
