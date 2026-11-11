const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function createApp(saved) {
    const storage = new Map(saved === undefined ? [] : [['downloadeusz_settings', JSON.stringify(saved)]]);
    const context = vm.createContext({
        localStorage: {
            getItem: key => storage.get(key) ?? null,
            setItem: (key, value) => storage.set(key, value),
            removeItem: key => storage.delete(key),
        },
        setInterval() {}, document: { hidden: false },
    });
    vm.runInContext(readFileSync(join(__dirname, '../js/script.js'), 'utf8'), context);
    return { app: context.appData(), storage };
}

test('widget dock stays disabled for new users and existing saved preferences', () => {
    const fresh = createApp().app;
    assert.equal(fresh.widgetsDocked, false);
    fresh.loadSettings();
    assert.equal(fresh.widgetsDocked, false);

    const existing = createApp({ themeColor: '#123456', showTitle: false }).app;
    existing.loadSettings();
    assert.equal(existing.widgetsDocked, false);
    assert.equal(existing.themeColor, '#123456');
    assert.equal(existing.showTitle, false);
});

test('only a saved boolean true enables the widget dock', () => {
    for (const value of [true, false, null, 1, 0, 'true', 'false', [], {}]) {
        const { app } = createApp({ widgetsDocked: value });
        app.loadSettings();
        assert.equal(app.widgetsDocked, value === true, `saved value: ${JSON.stringify(value)}`);
    }
});

test('widget dock can be enabled and disabled across reloads without resetting appearance', () => {
    const { app, storage } = createApp({ themeColor: '#123456', showCommandPreview: false });
    app.loadSettings();
    for (const enabled of [true, false]) {
        app.widgetsDocked = enabled;
        app.saveSettings();
        const saved = JSON.parse(storage.get('downloadeusz_settings'));
        assert.equal(saved.widgetsDocked, enabled);
        const restored = createApp(saved).app;
        restored.loadSettings();
        assert.equal(restored.widgetsDocked, enabled);
        assert.equal(restored.themeColor, '#123456');
        assert.equal(restored.showCommandPreview, false);
    }
});

test('the widget dock preference is saved immediately by the registered settings watcher', () => {
    const { app, storage } = createApp();
    const watchers = new Map();
    app.$watch = (key, callback) => watchers.set(key, callback);
    app.$nextTick = callback => callback();
    for (const method of ['fetchVersion', 'fetchAccounts', 'fetchWallpapers', 'fetchErrors', 'fetchBeetsStatus', 'fetchJobs', 'fetchNormalizer', 'updateExtractedColor']) {
        app[method] = () => {};
    }
    app.initApp();
    assert.equal(typeof watchers.get('widgetsDocked'), 'function');
    for (const enabled of [true, false]) {
        app.widgetsDocked = enabled;
        watchers.get('widgetsDocked')();
        assert.equal(JSON.parse(storage.get('downloadeusz_settings')).widgetsDocked, enabled);
    }
});

test('all widget triggers share one dock and retain their unique dialog references', () => {
    const html = readFileSync(join(__dirname, '../index.html'), 'utf8');
    const wrapper = html.match(/<nav\b[^>]*class="[^"]*\bwidget-launchers\b[^"]*"[^>]*>([\s\S]*?)<\/nav>/);
    assert.ok(wrapper, 'widget dock navigation exists');
    for (const name of ['documentation', 'normalizer', 'settings', 'queue', 'music']) {
        const ref = new RegExp(`x-ref="${name}Trigger"`, 'g');
        assert.equal([...html.matchAll(ref)].length, 1, `${name} button is not duplicated`);
        assert.match(wrapper[1], ref, `${name} button belongs to the dock`);
        assert.match(wrapper[1], new RegExp(`aria-controls="${name}-dialog"`));
    }
    assert.match(wrapper[1], /class="[^"]*\berrors-trigger\b/);
    assert.match(wrapper[1], /x-show="errorsList\.length > 0 && !errorsOpen"/);
    assert.match(html, /<input\b[^>]*type="checkbox"[^>]*x-model="widgetsDocked"/);
});
