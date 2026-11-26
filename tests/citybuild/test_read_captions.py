"""``city check`` reads the newest clip's or show's captions, and says so when there are none."""

from datetime import date

from civiccast_brief.citybuild.read import read_latest
from civiccast_brief.meeting import cablecast, granicus
from civiccast_brief.sources import make_source

TODAY = date(2026, 11, 10)
HOST = "goldenco.granicus.com"

CUE = "00:00:01.000 --> 00:00:04.000\nGood evening, everyone.\n\n"
CUE2 = "00:00:04.000 --> 00:00:07.000\nThe meeting is called to order.\n\n"


def _granicus_clip(clip_id=950, when="November 4, 2026"):
    return granicus.Clip(HOST, 2, clip_id, "City Council", when)


def _granicus_source():
    return make_source("granicus", {"host": HOST, "view_id": 2, "bodies": ["City Council"]})


def test_granicus_with_captions_reads_the_newest_clip(web, monkeypatch):
    monkeypatch.setattr(granicus, "list_clips", lambda h, v: [_granicus_clip()])
    monkeypatch.setattr(granicus, "_fetch", lambda url: "WEBVTT\n\n" + CUE + CUE2)
    proof = read_latest(_granicus_source(), "City Council", web.fetcher(), today=TODAY)
    assert proof.ok, proof.problem
    assert proof.what == "video clip with captions"
    assert proof.lines == [
        "2 caption lines",
        "Good evening, everyone.",
        "The meeting is called to order.",
    ]


def test_granicus_with_an_empty_caption_file_is_read_as_transcribable(web, monkeypatch):
    monkeypatch.setattr(granicus, "list_clips", lambda h, v: [_granicus_clip()])
    monkeypatch.setattr(granicus, "_fetch", lambda url: "WEBVTT\n")
    proof = read_latest(_granicus_source(), "City Council", web.fetcher(), today=TODAY)
    assert proof.ok, proof.problem
    assert proof.what == "video clip with no captions; it is transcribed on this computer"
    assert proof.lines == [
        (
            "no captions on Granicus; the audio is transcribed on this computer when the "
            "meeting is written"
        )
    ]


def test_granicus_caption_fetch_that_fails_is_a_problem(web, monkeypatch):
    monkeypatch.setattr(granicus, "list_clips", lambda h, v: [_granicus_clip()])

    def broken(url):
        raise granicus.GranicusError("could not fetch the caption file: HTTP Error 503")

    monkeypatch.setattr(granicus, "_fetch", broken)
    proof = read_latest(_granicus_source(), "City Council", web.fetcher(), today=TODAY)
    assert not proof.ok
    assert proof.problem.startswith("could not read the captions:")


CABLE_HOST = "reflect-cityofloveland-co.cablecast.tv"


def _cablecast_show(show_id=746):
    return cablecast.Show(
        CABLE_HOST, show_id, 530, "City Council Meeting November 4, 2026", "November 4, 2026"
    )


def _cablecast_source():
    return make_source("cablecast", {"host": CABLE_HOST, "bodies": ["City Council"]})


def test_cablecast_with_captions_reads_the_newest_show(web, monkeypatch):
    monkeypatch.setattr(cablecast, "list_shows", lambda host: [_cablecast_show()])
    monkeypatch.setattr(cablecast, "find_show", lambda host, show_id: _cablecast_show())
    monkeypatch.setattr(
        cablecast,
        "fetch_captions",
        lambda show: [
            {"start_ms": 1000, "end_ms": 4000, "text": "Good evening, everyone."},
            {"start_ms": 4000, "end_ms": 7000, "text": "The meeting is called to order."},
        ],
    )
    proof = read_latest(_cablecast_source(), "City Council", web.fetcher(), today=TODAY)
    assert proof.ok, proof.problem
    assert proof.what == "video show with captions"
    assert proof.lines == [
        "2 caption lines",
        "Good evening, everyone.",
        "The meeting is called to order.",
    ]


def test_cablecast_with_no_captions_is_read_as_transcribable(web, monkeypatch):
    monkeypatch.setattr(cablecast, "list_shows", lambda host: [_cablecast_show()])
    monkeypatch.setattr(cablecast, "find_show", lambda host, show_id: _cablecast_show())

    def no_captions(show):
        raise cablecast.CablecastError(
            f"show {show.show_id} on {show.host} has no caption lines; "
            "the audio is transcribed on this computer"
        )

    monkeypatch.setattr(cablecast, "fetch_captions", no_captions)
    proof = read_latest(_cablecast_source(), "City Council", web.fetcher(), today=TODAY)
    assert proof.ok, proof.problem
    assert proof.what == "video show with no captions; it is transcribed on this computer"
    assert proof.lines == [
        (
            "no captions on Cablecast; the audio is transcribed on this computer when the "
            "meeting is written"
        )
    ]
