const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function createApp(saved = null) {
    const storage = new Map(saved ? [['downloadeusz_settings', JSON.stringify(saved)]] : []);
    const body = { style: { overflow: 'auto' } };
    const context = vm.createContext({
        localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
        AbortController, URLSearchParams, Date,
        clearInterval() {}, clearTimeout() {},
        document: { body },
    });
    vm.runInContext(readFileSync(join(__dirname, '../js/script.js'), 'utf8'), context);
    const app = context.appData();
    let shows = 0, focuses = 0;
    app.$refs = {
        musicDialog: { showModal() { shows++; }, close() {} },
        musicTrigger: { focus() { focuses++; } },
        musicEditor: { scrollIntoView() {}, focus() {} },
    };
    app.$nextTick = callback => callback();
    return { app, context, body, storage, counts: () => ({ shows, focuses }) };
}
const response = (data, ok = true) => ({ ok, json: async () => data });
const track = (id = 'one') => ({ id, title: `Song ${id}`, artist: 'Artist', album: 'Album', relative_path: `${id}.flac` });

test('music popup opens once, refreshes index before listing and restores focus and scroll once', async () => {
    const { app, context, body, counts } = createApp();
    const calls = [];
    context.fetch = async (url, options) => {
        calls.push([url, options?.method]);
        return response(url.includes('/refresh') ? { total: 1 } : { items: [track()], total: 1, offset: 0, limit: 48 });
    };
    app.errorsOpen = true;
    await app.openMusic();
    await app.openMusic();
    assert.equal(calls[0][0], '/api/music/refresh');
    assert.equal(calls[0][1], 'POST');
    assert.match(calls[1][0], /^\/api\/music\/tracks\?/);
    assert.equal(app.errorsOpen, false);
    assert.equal(body.style.overflow, 'hidden');
    assert.equal(app.musicItems.length, 1);
    assert.equal(counts().shows, 1);
    app.closeMusic(); app.musicClosed(); app.musicClosed();
    assert.equal(body.style.overflow, 'auto');
    assert.equal(counts().focuses, 1);
});

test('music popup and other native dialogs cannot overlap', async () => {
    for (const other of ['settingsOpen', 'queueOpen', 'normalizerOpen', 'documentationOpen']) {
        const { app, body, counts } = createApp();
        app[other] = true;
        await app.openMusic();
        assert.equal(counts().shows, 0);
        assert.equal(body.style.overflow, 'auto');
    }
    const { app, body } = createApp();
    app.musicOpen = true; body.style.overflow = 'hidden';
    app.openSettings(); app.openQueue(); app.openNormalizer(); app.openDocumentation();
    assert.equal(app.settingsOpen || app.queueOpen || app.normalizerOpen || app.documentationOpen, false);
    assert.equal(body.style.overflow, 'hidden');
});

test('column preference is validated, saved and restored without resetting other preferences', () => {
    const { app, storage } = createApp({ musicColumns: 6, showCommandPreview: false });
    app.loadSettings();
    assert.equal(app.musicColumns, 6);
    app.musicColumns = 3; app.saveSettings();
    assert.equal(JSON.parse(storage.get('downloadeusz_settings')).musicColumns, 3);
    assert.equal(app.showCommandPreview, false);
    const invalid = createApp({ musicColumns: 100 }).app;
    invalid.loadSettings(); assert.equal(invalid.musicColumns, 4);
});

test('search, incomplete filter and pagination query the server index without refreshing files', async () => {
    const { app, context } = createApp(); const calls = [];
    context.fetch = async url => { calls.push(url); return response({ items: [track()], total: 100, offset: app.musicOffset, limit: 48 }); };
    app.musicOpen = true; app.musicQuery = 'Title & Album'; app.musicOffset = 48;
    await app.searchMusic();
    assert.equal(app.musicOffset, 0);
    await app.setMusicTab('incomplete');
    await app.changeMusicPage(1);
    assert.equal(app.musicOffset, 48);
    for (const url of calls) assert.match(url, /^\/api\/music\/tracks\?/);
    const query = new URL(calls.at(-1), 'http://test').searchParams;
    assert.equal(query.get('q'), 'Title & Album');
    assert.equal(query.get('incomplete'), 'true');
    assert.equal(query.get('offset'), '48');
    assert.equal(query.get('limit'), '48');
});

test('older search response cannot overwrite newer results', async () => {
    const { app, context } = createApp(); const pending = [];
    context.fetch = () => new Promise(resolve => pending.push(resolve));
    app.musicOpen = true; app.musicQuery = 'old';
    const old = app.fetchMusicTracks();
    app.musicQuery = 'new'; const latest = app.fetchMusicTracks();
    pending[1](response({ items: [track('new')], total: 1, offset: 0, limit: 48 })); await latest;
    pending[0](response({ items: [track('old')], total: 1, offset: 0, limit: 48 })); await old;
    assert.equal(app.musicItems[0].id, 'new');
    assert.equal(app.fetchingMusic, false);
});

test('preview responses are ignored after changing song and errors preserve entered URL', async () => {
    const { app, context } = createApp(); let finish;
    app.musicOpen = true; app.selectMusicTrack(track()); app.musicUrl = 'https://musicbrainz.org/release/one';
    context.fetch = () => new Promise(resolve => { finish = resolve; });
    const pending = app.previewMusic();
    app.selectMusicTrack(track('two'));
    finish(response({ preview_token: 'old', current: track(), proposed: { album: 'Old' }, destination: 'old.flac' })); await pending;
    assert.equal(app.musicPreview, null);
    app.musicUrl = 'https://musicbrainz.org/recording/two';
    context.fetch = async () => response({ detail: 'Plik zmienił się. Odśwież bibliotekę.' }, false);
    await app.previewMusic();
    assert.match(app.musicEditorError, /Plik zmienił/);
    assert.equal(app.musicUrl, 'https://musicbrainz.org/recording/two');
    assert.equal(app.musicSelectedTrack.id, 'two');
});

test('release and track choice require explicit selection and preserve server-issued key', async () => {
    const { app, context } = createApp(); const calls = [];
    app.musicOpen = true; app.selectMusicTrack(track()); app.musicUrl = 'https://musicbrainz.org/recording/one';
    context.fetch = async (url, options) => {
        calls.push(JSON.parse(options.body));
        return response(calls.length === 1
            ? { current: track(), choices: [{ key: 'release-1', kind: 'release', label: 'Edition', url: 'https://musicbrainz.org/release/one' }] }
            : calls.length === 2
                ? { current: track(), choices: [{ key: 'track-2', kind: 'track', label: 'Song' }] }
                : { preview_token: 'token', current: track(), proposed: { title: 'Song', album: 'Edition' }, destination: 'Artist/Edition/Song.flac' });
    };
    await app.previewMusic();
    assert.equal(app.musicChoiceKey, ''); assert.equal(app.musicPreview, null);
    await app.chooseMusicMatch(); assert.equal(calls.length, 1);
    app.musicChoiceKey = 'release-1'; await app.chooseMusicMatch();
    assert.equal(calls[1].url, 'https://musicbrainz.org/release/one');
    assert.equal(calls[1].candidate_key, '');
    app.musicChoiceKey = 'track-2'; await app.chooseMusicMatch();
    assert.equal(calls[2].candidate_key, 'track-2');
    assert.equal(app.musicPreview.preview_token, 'token');
});

test('album title action only previews and invalidating input disables an older preview', async () => {
    const { app, context } = createApp(); let body;
    app.musicOpen = true; app.selectMusicTrack(track());
    context.fetch = async (url, options) => { body = JSON.parse(options.body); return response({ preview_token: 'title', current: track(), proposed: { album: 'Song one' }, destination: 'one.flac', action: 'title' }); };
    await app.previewMusic('title');
    assert.equal(body.action, 'title'); assert.equal(body.url, '');
    assert.equal(app.musicPreview.proposed.album, 'Song one');
    app.invalidateMusicPreview(); assert.equal(app.musicPreview, null);
});

test('apply queues once, keeps popup in place and refreshes only on successful job completion', async () => {
    const { app, context } = createApp(); let finish; let submits = 0, refreshes = 0;
    const connected = [];
    app.musicOpen = true; app.selectMusicTrack(track());
    app.musicPreview = { preview_token: 'token' }; app.selectedNaviAccount = 'account'; app.naviSync = true;
    app.connectLogs = id => connected.push(id); app.fetchJobs = async () => {};
    app.refreshMusic = async () => { refreshes++; };
    context.fetch = async (url, options) => {
        submits++;
        assert.deepEqual(JSON.parse(options.body), { preview_token: 'token', navi_account_id: 'account', navi_sync: true });
        return new Promise(resolve => { finish = resolve; });
    };
    const pending = app.applyMusicPreview(); await app.applyMusicPreview();
    finish(response({ task_id: 'edit-one', message: 'Dodano zmianę do kolejki.' })); await pending;
    assert.equal(submits, 1); assert.deepEqual(connected, ['edit-one']);
    assert.equal(app.musicOpen, true); assert.equal(app.queueOpen, false);
    assert.equal(app.musicPreview, null);
    app.trackMusicJobs([{ id: 'edit-one', kind: 'music_edit', status: 'running' }]);
    assert.equal(refreshes, 0);
    app.trackMusicJobs([{ id: 'edit-one', kind: 'music_edit', status: 'done' }]);
    app.trackMusicJobs([{ id: 'edit-one', kind: 'music_edit', status: 'done' }]);
    assert.equal(refreshes, 1);
});

test('failed apply preserves preview and URL; destroy aborts pending operations without returning focus', async () => {
    const { app, context, body, counts } = createApp();
    app.musicOpen = true; app.musicPreviousOverflow = 'auto'; body.style.overflow = 'hidden';
    app.selectMusicTrack(track()); app.musicUrl = 'https://musicbrainz.org/release/one'; app.musicPreview = { preview_token: 'token' };
    context.fetch = async () => response({ detail: 'Podgląd wygasł.' }, false);
    await app.applyMusicPreview();
    assert.equal(app.musicPreview.preview_token, 'token');
    assert.equal(app.musicUrl, 'https://musicbrainz.org/release/one');
    assert.match(app.musicEditorError, /wygasł/);
    const controller = new AbortController(); app.musicController = controller;
    app.destroy(); app.musicClosed();
    assert.equal(controller.signal.aborted, true);
    assert.equal(body.style.overflow, 'auto');
    assert.equal(counts().focuses, 0);
});

test('job completion during an existing refresh schedules one further index refresh', async () => {
    const { app, context } = createApp(); let finish; let posts = 0;
    app.musicOpen = true;
    app.trackMusicJobs([{ id: 'import-one', kind: 'import', status: 'running' }]);
    context.fetch = async (url, options) => {
        if (options?.method === 'POST') {
            posts++;
            if (posts === 1) return new Promise(resolve => { finish = resolve; });
            return response({ total: 1 });
        }
        return response({ items: [track()], total: 1, offset: 0, limit: 48 });
    };
    const first = app.refreshMusic();
    app.trackMusicJobs([{ id: 'import-one', kind: 'import', status: 'done' }]);
    finish(response({ total: 1 })); await first;
    assert.equal(posts, 2);
});

test('refresh failure is visible while the previous server index remains available', async () => {
    const { app, context } = createApp();
    app.musicOpen = true;
    context.fetch = async (url, options) => options?.method === 'POST'
        ? response({ detail: 'Nie można teraz sprawdzić plików.' }, false)
        : response({ items: [track()], total: 1, offset: 0, limit: 48 });
    await app.refreshMusic();
    assert.match(app.musicRefreshError, /Nie można teraz/);
    assert.equal(app.musicItems[0].id, 'one');
    assert.equal(app.refreshingMusic, false);
});

test('artwork comparison keeps existing cover for title fallback and uses explicit release artwork', () => {
    const { app } = createApp();
    app.musicSelectedTrack = { ...track(), cover_url: '/api/music/tracks/one/cover' };
    app.musicPreview = { current: app.musicSelectedTrack, proposed: { album: 'Song one' }, action: 'title' };
    assert.equal(app.musicCurrentCover, '/api/music/tracks/one/cover');
    assert.equal(app.musicProposedCover, app.musicCurrentCover);
    app.musicPreview.proposed.cover_url = 'https://coverartarchive.org/release/example/front-250';
    assert.equal(app.musicProposedCover, app.musicPreview.proposed.cover_url);
    app.musicCurrentCoverError = true; app.musicProposedCoverError = true;
    app.invalidateMusicPreview();
    assert.equal(app.musicCurrentCoverError, false); assert.equal(app.musicProposedCoverError, false);
});

test('both music organization entry points live in the music dialog, while service testing and status stay on main', () => {
    const html = readFileSync(join(__dirname, '../index.html'), 'utf8');
    const music = html.match(/<dialog id="music-dialog"[\s\S]*?<\/dialog>/)?.[0] || '';
    const main = html.match(/<main[\s\S]*?<\/main>/)?.[0] || '';
    assert.match(music, /@click="organizeSlskd\(\)"/);
    assert.match(music, /@click="importBeets\(\)"/);
    assert.doesNotMatch(main, /@click="organizeSlskd\(\)"|aria-labelledby="beets-heading"/);
    assert.match(main, /@click="testSlskd\(\)"/);
    assert.match(main, /x-show="statusMsg"/);
});
