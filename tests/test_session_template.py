"""Шаблон сессии: цепочки эффектов по ролям из .sesx, сохранённого владельцем в Audition.

Пресет дорожки в .sesx не ссылкой, а развёрнутой цепочкой (`effectsRack`) со всеми
параметрами — пресеты владельца живут в настройках Audition на его машине. Поэтому
владелец один раз собирает шаблон (дорожка на роль, на ней её пресет), и сборка
главы копирует цепочку на дорожку с тем же именем. Обязательный первый эффект —
Single-band Compressor с пресетом Voice Over (владелец, 25.10).
"""
import re
import xml.etree.ElementTree as ET

import pytest

from app.services import session_template as st
from app.services.audition_session import SessionTrack, build_session_xml

VO_ID = "2da3e2bc-5005-481c-bbeb-7689d3169edf"


def _slot(index, name, preset="(Custom)", component_id="164b4e8a-7105-406b-b23b-1ef2cc4d8957"):
    return (f'<slot powered="true" slotIndex="{index}">'
            f'<component componentGuid="g-{name}-{index}" componentID="{component_id}" name="{name}" presetName="{preset}">'
            '<busConfig><![CDATA[{"in":[{"layout":[100,101]}]}]]></busConfig>'
            '<presetChunkData><![CDATA[QUJD]]></presetChunkData>'
            '</component></slot>')


VO = lambda index: _slot(index, "Single-band Compressor", "Voice Over", VO_ID)  # noqa: E731

HEADER = ('<component componentGuid="h1" componentID="Audition.ComponentGroup" id="rackMix" powered="true">'
          '<parameter index="0" parameterValue="0"/></component>'
          '<component componentGuid="h2" id="rackInput" powered="true"><parameter index="0" parameterValue="1"/></component>')


def _track(name, slots, channel="stereo"):
    rack = f'<effectsRack preFader="true">{HEADER}{"".join(slots)}</effectsRack>' if slots is not None else ""
    return (f'<audioTrack id="1" index="1"><trackParameters trackHue="-1"><name>{name}</name></trackParameters>'
            f'<trackAudioParameters audioChannelType="{channel}"><trackOutput outputID="10000" type="trackID"/>'
            f'</trackAudioParameters>{rack}<editParameter parameterIndex="0" slotIndex="4294967280"/></audioTrack>')


def _template(*tracks):
    return ('﻿<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n<!DOCTYPE sesx>\n'
            '<sesx version="1.9"><session sampleRate="48000"><tracks>' + "".join(tracks)
            + '<masterTrack id="10000"><trackParameters><name>Mix</name></trackParameters></masterTrack>'
            '</tracks></session></sesx>')


def _names(rack):
    return re.findall(r' name="([^"]*)" presetName', rack)


class TestReadingTheTemplate:
    def test_each_role_gets_its_own_chain_with_voice_over_first(self):
        tpl = st.parse_template(_template(
            _track("Рассказчик", [VO(0), _slot(1, "Pitch Shifter"), _slot(2, "Parametric Equalizer")]),
            _track("Хдыбдяр", [VO(0), _slot(1, "DeEsser")]),
        ))
        fx = st.rack_for(tpl, "Рассказчик")
        assert _names(fx.rack) == ["Single-band Compressor", "Pitch Shifter", "Parametric Equalizer"]
        assert fx.channel_type == "stereo"
        assert _names(st.rack_for(tpl, "хдыбдяр").rack) == ["Single-band Compressor", "DeEsser"]

    def test_a_role_missing_from_the_template_still_gets_voice_over(self):
        """Voice Over — обязательный эффект на каждой голосовой дорожке."""
        tpl = st.parse_template(_template(_track("Рассказчик", [VO(0), _slot(1, "Pitch Shifter")])))
        fx = st.rack_for(tpl, "Юмкуба")
        assert _names(fx.rack) == ["Single-band Compressor"]
        assert 'presetName="Voice Over"' in fx.rack

    def test_voice_over_is_put_first_when_the_template_has_it_later(self):
        tpl = st.parse_template(_template(_track("Химес", [_slot(0, "DeEsser"), VO(1)])))
        rack = st.rack_for(tpl, "Химес").rack
        assert _names(rack) == ["Single-band Compressor", "DeEsser"]
        assert re.findall(r'slotIndex="(\d+)"', rack) == ["0", "1"]

    def test_voice_over_is_added_when_a_role_forgot_it(self):
        tpl = st.parse_template(_template(_track("Рассказчик", [VO(0)]), _track("Хоркон", [_slot(0, "DeEsser")])))
        assert _names(st.rack_for(tpl, "Хоркон").rack) == ["Single-band Compressor", "DeEsser"]

    def test_a_tuned_compressor_on_the_track_is_its_voice_over(self):
        """Владелец подстраивает Voice Over под голос — пресет становится «(Custom)».
        Такой компрессор и есть Voice Over этой роли: второй рядом не вешается (25.10:
        у Рассказчика, Хдыбдяра, Фибэла и Фэдывнютд висело по два)."""
        tuned = _slot(1, "Single-band Compressor", "(Custom)", VO_ID)
        tpl = st.parse_template(_template(_track("Рассказчик", [VO(0)]),
                                          _track("Фибэл", [_slot(0, "Pitch Shifter"), tuned])))
        rack = st.rack_for(tpl, "Фибэл").rack
        assert _names(rack) == ["Single-band Compressor", "Pitch Shifter"]
        assert 'presetName="Voice Over"' not in rack

    def test_without_voice_over_anywhere_an_unknown_role_stays_bare(self):
        tpl = st.parse_template(_template(_track("Хоркон", [_slot(0, "DeEsser")])))
        assert st.rack_for(tpl, "Юмкуба") is None
        assert _names(st.rack_for(tpl, "Хоркон").rack) == ["DeEsser"]

    def test_every_copy_gets_fresh_component_guids(self):
        """Audition не терпит повторов GUID внутри файла."""
        tpl = st.parse_template(_template(_track("Рассказчик", [VO(0)])))
        one, two = st.rack_for(tpl, "Рассказчик").rack, st.rack_for(tpl, "Фибэл").rack
        guids = re.findall(r'componentGuid="([^"]*)"', one + two)
        assert len(guids) == len(set(guids)) and "g-Single-band Compressor-0" not in guids

    def test_cdata_survives_byte_for_byte(self):
        tpl = st.parse_template(_template(_track("Рассказчик", [VO(0)])))
        assert "<presetChunkData><![CDATA[QUJD]]></presetChunkData>" in st.rack_for(tpl, "Рассказчик").rack

    def test_a_file_that_is_not_a_session_is_refused(self):
        with pytest.raises(st.TemplateError):
            st.parse_template("<html>нет</html>")

    def test_a_session_without_any_effects_is_refused(self):
        with pytest.raises(st.TemplateError):
            st.parse_template(_template(_track("Рассказчик", None)))


class TestTheChapterSession:
    def test_the_rack_lands_on_the_track_and_the_xml_stays_valid(self):
        tpl = st.parse_template(_template(_track("Рассказчик", [VO(0), _slot(1, "Pitch Shifter")])))
        tracks = [SessionTrack(name="Рассказчик"), SessionTrack(name="Ambient 1")]
        st.apply_template(tracks, tpl, voices={"Рассказчик"})
        root = ET.fromstring(build_session_xml(title="t", tracks=tracks).lstrip("﻿"))
        narrator, ambient = root.findall("session/tracks/audioTrack")
        assert [c.get("name") for c in narrator.findall("effectsRack/slot/component")] == \
            ["Single-band Compressor", "Pitch Shifter"]
        assert narrator.find("trackAudioParameters").get("audioChannelType") == "stereo"
        # эмбиент — не голос: Voice Over ему не нужен
        assert ambient.find("effectsRack") is None
        assert ambient.find("trackAudioParameters").get("audioChannelType") == "mono"

    def test_no_template_changes_nothing(self):
        tracks = [SessionTrack(name="Рассказчик")]
        st.apply_template(tracks, None, voices={"Рассказчик"})
        assert tracks[0].effects_rack == "" and tracks[0].channel_type == "mono"


class TestKeepingTheTemplate:
    def test_saved_template_is_read_back(self, tmp_path):
        path = str(tmp_path / "session_template.sesx")
        tpl = st.save_template(_template(_track("Рассказчик", [VO(0)])).encode("utf-8"), path=path)
        assert st.load_template(path=path).roles == tpl.roles == ["Рассказчик"]

    def test_a_bad_file_does_not_replace_the_good_one(self, tmp_path):
        path = str(tmp_path / "session_template.sesx")
        st.save_template(_template(_track("Рассказчик", [VO(0)])).encode("utf-8"), path=path)
        with pytest.raises(st.TemplateError):
            st.save_template(b"<html/>", path=path)
        assert st.load_template(path=path).roles == ["Рассказчик"]

    def test_no_file_means_no_template(self, tmp_path):
        assert st.load_template(path=str(tmp_path / "none.sesx")) is None


class TestOwnerConventions:
    """Владелец 25.10: «ГеКоон = Ге'Коон, лень было апостроф ставить»; «Любой голос» —
    универсальная дорожка для персонажа, у которого нет своего пресета."""

    def test_apostrophes_and_hyphens_do_not_matter(self):
        tpl = st.parse_template(_template(_track("ГеКоон", [VO(0), _slot(1, "DeEsser")])))
        assert _names(st.rack_for(tpl, "Ге'Коон").rack) == ["Single-band Compressor", "DeEsser"]
        assert _names(st.rack_for(tpl, "Ге-Коон").rack) == ["Single-band Compressor", "DeEsser"]

    def test_a_role_without_a_preset_gets_any_voice(self):
        tpl = st.parse_template(_template(_track("Рассказчик", [VO(0)]),
                                          _track("Любой голос", [_slot(0, "Parametric Equalizer"), VO(1)])))
        assert _names(st.rack_for(tpl, "Юмкуба").rack) == ["Single-band Compressor", "Parametric Equalizer"]

    def test_any_voice_is_not_counted_as_a_role(self):
        tpl = st.parse_template(_template(_track("Рассказчик", [VO(0)]), _track("Любой голос", [VO(0)])))
        assert tpl.roles == ["Рассказчик"] and tpl.any_voice

    def test_tracks_matching_no_role_are_named(self):
        tpl = st.parse_template(_template(_track("Рассказчик", [VO(0)]), _track("Хдыдяр", [VO(0)]),
                                          _track("ГеКоон", [VO(0)])))
        assert st.unknown_roles(tpl, ["Рассказчик", "Хдыбдяр", "Ге'Коон"]) == ["Хдыдяр"]
