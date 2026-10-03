"""Сессия Audition, собранная нами: треки по ролям, клипы по репликам.

Ради этого всё и затевалось. В «Байке 65» — 825 клипов на 32 трека, из них 247 на
одном Дегатти: это руками нарезанная сплошная запись роли. Выравнивание знает границы
каждой реплики, а `.sesx` умеет ставить их на места.

Формат разобран по настоящей сессии Audition 22.5. Три вещи, в которых легко ошибиться:
время считается в сэмплах, а не в секундах; у клипа задаются и место на таймлайне
(`startPoint`/`endPoint`), и вырезаемый кусок исходника (`sourceInPoint`/`sourceOutPoint`);
файл подключается таблицей и упоминается в клипе номером.

Чего мы нарочно не пишем: `xmpMetadata` (сто килобайт служебных данных Adobe),
`properties` с пресетами и `audioDevice` — там идентификаторы звуковой карты владельца,
и на другой машине они означали бы чужое железо. Audition проставит своё.
"""
import xml.etree.ElementTree as ET

import pytest

from app.services.audition_session import SessionClip, SessionTrack, build_session_xml

RATE = 44100


def _parse(xml: str) -> ET.Element:
    return ET.fromstring(xml.lstrip("﻿"))


def _session(**kwargs):
    defaults = dict(title="Крылья полумрака · Глава 4", sample_rate=RATE, tracks=[])
    defaults.update(kwargs)
    return build_session_xml(**defaults)


def _track(name, clips):
    return SessionTrack(name=name, clips=clips)


def _clip(name, path, start, source_in=0.0, source_out=1.0):
    return SessionClip(name=name, path=path, start=start, source_in=source_in, source_out=source_out)


class TestTheShellOfTheFile:
    def test_it_declares_itself_a_session_audition_knows(self):
        root = _parse(_session())

        assert root.tag == "sesx"
        assert root.get("version") == "1.9"

    def test_the_doctype_is_there_because_audition_writes_one(self):
        assert "<!DOCTYPE sesx>" in _session()

    def test_the_sample_rate_is_the_one_we_count_in(self):
        root = _parse(_session(sample_rate=48000))

        assert root.find("session").get("sampleRate") == "48000"

    def test_we_do_not_hand_out_somebody_elses_sound_card(self):
        root = _parse(_session())

        assert root.find("audioDevice") is None
        assert root.find("session/xmpMetadata") is None


class TestTracks:
    def test_a_track_per_role_named_by_the_role(self):
        root = _parse(_session(tracks=[_track("Дгарнин", []), _track("Сатухух", [])]))
        names = [node.text for node in root.findall("session/tracks/audioTrack/trackParameters/name")]

        assert names == ["Дгарнин", "Сатухух"]

    def test_every_track_feeds_the_master(self):
        root = _parse(_session(tracks=[_track("Дгарнин", [])]))
        output = root.find("session/tracks/audioTrack/trackAudioParameters/trackOutput")
        master = root.find("session/tracks/masterTrack")

        assert output.get("type") == "trackID"
        assert output.get("outputID") == master.get("id")

    def test_the_master_goes_to_the_hardware(self):
        root = _parse(_session(tracks=[_track("Дгарнин", [])]))
        output = root.find("session/tracks/masterTrack/trackAudioParameters/trackOutput")

        assert output.get("type") == "hardwareOutput"

    def test_tracks_are_numbered_in_order_and_the_master_comes_last(self):
        root = _parse(_session(tracks=[_track("А", []), _track("Б", []), _track("В", [])]))
        indexes = [int(node.get("index")) for node in root.findall("session/tracks/audioTrack")]

        assert indexes == [1, 2, 3]
        assert int(root.find("session/tracks/masterTrack").get("index")) == 4


class TestClips:
    def test_time_is_counted_in_samples(self):
        root = _parse(_session(tracks=[_track("Дгарнин", [_clip("реплика", "/a.wav", start=2.0, source_in=0.5, source_out=1.5)])]))
        clip = root.find("session/tracks/audioTrack/audioClip")

        assert clip.get("startPoint") == str(2 * RATE)
        assert clip.get("endPoint") == str(3 * RATE), "две секунды плюс секунда куска"
        assert clip.get("sourceInPoint") == str(int(0.5 * RATE))
        assert clip.get("sourceOutPoint") == str(int(1.5 * RATE))

    def test_a_clip_is_named_so_the_engineer_knows_what_he_sees(self):
        root = _parse(_session(tracks=[_track("Дгарнин", [_clip("Я не пойду туда", "/a.wav", 0.0)])]))

        assert root.find("session/tracks/audioTrack/audioClip").get("name") == "Я не пойду туда"

    def test_clips_of_one_track_get_their_own_order(self):
        clips = [_clip("раз", "/a.wav", 0.0), _clip("два", "/a.wav", 5.0)]
        root = _parse(_session(tracks=[_track("Дгарнин", clips)]))
        found = root.findall("session/tracks/audioTrack/audioClip")

        assert [node.get("zOrder") for node in found] == ["0", "1"]
        assert len({node.get("id") for node in found}) == 2, "у клипов разные идентификаторы"


class TestTheFileTable:
    def test_a_file_is_listed_once_and_pointed_at_by_number(self):
        clips = [_clip("раз", "/такт/a.wav", 0.0), _clip("два", "/такт/a.wav", 5.0)]
        root = _parse(_session(tracks=[_track("Дгарнин", clips)]))
        files = root.findall("files/file")

        assert len(files) == 1, "один файл — одна строка в таблице, сколько бы клипов её ни звало"
        assert {node.get("fileID") for node in root.findall("session/tracks/audioTrack/audioClip")} == {files[0].get("id")}

    def test_two_files_get_two_rows(self):
        root = _parse(_session(tracks=[
            _track("Дгарнин", [_clip("раз", "/a.wav", 0.0)]),
            _track("Сатухух", [_clip("два", "/b.wav", 0.0)]),
        ]))

        assert len(root.findall("files/file")) == 2

    def test_the_handler_follows_the_extension(self):
        root = _parse(_session(tracks=[
            _track("А", [_clip("раз", "/a.wav", 0.0)]),
            _track("Б", [_clip("два", "/b.mp3", 0.0)]),
        ]))
        handlers = {node.get("relativePath"): node.get("mediaHandler") for node in root.findall("files/file")}

        assert handlers == {"a.wav": "AmioWav", "b.mp3": "AmioMP3"}

    def test_the_absolute_path_is_kept_because_that_is_how_audition_finds_it(self):
        root = _parse(_session(tracks=[_track("А", [_clip("раз", "/mnt/nas/КП/a.wav", 0.0)])]))

        assert root.find("files/file").get("absolutePath") == "/mnt/nas/КП/a.wav"


class TestTheLengthOfTheWholeThing:
    def test_the_session_lasts_until_the_last_clip_ends(self):
        root = _parse(_session(tracks=[
            _track("А", [_clip("раз", "/a.wav", start=0.0, source_in=0.0, source_out=2.0)]),
            _track("Б", [_clip("два", "/b.wav", start=10.0, source_in=0.0, source_out=3.0)]),
        ]))

        assert int(root.find("session").get("duration")) == 13 * RATE

    def test_an_empty_session_is_not_negative(self):
        assert int(_parse(_session()).find("session").get("duration")) == 0


def test_cyrillic_survives_the_round_trip():
    """Имена ролей и реплики — кириллица; файл объявлен UTF-8 и должен ею и быть."""
    xml = _session(tracks=[_track("Тот-Кто-Знает", [_clip("«Да», — сказал он", "/a.wav", 0.0)])])

    assert "Тот-Кто-Знает" in xml
    root = _parse(xml)
    assert root.find("session/tracks/audioTrack/audioClip").get("name") == "«Да», — сказал он"


class TestFindingTheAudioOnAnotherMachine:
    """Абсолютный путь в сессии — это путь сервера, а на Mac монтажёра его нет.

    Audition хранит оба пути и, не найдя абсолютный, идёт по относительному — от папки
    самой сессии. Значит сессию можно положить рядом с файлами, и она найдёт их где
    угодно: в архиве, на флешке, в чужой папке «Загрузки».
    """

    def test_the_relative_path_can_be_set_apart_from_the_absolute_one(self):
        clip = SessionClip(name="реплика", path="/mnt/nas/uploads/raw/2026/09/uuid_KP.wav",
                           start=0.0, source_out=1.0, relative="KP_Ch15_Bdeuks_SergeyZotov.wav")
        root = _parse(_session(tracks=[_track("Бдеукс", [clip])]))
        node = root.find("files/file")

        assert node.get("absolutePath") == "/mnt/nas/uploads/raw/2026/09/uuid_KP.wav"
        assert node.get("relativePath") == "KP_Ch15_Bdeuks_SergeyZotov.wav"

    def test_without_it_the_relative_path_is_just_the_file_name(self):
        root = _parse(_session(tracks=[_track("А", [_clip("раз", "/very/deep/path/a.wav", 0.0)])]))

        assert root.find("files/file").get("relativePath") == "a.wav"


class TestMarkersOnTheCuePointTrack:
    """Маркеры звукорежиссёра — отдельный трек `CuePoint Markers` в `xmpMetadata`.

    Формат подтверждён владельцем в настоящей Audition на сессии, собранной этим же
    генератором (см. `.superpowers/sdd/2026-09-21-sound-markers-sesx/implementer-common.md`).
    """

    def test_no_markers_means_no_xmp_block(self):
        from app.services.audition_session import SessionTrack, build_session_xml

        assert "xmpMetadata" not in build_session_xml(title="t", tracks=[SessionTrack(name="A")])

    def test_markers_block_point_and_range_in_samples(self):
        import re
        import xml.dom.minidom as minidom

        from app.services.audition_session import SessionMarker, SessionTrack, build_session_xml

        xml = build_session_xml(title="t", sample_rate=44100, tracks=[SessionTrack(name="A")], markers=[
            SessionMarker(name="СЦЕНА 1 · Таверна", start=0.0, duration=45.0,
                         comment="фон: гомон · 🔎 tavern", guid="xmp:id:g1"),
            SessionMarker(name="● дверь <хлоп>", start=12.5, comment="a ]]> b", guid="xmp:id:g2"),
        ])
        doc = minidom.parseString(xml.lstrip("﻿").encode("utf-8"))
        packet = doc.getElementsByTagName("xmpMetadata")[0].firstChild.data
        inner = minidom.parseString(re.sub(r"<\?xpacket[^>]*\?>", "", packet).encode("utf-8"))
        names = [n.firstChild.data for n in inner.getElementsByTagName("xmpDM:name")]

        assert names == ["СЦЕНА 1 · Таверна", "● дверь <хлоп>"]
        assert [n.firstChild.data for n in inner.getElementsByTagName("xmpDM:startTime")] == ["0", "551250"]
        assert [n.firstChild.data for n in inner.getElementsByTagName("xmpDM:duration")] == ["1984500"]
        assert "]]>" not in packet

    def test_the_block_sits_between_session_state_and_clip_groups(self):
        from app.services.audition_session import SessionMarker, SessionTrack, build_session_xml

        xml = build_session_xml(title="t", tracks=[SessionTrack(name="A")],
                                markers=[SessionMarker(name="●", start=0.0)])

        assert xml.index("</sessionState>") < xml.index("<xmpMetadata>") < xml.index("<clipGroups")

    def test_a_marker_without_a_guid_gets_a_random_one(self):
        import re
        import xml.dom.minidom as minidom

        from app.services.audition_session import SessionMarker, SessionTrack, build_session_xml

        xml = build_session_xml(title="t", tracks=[SessionTrack(name="A")],
                                markers=[SessionMarker(name="●", start=0.0)])
        doc = minidom.parseString(xml.lstrip("﻿").encode("utf-8"))
        packet = doc.getElementsByTagName("xmpMetadata")[0].firstChild.data
        inner = minidom.parseString(re.sub(r"<\?xpacket[^>]*\?>", "", packet).encode("utf-8"))
        guid = inner.getElementsByTagName("xmpDM:guid")[0].firstChild.data

        assert re.fullmatch(r"xmp:id:[0-9a-f-]{36}", guid)

    def test_a_zero_duration_marker_writes_no_duration_tag(self):
        import xml.dom.minidom as minidom
        import re

        from app.services.audition_session import SessionMarker, SessionTrack, build_session_xml

        xml = build_session_xml(title="t", tracks=[SessionTrack(name="A")],
                                markers=[SessionMarker(name="◆ что", start=1.0, guid="xmp:id:g3")])
        doc = minidom.parseString(xml.lstrip("﻿").encode("utf-8"))
        packet = doc.getElementsByTagName("xmpMetadata")[0].firstChild.data
        inner = minidom.parseString(re.sub(r"<\?xpacket[^>]*\?>", "", packet).encode("utf-8"))

        assert inner.getElementsByTagName("xmpDM:duration") == []
