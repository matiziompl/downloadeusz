const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function createApp() {
    const context = vm.createContext({ localStorage: { getItem: () => null }, URLSearchParams });
    vm.runInContext(readFileSync(join(__dirname, '../js/script.js'), 'utf8'), context);
    return context.appData();
}

test('search opens recordings filtered by title and artist, without the fallback album', () => {
    const app = createApp();
    app.musicSelectedTrack = { title: ' Careless Whisper ', artist: ' George Michael ', album: 'Careless Whisper' };
    const url = new URL(app.musicBrainzSearchUrl());
    assert.equal(url.origin, 'https://musicbrainz.org');
    assert.equal(url.pathname, '/search');
    assert.equal(url.searchParams.get('type'), 'recording');
    assert.equal(url.searchParams.get('method'), 'advanced');
    assert.equal(url.searchParams.get('query'), 'recording:"Careless Whisper" AND artist:"George Michael"');
});

test('title-only search and missing artist both omit the artist restriction', () => {
    const app = createApp();
    app.musicSelectedTrack = { title: 'Song (Live Remix)', artist: 'Artist' };
    assert.equal(new URL(app.musicBrainzSearchUrl(true)).searchParams.get('query'), 'recording:"Song \\(Live Remix\\)"');
    app.musicSelectedTrack.artist = '';
    assert.equal(app.musicBrainzSearchUrl(), app.musicBrainzSearchUrl(true));
});

test('Unicode and Lucene punctuation remain literal and cannot inject URL parameters', () => {
    const app = createApp();
    app.musicSelectedTrack = { title: 'Żółć "A" \\ B & type=artist', artist: 'AC/DC + Björk' };
    const url = new URL(app.musicBrainzSearchUrl());
    assert.equal(url.searchParams.get('query'), 'recording:"Żółć \\"A\\" \\\\ B \\& type=artist" AND artist:"AC\\/DC \\+ Björk"');
    assert.equal(url.searchParams.getAll('type').length, 1);
    assert.equal(url.searchParams.get('type'), 'recording');
    assert.equal(url.hash, '');
});

test('search tracks the selected song and is unavailable without a title', () => {
    const app = createApp();
    assert.equal(app.musicBrainzSearchUrl(), '');
    app.musicSelectedTrack = { title: 'First', artist: 'Artist' };
    const first = app.musicBrainzSearchUrl();
    app.musicSelectedTrack = { title: 'Second', artist: 'Other' };
    assert.notEqual(app.musicBrainzSearchUrl(), first);
    app.musicSelectedTrack.title = '   ';
    assert.equal(app.musicBrainzSearchUrl(), '');
});

test('both MusicBrainz search links are usable anchors opening a separate tab', () => {
    const html = readFileSync(join(__dirname, '../index.html'), 'utf8');
    const links = [...html.matchAll(/<a\b[^>]*:href="musicBrainzSearchUrl\((?:true)?\)"[^>]*>[\s\S]*?<\/a>/g)].map(match => match[0]);
    assert.equal(links.length, 2);
    for (const link of links) {
        assert.match(link, /target="_blank"/);
        assert.match(link, /rel="noopener noreferrer"/);
    }
});
