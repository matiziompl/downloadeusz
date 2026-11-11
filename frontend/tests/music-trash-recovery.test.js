const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function createApp() {
    const context = vm.createContext({
        localStorage: { getItem: () => null }, AbortController,
        document: { body: { style: { overflow: 'hidden' } } },
        clearInterval() {}, clearTimeout() {},
    });
    vm.runInContext(readFileSync(join(__dirname, '../js/script.js'), 'utf8'), context);
    const app = context.appData();
    app.$nextTick = callback => callback();
    app.$refs = { musicDialog: { close() {} } };
    app.musicOpen = true;
    app.musicSelectedTrack = { id: 'song', version: 'current', title: 'Song' };
    app.musicItems = [app.musicSelectedTrack];
    return { app, context };
}

test('an unconfirmed success response never removes the selected music row', async () => {
    const { app, context } = createApp();
    let requests = 0;
    context.fetch = async () => {
        requests++;
        return { ok: true, json: async () => ({ status: 'queued' }) };
    };
    await app.trashMusicTrack();
    assert.equal(requests, 1);
    assert.equal(app.musicItems.length, 1);
    assert.equal(app.musicSelectedTrack.id, 'song');
    assert.equal(app.trashingMusic, false);
    assert.match(app.musicTrashError, /nie potwierdził/i);
});

test('destroy aborts each trash operation and ignores late replies including permanent deletion', async () => {
    for (const [method, status] of [['trashMusicTrack', 'trashed'], ['restoreMusicTrack', 'restored'], ['deleteMusicTrack', 'deleted']]) {
        const { app, context } = createApp();
        if (method !== 'trashMusicTrack') {
            app.musicTab = 'trash';
            app.musicSelectedTrack.trashed = true;
            if (method === 'deleteMusicTrack') app.requestMusicDelete();
        }
        let resolveRequest, signal, requests = 0;
        context.fetch = (url, options) => {
            requests++;
            signal = options.signal;
            return new Promise(resolve => { resolveRequest = resolve; });
        };
        const pending = app[method]();
        await app[method]();
        assert.equal(requests, 1);
        app.destroy();
        assert.equal(signal.aborted, true);
        resolveRequest({ ok: true, json: async () => ({ status }) });
        await pending;
        assert.equal(requests, 1);
        assert.equal(app.musicItems.length, 1);
        assert.equal(app.musicSelectedTrack.id, 'song');
        assert.equal(app.musicActionMessage, '');
        assert.equal(app.trashingMusic, false);
    }
});

test('queued focus after a successful mark respects dialog closure and a newly selected track', async () => {
    for (const nextState of ['closed', 'selected']) {
        const { app, context } = createApp();
        const ticks = [];
        let focuses = 0;
        app.$nextTick = callback => ticks.push(callback);
        app.$refs.musicSearch = { focus() { focuses++; } };
        app.fetchMusicTracks = async () => {};
        context.fetch = async () => ({ ok: true, json: async () => ({ status: 'trashed' }) });
        await app.trashMusicTrack();
        if (nextState === 'closed') app.musicOpen = false;
        else app.musicSelectedTrack = { id: 'new' };
        for (const callback of ticks) callback();
        assert.equal(focuses, 0);
    }
});
