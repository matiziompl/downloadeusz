const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const html = readFileSync(join(__dirname, '../index.html'), 'utf8');
const css = readFileSync(join(__dirname, '../css/style.css'), 'utf8');

test('appearance preferences use the same accessible switch as existing compact toggles', () => {
    const labels = [...html.matchAll(/<label\b[^>]*>[\s\S]*?<\/label>/g)].map(match => match[0]);
    for (const model of ['showTitle', 'widgetsDocked', 'showMetadataButton', 'showCommandPreview', 'naviSync']) {
        const label = labels.find(markup => markup.includes(`x-model="${model}"`));
        assert.ok(label, model);
        assert.match(label, /class="[^"]*\btoggle-label\b/);
        assert.match(label, /<input\b[^>]*class="sr-only peer"/);
        assert.match(label, /class="custom-toggle(?: custom-toggle--compact)?"[^>]*aria-hidden="true"/);
    }
    assert.doesNotMatch(html, /peer-checked:after:translate-x-full|after:top-0\.5|after:top-\[1px\]/);
    assert.match(css, /\.custom-toggle::after\s*\{[^}]*top:\s*50%[^}]*transform:\s*translateY\(-50%\)/);
    assert.match(css, /input:focus-visible\s*\+\s*\.custom-toggle/);
    assert.match(css, /\.toggle-label\s*\{[^}]*min-height:\s*44px/);
});

test('dock keyboard order starts with Music and ends with Settings even when errors appear', () => {
    const nav = html.match(/<nav\b[^>]*class="[^"]*widget-launchers[^"]*"[^>]*>([\s\S]*?)<\/nav>/)?.[1];
    assert.ok(nav);
    const buttons = [...nav.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g)].map(match => match[0]);
    assert.match(buttons[0], /x-ref="musicTrigger"/);
    assert.match(buttons.at(-1), /x-ref="settingsTrigger"/);
    assert.equal(buttons.length, 6);
    assert.match(buttons.at(-2), /errors-trigger/);
    assert.match(buttons[0], /top-\[10\.5rem\] right-6/);
    assert.match(buttons.at(-1), /top-6 right-6/);
});

function createApp() {
    const context = vm.createContext({
        localStorage: { getItem: () => null }, AbortController, URLSearchParams,
        document: { body: { style: { overflow: 'hidden' } } },
        clearInterval() {}, clearTimeout() {},
    });
    vm.runInContext(readFileSync(join(__dirname, '../js/script.js'), 'utf8'), context);
    const app = context.appData();
    app.$nextTick = callback => callback();
    app.$refs = { musicEditor: { focus() {}, scrollIntoView() {} }, musicTrashConfirm: { focus() {} }, musicTrashTrigger: { focus() {} }, musicTrigger: { focus() {} } };
    app.musicOpen = true;
    app.musicSelectedTrack = { id: 'track/1', version: 'v1', title: 'Song', artist: 'Artist', relative_path: 'Artist/Song.flac', trashed: false };
    app.musicItems = [app.musicSelectedTrack];
    return { app, context };
}
const response = (data, ok = true) => ({ ok, json: async () => data });

test('the trash tab filters marked tracks without refreshing or moving files', async () => {
    const { app, context } = createApp();
    const calls = [];
    context.fetch = async url => { calls.push(url); return response({ items: [], total: 0 }); };
    assert.ok(app.musicTabs.some(tab => tab.id === 'trash' && tab.label === 'Kosz'));
    await app.setMusicTab('trash');
    let query = new URL(calls.at(-1), 'http://test').searchParams;
    assert.equal(query.get('trashed'), 'true');
    assert.equal(query.get('incomplete'), 'false');
    await app.setMusicTab('incomplete');
    query = new URL(calls.at(-1), 'http://test').searchParams;
    assert.equal(query.get('trashed'), 'false');
    assert.equal(query.get('incomplete'), 'true');
    assert.ok(calls.every(url => url.startsWith('/api/music/tracks?')));
});

test('moving to trash marks the current track directly with no destructive confirmation', async () => {
    const { app, context } = createApp();
    const calls = [];
    context.fetch = async (url, options) => {
        calls.push([url, options]);
        return response(url.endsWith('/trash') ? { status: 'trashed', trash_id: 'trash-1', message: 'Przeniesiono utwór do kosza.' } : { items: [], total: 0 });
    };
    await app.trashMusicTrack();
    assert.equal(calls[0][0], '/api/music/tracks/track%2F1/trash');
    assert.equal(calls[0][1].method, 'POST');
    assert.deepEqual(JSON.parse(calls[0][1].body), { version: 'v1' });
    assert.match(calls[1][0], /^\/api\/music\/tracks\?/);
    assert.equal(app.musicSelectedTrack, null);
    assert.equal(app.musicDeleteConfirmation, null);
    assert.equal(app.trashingMusic, false);
    assert.equal(app.musicItems.length, 0);
    assert.equal(app.musicActionMessage, 'Przeniesiono utwór do kosza.');
});

test('busy tracks and duplicate marking cannot send an extra request', async () => {
    const { app, context } = createApp();
    app.musicPendingEdits = { task: 'track/1' };
    context.fetch = async () => { throw new Error('must not send'); };
    await app.trashMusicTrack();
    assert.equal(app.trashingMusic, false);
    app.musicPendingEdits = {};
    let resolveRequest, requests = 0;
    context.fetch = () => { requests++; return new Promise(resolve => { resolveRequest = resolve; }); };
    const pending = app.trashMusicTrack();
    assert.equal(app.trashingMusic, true);
    app.selectMusicTrack({ id: 'other' });
    assert.equal(app.musicSelectedTrack.id, 'track/1');
    await app.trashMusicTrack();
    assert.equal(requests, 1);
    resolveRequest(response({ detail: 'Plik jest używany przez inne zadanie.' }, false));
    await pending;
    assert.equal(app.trashingMusic, false);
    assert.equal(app.musicSelectedTrack.id, 'track/1');
    assert.match(app.musicTrashError, /Plik jest używany/);
});

test('a stale version or IO failure preserves the selected track and its filter', async () => {
    const { app, context } = createApp();
    context.fetch = async () => response({ detail: 'Plik zmienił się. Odśwież bibliotekę.' }, false);
    await app.trashMusicTrack();
    assert.equal(app.musicSelectedTrack.id, 'track/1');
    assert.equal(app.musicItems.length, 1);
    assert.match(app.musicTrashError, /Plik zmienił się/);
    assert.equal(app.musicTab, 'library');
});

test('restore is restricted to the trash filter and only unhides the marked version', async () => {
    const { app, context } = createApp();
    const calls = [];
    context.fetch = async (url, options) => {
        calls.push([url, options]);
        return response(url.endsWith('/restore') ? { status: 'restored' } : { items: [], total: 0 });
    };
    await app.restoreMusicTrack();
    assert.equal(calls.length, 0);
    app.musicTab = 'trash';
    app.musicSelectedTrack.trashed = true;
    await app.restoreMusicTrack();
    assert.equal(calls[0][0], '/api/music/tracks/track%2F1/restore');
    assert.deepEqual(JSON.parse(calls[0][1].body), { version: 'v1' });
    assert.match(calls[1][0], /trashed=true/);
    assert.equal(app.musicSelectedTrack, null);
});

test('permanent deletion is available only in trash and needs a matching explicit confirmation', async () => {
    const { app, context } = createApp();
    const calls = [];
    context.fetch = async (url, options) => {
        calls.push([url, options]);
        return response(url.endsWith('/delete') ? { status: 'deleted' } : { items: [], total: 0 });
    };
    app.requestMusicDelete();
    await app.deleteMusicTrack();
    assert.equal(calls.length, 0);
    app.musicTab = 'trash';
    app.musicSelectedTrack.trashed = true;
    await app.deleteMusicTrack();
    assert.equal(calls.length, 0);
    app.requestMusicDelete();
    assert.equal(app.musicDeleteConfirmation.title, 'Song');
    assert.equal(app.musicDeleteConfirmation.artist, 'Artist');
    assert.equal(app.musicDeleteConfirmation.relative_path, 'Artist/Song.flac');
    app.cancelMusicDelete();
    await app.deleteMusicTrack();
    assert.equal(calls.length, 0);
    app.requestMusicDelete();
    app.musicSelectedTrack.version = 'new';
    await app.deleteMusicTrack();
    assert.equal(calls.length, 0);
    app.requestMusicDelete();
    await app.deleteMusicTrack();
    assert.equal(calls[0][0], '/api/music/tracks/track%2F1/delete');
    assert.deepEqual(JSON.parse(calls[0][1].body), { version: 'new', confirmed: true });
    assert.equal(app.musicSelectedTrack, null);
});
