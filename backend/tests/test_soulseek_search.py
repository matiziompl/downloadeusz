import importlib
import json
import socket
from urllib.parse import urlparse, parse_qs

import pytest


SEARCH_ID = "1bb6c19c-42ee-4a5b-9f92-039b78de7b17"
TRANSFER_ID = "4ad4b3af-30ca-4e73-a448-ea1148698d8c"
TRACK = {"title": "Song", "artist": "Artist", "album": "Album", "length": 180,
         "source_url": "https://example.org/original"}


def module():
    try:
        return importlib.import_module("backend.soulseek_search")
    except ImportError:
        pytest.fail("Soulseek search_and_queue capability is missing")


class Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.value).encode()


def peer(username="peer", filename="Artist/Album/01 - Song.flac", length=180,
         free=True, speed=500000, queue=0):
    return {"username": username, "hasFreeUploadSlot": free, "uploadSpeed": speed,
            "queueLength": queue, "fileCount": 1, "lockedFileCount": 0, "lockedFiles": [],
            "files": [{"filename": filename, "size": 42000000, "length": length,
                       "extension": filename.rsplit(".", 1)[-1], "bitDepth": 16,
                       "sampleRate": 44100, "bitRate": 900, "isLocked": False}]}


def sequence(monkeypatch, responses):
    requests = []
    items = iter(responses)

    def urlopen(request, timeout):
        requests.append(request)
        assert 0 < timeout <= 10
        value = next(items)
        if isinstance(value, Exception):
            raise value
        return Response(value)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return requests


def search_responses(peers, enqueue=None):
    return [{"id": SEARCH_ID, "isComplete": False, "state": "InProgress", "responses": []},
            {"id": SEARCH_ID, "isComplete": True, "state": "Completed",
             "endedAt": "2026-11-07T12:00:05Z", "responses": peers}] + (
        [{"enqueued": [{"id": TRANSFER_ID, "username": "peer", "filename": "Artist/Album/01 - Song.flac",
                        "state": "Queued", "size": 42000000}], "failed": []} if enqueue is None else enqueue]
        if enqueue is not False else [])


def test_official_search_and_enqueue_contract_and_original_metadata(monkeypatch, tmp_path):
    requests = sequence(monkeypatch, search_responses([peer("peer/name")]))
    result = module().search_and_queue(TRACK, url="http://slskd:5030", api_key="key", download_dir=str(tmp_path))
    assert result["state"] == "queued"
    assert result["transfer_ids"] == [TRANSFER_ID]
    assert result["track"] == TRACK
    assert result["selected"]["username"] == "peer/name"
    assert result["selected"]["filename"] == "Artist/Album/01 - Song.flac"
    assert [urlparse(req.full_url).path for req in requests] == [
        "/api/v0/searches", f"/api/v0/searches/{SEARCH_ID}", "/api/v0/transfers/downloads/peer%2Fname"]
    assert requests[0].get_header("X-api-key") == "key"
    assert json.loads(requests[0].data)["searchText"] == "artist song"
    assert parse_qs(urlparse(requests[1].full_url).query) == {"includeResponses": ["true"]}
    assert json.loads(requests[2].data) == [{"filename": "Artist/Album/01 - Song.flac", "size": 42000000}]


def test_login_authentication_is_reused(monkeypatch):
    requests = sequence(monkeypatch, [{"token": "jwt"}] + search_responses([peer()]))
    assert module().search_and_queue(TRACK, url="slskd:5030", username="user", password="pass")["state"] == "queued"
    assert urlparse(requests[0].full_url).path == "/api/v0/session"
    assert requests[1].get_header("Authorization") == "Bearer jwt"


@pytest.mark.parametrize("candidate", [None, peer(filename="Artist/Album/01 - Song (Live).flac"),
    peer(filename="Other Artist/Album/01 - Song.flac"), peer(length=250),
    peer(filename="Artist/Album/01 - Song (Remix).flac")])
def test_missing_or_wrong_recording_is_not_downloaded(monkeypatch, candidate):
    requests = sequence(monkeypatch, search_responses([candidate] if candidate else [], enqueue=False) * 2)
    result = module().search_and_queue(TRACK, url="http://slskd")
    assert result["state"] == "not_found"
    assert result["transfer_ids"] == []
    assert len(requests) == 4


def test_identity_and_duration_rank_before_quality_then_peer_quality(monkeypatch):
    candidates = [peer("wrong", length=250), peer("mp3", filename="Artist/Album/01 - Song.mp3"),
                  peer("busy", free=False, speed=9999999, queue=400), peer("fast", speed=800000)]
    requests = sequence(monkeypatch, search_responses(candidates))
    result = module().search_and_queue(TRACK, url="http://slskd")
    assert result["selected"]["username"] == "fast"
    assert requests[-1].full_url.endswith("/fast")


def test_retry_excludes_previous_peer(monkeypatch):
    requests = sequence(monkeypatch, search_responses([peer("failed", speed=999999), peer("alternate")]))
    result = module().search_and_queue({**TRACK, "excluded_usernames": ["failed"]}, url="http://slskd")
    assert result["selected"]["username"] == "alternate"
    assert requests[-1].full_url.endswith("/alternate")


def test_unknown_duration_cannot_choose_between_different_recordings(monkeypatch):
    requests = sequence(monkeypatch, search_responses([peer("one", length=180), peer("two", length=250)], enqueue=False) * 2)
    result = module().search_and_queue({**TRACK, "length": None}, url="http://slskd")
    assert result["state"] == "needs_review"
    assert len(requests) == 4


def test_missing_candidate_duration_requires_review(monkeypatch):
    requests = sequence(monkeypatch, search_responses([peer(length=None)], enqueue=False) * 2)
    result = module().search_and_queue(TRACK, url="http://slskd")
    assert result["state"] == "needs_review"
    assert len(requests) == 4


def test_network_timeout_is_reviewable_and_retryable(monkeypatch):
    sequence(monkeypatch, [socket.timeout("timed out")])
    result = module().search_and_queue(TRACK, url="http://slskd")
    assert result["state"] == "needs_review"
    assert result["retryable"] is True
    assert result["transfer_ids"] == []


def test_enqueue_timeout_requires_transfer_inspection_before_retry(monkeypatch):
    sequence(monkeypatch, search_responses([peer()], enqueue=False) + [socket.timeout("timed out")])
    result = module().search_and_queue(TRACK, url="http://slskd")
    assert result["state"] == "needs_review"
    assert result["retryable"] is False
    assert result["selected"]["username"] == "peer"


def test_search_polling_has_a_hard_bound(monkeypatch):
    pending = {"id": SEARCH_ID, "isComplete": False, "state": "InProgress", "responses": []}
    requests = sequence(monkeypatch, [pending] * 100)
    search_module = module()
    monkeypatch.setattr(search_module.time, "sleep", lambda seconds: None)
    result = search_module.search_and_queue(TRACK, url="http://slskd")
    assert result["state"] == "needs_review"
    assert result["retryable"] is True
    assert 2 <= len(requests) <= 65


@pytest.mark.parametrize("filename", ["../Artist/Album/Song.flac", "https://evil/Artist/Song.flac", "Artist/Album/../Song.flac"])
def test_remote_paths_cannot_supply_urls_or_traversal(monkeypatch, filename):
    requests = sequence(monkeypatch, search_responses([peer(filename=filename)], enqueue=False) * 2)
    assert module().search_and_queue(TRACK, url="http://slskd")["state"] == "not_found"
    assert len(requests) == 4


def test_rejected_enqueue_does_not_report_success(monkeypatch):
    sequence(monkeypatch, search_responses([peer()], enqueue={"enqueued": [], "failed": [{"filename": "Artist/Album/01 - Song.flac", "message": "offline"}]}))
    result = module().search_and_queue(TRACK, url="http://slskd")
    assert result["state"] == "needs_review"
    assert result["retryable"] is True
    assert result["transfer_ids"] == []


def test_progressive_search_cleans_upload_title_and_rejects_wrong_artist(monkeypatch):
    track = {**TRACK, "artist": "Playboi Carti - Topic",
             "title": "Playboi Carti COCAINE NOSE Official Audio"}
    responses = search_responses([peer(filename="Other Carti/Album/COCAINE NOSE.flac")], enqueue=False)
    responses += search_responses([peer(filename="Playboi Carti/Album/COCAINE NOSE.flac")])
    requests = sequence(monkeypatch, responses)
    progress = []
    result = module().search_and_queue(track, url="http://slskd", progress_callback=progress.append)
    assert result["state"] == "queued"
    assert result["track"] == track
    queries = [json.loads(request.data)["searchText"] for request in requests if request.full_url.endswith("/searches")]
    assert queries == ["playboi carti cocaine nose", "carti cocaine nose"]
    assert progress == [{"query": queries[0], "attempt": 1, "total": 3},
                        {"query": queries[1], "attempt": 2, "total": 3}]


def test_title_only_fallback_requires_full_artist_and_preserves_version(monkeypatch):
    responses = search_responses([], enqueue=False) * 2
    responses += search_responses([peer(filename="Artist/Album/Song (Acoustic).flac"),
                                   peer(filename="Other Artist/Album/Song.flac")], enqueue=False)
    requests = sequence(monkeypatch, responses)
    result = module().search_and_queue({**TRACK, "artist": "Full Artist"}, url="http://slskd")
    assert result["state"] == "not_found"
    assert [json.loads(request.data)["searchText"] for request in requests if request.full_url.endswith("/searches")] == [
        "full artist song", "artist song", "song"]


@pytest.mark.parametrize("suffix", ["(Edit)", "- Edit", "(Live)", "- Instrumental", "(Demo)", "(Acoustic)"])
def test_version_qualifier_is_not_erased_by_title_comparison(monkeypatch, suffix):
    responses = search_responses([peer(filename=f"Artist/Album/Song {suffix}.flac")], enqueue=False) * 2
    sequence(monkeypatch, responses)
    assert module().search_and_queue(TRACK, url="http://slskd")["state"] == "not_found"


def test_shared_search_budget_stops_before_next_query_and_is_retryable(monkeypatch):
    search_module = module()
    current = [0]
    monkeypatch.setattr(search_module.time, "monotonic", lambda: current[0])
    requests = []

    def urlopen(request, timeout):
        requests.append(request)
        if request.data:
            return Response({"id": SEARCH_ID})
        current[0] = 61
        return Response({"id": SEARCH_ID, "isComplete": True, "state": "Completed",
                         "endedAt": "2026-11-07T12:01:01Z", "responses": []})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    result = search_module.search_and_queue(TRACK, url="http://slskd")
    assert result["state"] == "needs_review"
    assert result["retryable"] is True
    assert len(requests) == 2


def test_long_title_fallback_remains_bounded_unique_and_without_album_or_format(monkeypatch):
    responses = search_responses([], enqueue=False) * 4
    requests = sequence(monkeypatch, responses)
    result = module().search_and_queue({**TRACK, "artist": "Full Artist",
        "title": "The Incredibly Long Song About Something (Live)", "album": "Secret Album"}, url="http://slskd")
    assert result["state"] == "not_found"
    queries = [json.loads(request.data)["searchText"] for request in requests if request.full_url.endswith("/searches")]
    assert len(queries) == len(set(queries)) == 4
    assert queries[:3] == ["full artist the incredibly long song about something live",
                          "artist the incredibly long song about something live",
                          "the incredibly long song about something live"]
    assert "live" in queries[-1]
    assert all("secret album" not in query and "flac" not in query for query in queries)


def test_missing_duration_keeps_searching_and_selected_search_id_matches_source(monkeypatch):
    second_id = "26d12d09-3212-411e-b574-aa9a9ea1b462"
    first = search_responses([peer("incomplete", length=None)], enqueue=False)
    second = search_responses([peer("complete")])
    second[0]["id"] = second_id
    second[1]["id"] = second_id
    requests = sequence(monkeypatch, first + second)
    result = module().search_and_queue(TRACK, url="http://slskd")
    assert result["state"] == "queued"
    assert result["selected"]["username"] == "complete"
    assert result["selected"]["search_id"] == second_id
    assert len(requests) == 5


def test_same_candidate_duration_can_be_completed_by_later_query(monkeypatch):
    responses = search_responses([peer(length=None)], enqueue=False) + search_responses([peer(length=180)])
    sequence(monkeypatch, responses)
    result = module().search_and_queue({**TRACK, "length": None}, url="http://slskd")
    assert result["state"] == "queued"
    assert result["selected"]["length"] == 180


@pytest.mark.parametrize(("source_title", "filename"), [
    ("Song (Remastered 2011)", "Artist/Album/Song.flac"),
    ("Song", "Artist/Album/Song - 2011 Remaster.flac"),
    ("Song (Deluxe Edition)", "Artist/Album/Song [Remastered 2020].flac"),
    ("Song [25th Anniversary Edition]", "Artist/Album/Song.flac"),
])
def test_search_matches_recording_across_edition_labels(monkeypatch, source_title, filename):
    requests = sequence(monkeypatch, search_responses([peer(filename=filename)]))
    track = {**TRACK, "title": source_title}
    result = module().search_and_queue(track, url="http://slskd")
    assert result["state"] == "queued"
    assert json.loads(requests[0].data)["searchText"] == "artist song"
    assert result["track"] == track


def test_search_timeout_uses_milliseconds_so_a_one_second_peer_can_respond(monkeypatch):
    """slskd passes searchTimeout straight to Soulseek.NET's millisecond timeout."""
    search_module = module()
    clock = [0.0]
    started = [0.0]
    idle_timeout = [0.0]
    posted_timeouts = []
    monkeypatch.setattr(search_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(search_module.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def urlopen(request, timeout):
        path = urlparse(request.full_url).path
        if path.endswith("/searches"):
            payload = json.loads(request.data)
            posted_timeouts.append(payload["searchTimeout"])
            idle_timeout[0] = payload["searchTimeout"] / 1000
            started[0] = clock[0]
            return Response({"id": SEARCH_ID})
        if "/transfers/downloads/" in path:
            return Response({"enqueued": [{"id": TRANSFER_ID}], "failed": []})
        elapsed = clock[0] - started[0]
        complete = elapsed >= idle_timeout[0]
        # A peer responding after one second cannot reach a search closed at 5 ms.
        responses = [peer()] if elapsed >= 1 and idle_timeout[0] >= 1 else []
        return Response({"id": SEARCH_ID, "state": "CompletedTimedOut" if complete else "InProgress",
                         "isComplete": complete, "endedAt": "2026-11-07T12:00:05Z" if complete else None,
                         "responses": responses})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    result = search_module.search_and_queue(TRACK, url="http://slskd", api_key="key")
    assert result["state"] == "queued"
    assert result["transfer_ids"] == [TRANSFER_ID]
    assert posted_timeouts == [5000]


@pytest.mark.parametrize("omit_ended_at", [False, True], ids=["null-endedAt", "omitted-endedAt"])
def test_complete_flag_waits_for_final_save_after_more_than_twelve_polls(monkeypatch, omit_ended_at):
    """SearchService publishes Completed before its final EndedAt/responses save."""
    search_module = module()
    clock = [0.0]
    polls = []
    monkeypatch.setattr(search_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(search_module.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def urlopen(request, timeout):
        path = urlparse(request.full_url).path
        if path.endswith("/searches"):
            return Response({"id": SEARCH_ID})
        if "/transfers/downloads/" in path:
            return Response({"enqueued": [{"id": TRANSFER_ID}], "failed": []})
        polls.append(clock[0])
        finalized = len(polls) >= 16
        search = {"id": SEARCH_ID, "state": "CompletedTimedOut", "isComplete": True,
                  "responses": [peer()] if finalized else []}
        if finalized:
            search["endedAt"] = "2026-11-07T12:00:15Z"
        elif not omit_ended_at:
            search["endedAt"] = None
        return Response(search)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    result = search_module.search_and_queue(TRACK, url="http://slskd", api_key="key")
    assert result["state"] == "queued"
    assert len(polls) == 16
    assert 12 < clock[0] < 60


@pytest.mark.parametrize("state", ["Completed, Cancelled", "Completed, Errored"])
def test_aborted_search_is_retryable_instead_of_definitive_not_found(monkeypatch, state):
    requests = sequence(monkeypatch, [
        {"id": SEARCH_ID},
        {"id": SEARCH_ID, "isComplete": True, "state": state,
         "endedAt": "2026-11-07T12:00:05Z", "responses": []},
    ] * 2)
    result = module().search_and_queue(TRACK, url="http://slskd")
    assert result["state"] == "needs_review"
    assert result["retryable"] is True
    assert result["transfer_ids"] == []
    assert len(requests) == 2


def test_final_timed_out_search_is_not_found_only_after_all_queries(monkeypatch):
    complete = {"id": SEARCH_ID, "isComplete": True, "state": "CompletedTimedOut",
                "endedAt": "2026-11-07T12:00:05Z", "responses": []}
    requests = sequence(monkeypatch, [{"id": SEARCH_ID}, complete] * 2)
    result = module().search_and_queue(TRACK, url="http://slskd")
    assert result["state"] == "not_found"
    assert result["retryable"] is False
    assert result["transfer_ids"] == []
    assert len(requests) == 4
    assert "odpowiedzi: 0, plików: 0" in result["message"]


def test_rejected_files_are_reported_separately_from_no_peer_responses(monkeypatch):
    requests = sequence(monkeypatch, search_responses([
        peer(filename="Different Artist/Album/Song.flac")
    ], enqueue=False) * 2)
    result = module().search_and_queue(TRACK, url="http://slskd")
    assert result["state"] == "not_found"
    assert len(requests) == 4
    assert "Brak plików zgodnych" in result["message"]
    assert "odpowiedzi: 2, plików: 2" in result["message"]


@pytest.mark.parametrize("version", ["Live", "Remix", "Acoustic", "Instrumental", "Demo", "Edit"])
def test_remaster_cleanup_keeps_recording_version_in_queries_and_filtering(monkeypatch, version):
    responses = search_responses([peer(filename="Artist/Album/Song.flac")], enqueue=False)
    responses += search_responses([peer(filename=f"Artist/Album/Song ({version}).flac")])
    requests = sequence(monkeypatch, responses)
    result = module().search_and_queue({**TRACK, "title": f"Song ({version}) (Remastered 2011)"}, url="http://slskd")
    assert result["state"] == "queued"
    assert result["selected"]["filename"] == f"Artist/Album/Song ({version}).flac"
    assert [json.loads(request.data)["searchText"] for request in requests if request.full_url.endswith("/searches")] == [
        f"artist song {version.lower()}", f"song {version.lower()}"]


def test_lowercase_search_reaches_title_only_without_changing_original_metadata(monkeypatch):
    track = {**TRACK, "artist": "Playboi Carti", "title": "COCAINE NOSE", "album": "MUSIC"}
    original = dict(track)
    responses = search_responses([], enqueue=False) * 2
    responses += search_responses([peer(filename="Playboi Carti/MUSIC/COCAINE NOSE.flac")])
    requests = sequence(monkeypatch, responses)
    progress = []
    result = module().search_and_queue(track, url="http://slskd", progress_callback=progress.append)
    queries = [json.loads(request.data)["searchText"] for request in requests if request.full_url.endswith("/searches")]
    assert queries == ["playboi carti cocaine nose", "carti cocaine nose", "cocaine nose"]
    assert [step["query"] for step in progress] == queries
    assert result["state"] == "queued"
    assert result["track"] == original == track
    assert result["selected"]["filename"] == "Playboi Carti/MUSIC/COCAINE NOSE.flac"
