const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function popupApp() {
    const body = { style: { overflow: 'auto' } };
    const context = vm.createContext({
        localStorage: { getItem: () => null }, document: { body },
        clearInterval() {}, clearTimeout() {},
    });
    vm.runInContext(readFileSync(join(__dirname, '../js/script.js'), 'utf8'), context);
    const app = context.appData();
    let shows = 0, focuses = 0;
    app.$refs = {
        settingsDialog: {
            open: false,
            showModal() { this.open = true; shows++; },
            close() { this.open = false; },
        },
        settingsTrigger: { focus() { focuses++; } },
    };
    return { app, body, counts: () => ({ shows, focuses }) };
}

test('settings opens once, hides errors and locks page scrolling', () => {
    const { app, body, counts } = popupApp();
    app.errorsOpen = true;
    app.openSettings();
    app.openSettings();
    assert.equal(app.settingsOpen, true);
    assert.equal(app.$refs.settingsDialog.open, true);
    assert.equal(app.errorsOpen, false);
    assert.equal(body.style.overflow, 'hidden');
    assert.equal(counts().shows, 1);
});

test('closing restores scrolling and focus once and preserves preferences', () => {
    const { app, body, counts } = popupApp();
    app.openSettings();
    app.themeColor = '#123456';
    app.naviSync = true;
    app.closeSettings();
    app.settingsClosed(); // Native close event also handles Escape.
    app.settingsClosed();
    assert.equal(app.settingsOpen, false);
    assert.equal(app.$refs.settingsDialog.open, false);
    assert.equal(body.style.overflow, 'auto');
    assert.equal(counts().focuses, 1);
    assert.equal(app.themeColor, '#123456');
    assert.equal(app.naviSync, true);
    app.openSettings();
    assert.equal(counts().shows, 2);
});

test('settings cannot overlap documentation, normalizer or queue', () => {
    for (const other of ['documentationOpen', 'normalizerOpen', 'queueOpen']) {
        const { app, body, counts } = popupApp();
        app[other] = true;
        app.openSettings();
        assert.equal(counts().shows, 0);
        assert.equal(body.style.overflow, 'auto');
    }
    const { app, body } = popupApp();
    app.openSettings();
    app.openDocumentation();
    app.openNormalizer();
    assert.equal(app.documentationOpen, false);
    assert.equal(app.normalizerOpen, false);
    assert.equal(body.style.overflow, 'hidden');
});

test('destroy closes settings and restores scrolling without focusing removed controls', () => {
    const { app, body, counts } = popupApp();
    app.openSettings();
    app.destroy();
    app.settingsClosed();
    app.openSettings();
    assert.equal(app.settingsOpen, false);
    assert.equal(app.$refs.settingsDialog.open, false);
    assert.equal(body.style.overflow, 'auto');
    assert.deepEqual(counts(), { shows: 1, focuses: 0 });
});
