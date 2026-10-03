"""The canonical cast of «Полумракские байки» as the owner keeps it.

One row per actor, their roles comma-separated. Checked against the file before
trusting the split: no role name contains a comma, and no bracket is left unpaired,
so the comma really is a separator here — «Остримунд Ваянелли» sits as one role and
«президент Ппейпогиска» as another, under a different actor.

This is the authority the legends are not. A legend is per chapter and may omit the
actor; this workbook holds 1019 role entries across 192 actors, and is what the
author profile should be seeded from.
"""
import pathlib

import pytest

from app.v2.cast_book import CastRole, load_cast, split_roles



class TestSplit:
    def test_roles_are_comma_separated(self):
        assert split_roles("Морской Епископ, Мо’Нахти") == ["Морской Епископ", "Мо’Нахти"]

    def test_a_trailing_comma_does_not_invent_an_empty_role(self):
        assert split_roles("Пеймон, Нозмре, Дликдеп Гехьмузо, ") == [
            "Пеймон", "Нозмре", "Дликдеп Гехьмузо",
        ]

    def test_a_lowercase_role_is_a_role(self):
        """457 of the 1019 entries start lowercase — «щитник», «бесовка», «наложница».

        They are minor parts described rather than named, not fragments of a split
        that went wrong.
        """
        assert split_roles("маэстро Эсе-ль-Теорк, цирюльник") == [
            "маэстро Эсе-ль-Теорк", "цирюльник",
        ]

    def test_nothing_at_all(self):
        assert split_roles("") == []
        assert split_roles(None) == []


class TestLoad:
    def test_one_role_can_belong_to_several_actors(self):
        """«наложница» is played nine times by nine different people."""
        cast = load_cast(rows=[
            ("Аверина Виктория", "наложница, женщина"),
            ("Арканова Виктория", "наложница"),
        ])

        entry = cast["наложница"]
        assert entry.role == "наложница"
        assert sorted(entry.actors) == ["Аверина Виктория", "Арканова Виктория"]

    def test_names_fold_the_way_the_profile_folds_them(self):
        """A stress mark in one place and not the other is one role, not two."""
        cast = load_cast(rows=[("Актёр А", "Дгарни́н"), ("Актёр Б", "Дгарнин")])

        assert len(cast) == 1
        assert len(next(iter(cast.values())).actors) == 2

    def test_an_actor_with_no_roles_adds_nothing(self):
        assert load_cast(rows=[("Кто-то", None), ("Ещё кто-то", "  ")]) == {}

    def test_lookup_is_by_folded_name(self):
        cast = load_cast(rows=[("Глеб Лапин", "Вукьрадух")])

        assert "вукьрадух" in cast
        assert cast["вукьрадух"].role == "Вукьрадух"
