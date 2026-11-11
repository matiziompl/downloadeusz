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
    const app = context.appData();
    app.beetsStatus = { enabled: true, available: true, running: false };
    app.fetchJobs = () => {};
    app.fetchBeetsStatus = () => {};
    app.connectLogs = () => {};
    return { app, context };
}

const key = 'a'.repeat(64);
const candidate = { selection_key: key, kind: 'recording', id: '12345678-1234-1234-1234-123456789abc',
    artist: 'Lady Pank', title: 'Mniej niż zero', length: 240 };
const item = { id: 'b'.repeat(64), candidates: [candidate] };

test('details distinguish recording from release and only form trusted MusicBrainz links', () => {
    const { app } = createApp();
    assert.equal(app.beetsCandidateUrl(candidate), `https://musicbrainz.org/recording/${candidate.id}`);
    assert.equal(app.beetsCandidateUrl({ ...candidate, kind: 'release' }), `https://musicbrainz.org/release/${candidate.id}`);
    assert.equal(app.beetsCandidateUrl({ ...candidate, id: 'javascript:alert(1)' }), '');
    assert.equal(app.beetsCandidateUrl({ ...candidate, kind: 'unknown' }), '');
    const details = app.beetsCandidateDetails({ ...candidate, country: 'PL', disambiguation: '<script>' });
    assert.ok(details.some(([label, value]) => label === 'Długość' && value === '4:00'));
    assert.ok(details.some(([label, value]) => label === 'Dopisek wersji' && value === '<script>'));
    assert.ok(!app.beetsCandidateDetails({ length: Infinity }).some(([label]) => label === 'Długość'));
});

test('legacy proposals cannot be selected and stale selection disappears on refresh', async () => {
    const { app, context } = createApp();
    app.beetsReviewItems = [item];
    app.beetsCandidateSelections[item.id] = key;
    assert.equal(app.beetsSelectedCandidate(item).selection_key, key);
    assert.equal(app.beetsSelectedCandidate({ ...item, candidates: [{ ...candidate, selection_key: undefined }] }), null);
    context.fetch = async () => ({ ok: true, json: async () => ({ items: [{ ...item, candidates: [] }] }) });
    await app.fetchBeetsReview();
    assert.equal(app.beetsCandidateSelections[item.id], undefined);
});

test('selection posts only the chosen saved key and prevents duplicate submission', async () => {
    const { app, context } = createApp();
    app.beetsReviewItems = [item];
    app.beetsCandidateSelections[item.id] = key;
    app.naviSync = true;
    app.selectedNaviAccount = 'listener';
    let finish;
    const calls = [];
    context.fetch = async (url, options) => {
        calls.push({ url, body: JSON.parse(options.body) });
        await new Promise(resolve => { finish = resolve; });
        return { ok: true, json: async () => ({ task_id: 'selection-task', message: 'Wybrano propozycję' }) };
    };
    const pending = app.importBeets(false, item.id, key);
    await app.importBeets(false, item.id, key);
    finish();
    await pending;
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, `/api/beets/review/${item.id}/select`);
    assert.deepEqual(calls[0].body, { candidate_key: key, navi_sync: true, navi_account_id: 'listener' });
    assert.equal(app.beetsCandidateSelections[item.id], undefined);
    assert.equal(app.importingBeets, false);
    assert.equal(app.beetsImportError, '');
});

test('unknown selection is not posted and failed selection retains the choice for retry', async () => {
    const { app, context } = createApp();
    app.beetsReviewItems = [item];
    app.beetsCandidateSelections[item.id] = key;
    let calls = 0;
    context.fetch = async () => {
        calls++;
        return { ok: false, json: async () => ({ detail: 'Propozycja jest nieaktualna. Sprawdź ponownie.' }) };
    };
    await app.importBeets(false, item.id, 'c'.repeat(64));
    assert.equal(calls, 0);
    await app.importBeets(false, item.id, key);
    assert.equal(app.beetsCandidateSelections[item.id], key);
    assert.match(app.beetsImportError, /nieaktualna/);
    assert.equal(app.importingBeets, false);
});

test('candidate markup uses native selection and text binding for untrusted labels', () => {
    const html = readFileSync(join(__dirname, '../index.html'), 'utf8');
    const panel = html.slice(html.indexOf('Propozycje do porównania'), html.indexOf('Pokaż kolejne 20 plików'));
    assert.match(panel, /type="radio"/);
    assert.match(panel, /<fieldset/);
    assert.match(panel, /<legend/);
    assert.match(panel, /rel="noopener noreferrer"/);
    assert.match(panel, /x-text="detail\[1\]"/);
    assert.doesNotMatch(panel, /x-html/);
});

test('choice and legacy recovery remain visible when candidate details are collapsed', () => {
    const html = readFileSync(join(__dirname, '../index.html'), 'utf8');
    const start = html.indexOf('<template x-for="(candidate, index)');
    const panel = html.slice(start, html.indexOf('`beets-choice-help-${item.id}`', start));
    const collapsed = panel.slice(0, panel.indexOf('<details>'));
    assert.match(collapsed, /type="radio"/);
    assert.match(collapsed, /x-text="beetsCandidateText\(candidate\)"/);
    assert.match(collapsed, /Ta propozycja ma tylko skrót danych/);
    assert.match(panel, /<summary[^>]*>Szczegóły propozycji<\/summary>/);
    assert.doesNotMatch(panel.slice(panel.indexOf('<details>')), /type="radio"/);
});
