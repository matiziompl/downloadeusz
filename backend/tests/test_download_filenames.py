"""Exercise actual yt-dlp filename formatting without contacting a source."""

import errno
from types import SimpleNamespace

import pytest
from yt_dlp import YoutubeDL

from backend import main


LONG_TITLE = (
    "𝙍𝙤𝙧𝙮 𝙞𝙣 𝙚𝙖𝙧𝙡𝙮 20𝙨 - 𝙩𝙝𝙚 𝙗𝙡𝙖𝙘𝙠 𝙝𝙤𝙡𝙚 𝙞𝙨 𝙩𝙤𝙤 𝙘𝙡𝙤𝙨𝙚! "
    "(𝙎𝙡𝙤𝙬𝙚𝙙 + 𝘽𝙚𝙨𝙩 𝙋𝙖𝙧𝙩 𝙇𝙤𝙤𝙥𝙚𝙙) | 𝙏𝙞𝙠𝙏𝙤𝙠 𝙑𝙚𝙧𝙨𝙞𝙤𝙣"
)


def filename(command, title=LONG_TITLE, identity="01234567890"):
    options = {"quiet": True}
    if "--output" in command:
        options["outtmpl"] = command[command.index("--output") + 1]
    with YoutubeDL(options) as downloader:
        metadata = {"title": title, "id": identity, "ext": "webm"}
        result = downloader.prepare_filename(metadata)
        assert metadata["title"] == title
        return result


@pytest.fixture
def download_command(tmp_path, monkeypatch):
    for setting in ("SPOTDL_CONFIG_DIR", "DOWNLOADS_DIR", "LOGS_DIR"):
        directory = tmp_path / setting
        directory.mkdir()
        monkeypatch.setattr(main, setting, str(directory))
    monkeypatch.setattr(main, "BEETS_ENABLED", False)
    monkeypatch.setattr(main.accounts, "get_navidrome_account_by_id", lambda *_: None)
    commands = []

    def popen(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(stdout=iter(["ERROR: source intentionally not contacted\n"]),
                               returncode=1, wait=lambda: 1)

    monkeypatch.setattr(main.subprocess, "Popen", popen)

    def run(audio_only=True):
        main.run_download_task("filename-test", main.DownloadRequest(
            tool="ytdl", url="https://www.youtube.com/watch?v=01234567890",
            flags={"audio_only": audio_only},
        ))
        return commands[-1]

    return run


def test_reported_title_exceeds_default_filename_byte_limit(tmp_path):
    raw = filename([])
    assert len(raw.encode("utf-8")) > 255
    with pytest.raises(OSError) as error:
        (tmp_path / raw).write_bytes(b"audio")
    assert error.value.errno == errno.ENAMETOOLONG


@pytest.mark.parametrize("audio_only", [True, False])
@pytest.mark.parametrize("title", [LONG_TITLE, "/|<:>" * 200, "🎼" * 200],
                         ids=["reported-title", "sanitization-expansion", "emoji"])
def test_downloader_names_allow_unicode_and_temporary_suffixes(download_command, tmp_path, audio_only, title):
    command = download_command(audio_only)
    name = filename(command, title)
    assert "01234567890" in name
    for suffix in ("", ".part", ".f251.webm.part-Frag99999", ".temp.mp3"):
        output = tmp_path / (name + suffix)
        output.write_bytes(b"audio")
        assert len(output.name.encode("utf-8")) <= 255
    if audio_only:
        assert "--embed-metadata" in command


def test_shortened_equal_prefixes_keep_distinct_source_ids(download_command, tmp_path):
    command = download_command()
    first = filename(command, LONG_TITLE + " first", "01234567890")
    second = filename(command, LONG_TITLE + " second", "01234567891")
    assert first != second
    (tmp_path / first).write_bytes(b"first recording")
    (tmp_path / second).write_bytes(b"second recording")
    assert (tmp_path / first).read_bytes() == b"first recording"
