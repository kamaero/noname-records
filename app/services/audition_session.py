"""Сессия Adobe Audition, собранная нами: треки по ролям, клипы по репликам.

Ради этого всё и затевалось. В «Байке 65» — 825 клипов на 32 трека, из них 247 на одном
Дегатти: это руками нарезанная сплошная запись роли, двести сорок семь раз за одну
байку. Выравнивание знает границы каждой реплики, а `.sesx` умеет ставить их на места.

Формат разобран по настоящей сессии Audition 22.5. Три вещи, в которых легко ошибиться:
время считается в сэмплах, а не в секундах; у клипа задаются и место на таймлайне
(`startPoint`/`endPoint`), и вырезаемый кусок исходника (`sourceInPoint`/`sourceOutPoint`);
файл подключается отдельной таблицей и упоминается в клипе номером.

Чего мы нарочно не пишем: `properties` с пресетами Essential Sound и `audioDevice` —
там идентификаторы звуковой карты владельца сессии, и на другой машине они означали бы
чужое железо. Audition проставит своё при первом открытии. `xmpMetadata` тоже не пишем
целиком (настоящая — сто килобайт служебных данных Adobe), но урезанный блок с одним
треком `CuePoint Markers` всё же кладём — маркерам звукорежиссёра больше некуда встать:
Audition читает разметку дорожки только оттуда, а не из своего формата клипов/треков.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from uuid import uuid4
from xml.sax.saxutils import escape, quoteattr

#: с этого номера идут треки; мастер держит круглый, как в настоящих сессиях
FIRST_TRACK_ID = 10001
MASTER_TRACK_ID = 10000

_HANDLERS = {
    ".wav": "AmioWav",
    ".mp3": "AmioMP3",
    ".flac": "AmioFlac",
    ".ogg": "AmioOgg",
    ".aif": "AmioAiff",
    ".aiff": "AmioAiff",
}


@dataclass
class SessionClip:
    """Один клип: что играет, откуда вырезано и где стоит. Время — в секундах."""

    name: str
    path: str
    start: float
    source_in: float = 0.0
    source_out: float = 0.0
    #: как файл называется рядом с сессией. Абсолютный путь — это путь сервера, и на
    #: чужой машине его нет; не найдя его, Audition идёт по относительному от папки
    #: с сессией. Значит сессию можно положить прямо к файлам.
    relative: str = ""

    @property
    def length(self) -> float:
        return max(0.0, float(self.source_out) - float(self.source_in))


@dataclass
class SessionTrack:
    name: str
    clips: list[SessionClip] = field(default_factory=list)


@dataclass
class SessionMarker:
    """Маркер на дорожке `CuePoint Markers`: сцена (отрезок), звук или переход (точка).

    Время — в секундах, как у клипа. `duration` пишется в XML только когда она
    больше нуля: точечный маркер (звук, переход) её вовсе не несёт, а сцена — несёт.
    `guid` задаёт вызывающий, когда его важно сделать детерминированным (иначе каждая
    пересборка выглядела бы для `session_fingerprint` новым маркером); пустой — получает
    случайный, как у остальных GUID в этом файле.
    """

    name: str
    start: float
    duration: float = 0.0
    comment: str = ""
    guid: str = ""


def _samples(seconds: float, rate: int) -> int:
    return int(round(max(0.0, float(seconds)) * int(rate)))


def _handler(path: str) -> str:
    return _HANDLERS.get(os.path.splitext(str(path or ""))[1].lower(), "AmioWav")


def _attrs(**pairs) -> str:
    return " ".join(f"{key}={quoteattr(str(value))}" for key, value in pairs.items())


def _component(component_id: str, node_id: str, name: str) -> str:
    # GUID у каждого экземпляра свой — в настоящих сессиях они не повторяются
    return (
        f'          <component componentGuid="{uuid4()}" componentID={quoteattr(component_id)} '
        f'id={quoteattr(node_id)} name={quoteattr(name)} powered="true" />'
    )


def _clip_xml(clip: SessionClip, *, clip_id: int, file_id: int, z_order: int, rate: int) -> list[str]:
    start = _samples(clip.start, rate)
    source_in = _samples(clip.source_in, rate)
    source_out = _samples(clip.source_out, rate)
    length = max(0, source_out - source_in)
    return [
        "        <audioClip " + _attrs(
            clipAutoCrossfade="true", crossFadeHeadClipID="-1", crossFadeTailClipID="-1",
            endPoint=start + length, fileID=file_id, hue="-1", id=clip_id,
            lockedInTime="false", looped="false", name=clip.name, offline="false",
            select="false", sourceInPoint=source_in, sourceOutPoint=source_out,
            startPoint=start, zOrder=z_order,
        ) + ">",
        _component("Audition.Fader", "clipGain", "volume"),
        _component("Audition.Mute", "clipMute", "Mute"),
        _component("Audition.StereoPanner", "clipPan", "StereoPanner"),
        '          <fadeIn crossFadeLinkType="linkedAsymmetric" endPoint="0" shape="19" startPoint="0" type="log" />',
        f'          <fadeOut crossFadeLinkType="linkedAsymmetric" endPoint="{length}" shape="19" startPoint="{length}" type="log" />',
        "          <channelMap />",
        "        </audioClip>",
    ]


def _track_xml(track: SessionTrack, *, track_id: int, index: int, rate: int, file_ids: dict[str, int], clip_ids) -> list[str]:
    lines = [
        f'      <audioTrack automationLaneOpenState="false" id="{track_id}" index="{index}" select="false" visible="true">',
        '        <trackParameters trackHeight="90" trackHue="-1" trackMinimized="false">',
        f"          <name>{escape(str(track.name or ''))}</name>",
        "        </trackParameters>",
        '        <trackAudioParameters audioChannelType="mono" automationMode="1" monitoring="false" recordArmed="false" solo="false" soloSafe="false">',
        f'          <trackOutput outputID="{MASTER_TRACK_ID}" type="trackID" />',
        '          <trackInput inputID="-1" />',
        _component("Audition.Fader", "trackFader", "volume"),
        "        </trackAudioParameters>",
        '        <editParameter parameterIndex="0" slotIndex="4294967280" />',
    ]
    for order, clip in enumerate(track.clips):
        lines += _clip_xml(clip, clip_id=next(clip_ids), file_id=file_ids[clip.path], z_order=order, rate=rate)
    lines.append("      </audioTrack>")
    return lines


def _cdata_safe(text: str) -> str:
    # Экранируем как обычный XML-текст, а следом добиваем ']]>' — она бы закрыла
    # CDATA-секцию раньше времени, а escape() не трогает ']' саму по себе.
    return escape(str(text or "")).replace("]]>", "]] >")


def _marker_xml(marker: SessionMarker, *, rate: int) -> list[str]:
    guid = marker.guid or f"xmp:id:{uuid4()}"
    lines = [
        '      <rdf:li rdf:parseType="Resource">',
        f"        <xmpDM:startTime>{_samples(marker.start, rate)}</xmpDM:startTime>",
    ]
    if float(marker.duration or 0.0) > 0:
        lines.append(f"        <xmpDM:duration>{_samples(marker.duration, rate)}</xmpDM:duration>")
    lines += [
        f"        <xmpDM:name>{_cdata_safe(marker.name)}</xmpDM:name>",
        f"        <xmpDM:comment>{_cdata_safe(marker.comment)}</xmpDM:comment>",
        "        <xmpDM:cuePointParams><rdf:Seq><rdf:li rdf:parseType=\"Resource\">"
        f"<xmpDM:key>marker_guid</xmpDM:key><xmpDM:value>{guid}</xmpDM:value>"
        "</rdf:li></rdf:Seq></xmpDM:cuePointParams>",
        f"        <xmpDM:guid>{guid}</xmpDM:guid>",
        "      </rdf:li>",
    ]
    return lines


def _xmp_metadata_xml(markers: list[SessionMarker], *, rate: int) -> list[str]:
    """Блок `xmpMetadata` с одним треком `CuePoint Markers`. Пусто — маркеров нет вовсе.

    Формат подтверждён владельцем в настоящей Audition 22 на сессии, собранной этим же
    генератором: `<?xpacket ...?>` вокруг куска `x:xmpmeta`, внутри — один трек `Cue`
    с очередью маркеров. Меньше этого Audition разметку дорожки не читает.
    """
    if not markers:
        return []
    body: list[str] = []
    for marker in markers:
        body += _marker_xml(marker, rate=rate)
    packet = "\n".join([
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>',
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">',
        '<rdf:Description rdf:about="" xmlns:xmpDM="http://ns.adobe.com/xmp/1.0/DynamicMedia/">'
        "<xmpDM:Tracks><rdf:Bag>",
        '<rdf:li rdf:parseType="Resource"><xmpDM:trackName>CuePoint Markers</xmpDM:trackName>'
        "<xmpDM:trackType>Cue</xmpDM:trackType>",
        f"<xmpDM:frameRate>f{rate}</xmpDM:frameRate><xmpDM:markers><rdf:Seq>",
        *body,
        "</rdf:Seq></xmpDM:markers></rdf:li>",
        "</rdf:Bag></xmpDM:Tracks></rdf:Description></rdf:RDF></x:xmpmeta>",
        '<?xpacket end="w"?>',
    ])
    return [f"    <xmpMetadata><![CDATA[{packet}]]></xmpMetadata>"]


def build_session_xml(*, title: str, sample_rate: int = 44100, tracks: list[SessionTrack],
                      markers: list[SessionMarker] | None = None) -> str:
    """XML сессии Audition. Возвращается строкой — пишет её тот, кто позвал."""
    rate = int(sample_rate or 44100)
    tracks = list(tracks or [])

    file_ids: dict[str, int] = {}
    relatives: dict[str, str] = {}
    for track in tracks:
        for clip in track.clips:
            file_ids.setdefault(clip.path, len(file_ids))
            if clip.relative:
                relatives[clip.path] = clip.relative

    duration = _samples(
        max((clip.start + clip.length for track in tracks for clip in track.clips), default=0.0),
        rate,
    )
    clip_ids = iter(range(1_000_000))

    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>',
        "<!DOCTYPE sesx>",
        '<sesx version="1.9">',
        f'  <session appBuild="0.0.0.0" appVersion="22.5" audioChannelType="stereo" '
        f'bitDepth="32" duration="{duration}" sampleRate="{rate}">',
        "    <tracks>",
    ]
    for index, track in enumerate(tracks, start=1):
        lines += _track_xml(track, track_id=FIRST_TRACK_ID + index - 1, index=index, rate=rate,
                            file_ids=file_ids, clip_ids=clip_ids)
    lines += [
        f'      <masterTrack automationLaneOpenState="false" id="{MASTER_TRACK_ID}" index="{len(tracks) + 1}" select="false" visible="true">',
        '        <trackParameters trackHeight="90" trackHue="-1" trackMinimized="false">',
        "          <name>Mix</name>",
        "        </trackParameters>",
        '        <trackAudioParameters audioChannelType="stereo" automationMode="1" monitoring="false" recordArmed="false" solo="false" soloSafe="false">',
        '          <trackOutput outputID="1" type="hardwareOutput" />',
        _component("Audition.Fader", "trackFader", "volume"),
        "        </trackAudioParameters>",
        '        <editParameter parameterIndex="0" slotIndex="4294967280" />',
        "      </masterTrack>",
        "    </tracks>",
        '    <sessionState ctiPosition="0" smpteStart="0">',
        '      <selectionState selectionDuration="0" selectionStart="0" />',
        f'      <viewState horizontalViewDuration="{max(duration, rate * 60)}" horizontalViewStart="0" trackControlsWidth="224" verticalScrollOffset="0" />',
        '      <timeFormatState beatsPerBar="4" beatsPerMinute="120" customFrameRate="12" linkToDefaultTimeSettings="true" noteLength="4" subdivisions="16" timeCodeDropFrame="false" timeCodeFrameRate="30" timeCodeNTSC="false" timeFormat="timeFormatDecimal" />',
        '      <mixingOptionState defaultPanModeLogarithmic="true" panPower="-2" playOverlappedRecordingClips="false" />',
        "    </sessionState>",
        *_xmp_metadata_xml(list(markers or []), rate=rate),
        "    <clipGroups />",
        "  </session>",
        "  <files>",
    ]
    for path, file_id in file_ids.items():
        lines.append(
            "    <file " + _attrs(
                absolutePath=path, id=file_id, mediaHandler=_handler(path),
                relativePath=relatives.get(path) or os.path.basename(str(path or "")),
            ) + " />"
        )
    lines += ["  </files>", "</sesx>", ""]
    # BOM: настоящие сессии Audition пишутся с ним, и нам незачем отличаться
    return "﻿" + "\n".join(lines)
