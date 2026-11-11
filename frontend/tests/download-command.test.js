const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

test('copied yt-dlp command uses the same bounded output names for audio and video', () => {
    const context = vm.createContext({ localStorage: { getItem: () => null } });
    vm.runInContext(readFileSync(join(__dirname, '../js/script.js'), 'utf8'), context);
    const app = context.appData();
    app.selectedTool = 'ytdl';
    app.url = 'https://www.youtube.com/watch?v=abcdefghijk';
    for (const audioOnly of [true, false]) {
        app.flags.audio_only = audioOnly;
        const command = app.getCommandPreview();
        assert.ok(command.includes('--output "%(title).60B [%(id)s].%(ext)s"'));
        assert.equal(command.includes('--audio-format mp3'), audioOnly);
    }
    app.selectedTool = 'spotdl';
    assert.ok(app.getCommandPreview().includes('--output "{artist}/{year} - {album}/{track-number} {title}.{ext}"'));
});
