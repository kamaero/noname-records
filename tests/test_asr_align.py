"""Что из сценария диктор произнёс, а что пропустил — и где именно в файле.

Диктор читает свою роль сверху вниз, поэтому реплики в записи идут в том же порядке,
что и в сценарии. Это и есть главная опора: искать каждую реплику по всему файлу
независимо — значит позволить второй «Да.» найтись раньше первой и перепутать всё
дальше. Поиск идёт вперёд от места, где кончилась предыдущая реплика.

Совпадение — нечёткое. Распознавание слышит «Дгарнина» как «Джим велл», глотает
предлоги и дописывает знаки препинания; требовать точного совпадения — значит объявить
пропущенной каждую вторую реплику.
"""
import pytest

from app.services.asr_align import align_transcript, normalize_words, number_words, ordinal_words, word_stream


def _spoken(*pairs, gap=0.5):
    """Реплики, произнесённые подряд: (текст, длительность)."""
    out, clock = [], 0.0
    for text, length in pairs:
        out.append({"text": text, "start": round(clock, 3), "end": round(clock + length, 3)})
        clock += length + gap
    return out


class TestTheStreamOfWords:
    def test_a_segment_becomes_words_with_times_spread_across_it(self):
        stream = word_stream([{"text": "один два три четыре", "start": 0.0, "end": 4.0}])

        assert [word for word, _s, _e in stream] == ["один", "два", "три", "четыре"]
        assert stream[0][1] == pytest.approx(0.0)
        assert stream[-1][2] == pytest.approx(4.0)

    def test_word_level_times_are_kept_when_they_are_given(self):
        stream = word_stream([{"text": "один два", "start": 0.0, "end": 2.0,
                               "words": [{"word": "один", "start": 0.1, "end": 0.6},
                                         {"word": "два", "start": 1.4, "end": 1.9}]}])

        assert stream == [("один", 0.1, 0.6), ("два", 1.4, 1.9)]

    def test_punctuation_and_case_do_not_survive(self):
        stream = word_stream([{"text": "Дгарнин, — сказал он!", "start": 0.0, "end": 3.0}])

        assert [word for word, _s, _e in stream] == ["дгарнин", "сказал", "он"]


class TestWhenEverythingWasRead:
    def test_every_line_is_found(self):
        expected = ["Я не пойду туда", "Почему же", "Там холодно"]
        result = align_transcript(expected, _spoken(("Я не пойду туда", 3.0), ("Почему же", 2.0), ("Там холодно", 2.5)))

        assert [line["matched"] for line in result["lines"]] == [True, True, True]
        assert result["missing"] == []
        assert result["coverage"] == pytest.approx(1.0)

    def test_each_line_knows_where_it_sits_in_the_file(self):
        expected = ["Я не пойду туда", "Почему же"]
        result = align_transcript(expected, _spoken(("Я не пойду туда", 3.0), ("Почему же", 2.0)))

        first, second = result["lines"]
        assert first["start"] == pytest.approx(0.0, abs=0.2)
        assert first["end"] == pytest.approx(3.0, abs=0.2)
        assert second["start"] == pytest.approx(3.5, abs=0.3)

    def test_a_misheard_word_does_not_lose_the_line(self):
        """«Дгарнин» распознан как «Джим вел» — реплика та же."""
        result = align_transcript(
            ["Дгарнин обернулся и молча кивнул"],
            _spoken(("Джим вел обернулся и молча кивнул", 4.0)),
        )

        assert result["lines"][0]["matched"] is True


class TestWhenSomethingWasSkipped:
    def test_the_skipped_line_is_named(self):
        expected = ["Первая реплика героя", "Вторая реплика героя", "Третья реплика героя"]
        result = align_transcript(expected, _spoken(("Первая реплика героя", 3.0), ("Третья реплика героя", 3.0)))

        assert result["missing"] == [1]
        assert result["lines"][1]["matched"] is False
        assert result["coverage"] == pytest.approx(2 / 3)

    def test_a_skipped_line_does_not_drag_the_rest_out_of_place(self):
        """Пропущенная реплика не должна съесть кусок потока и сдвинуть следующие."""
        expected = ["Первая реплика героя", "Вторая реплика героя", "Третья реплика героя"]
        result = align_transcript(expected, _spoken(("Первая реплика героя", 3.0), ("Третья реплика героя", 3.0)))

        assert result["lines"][2]["matched"] is True
        assert result["lines"][2]["start"] == pytest.approx(3.5, abs=0.3)

    def test_nothing_recorded_at_all(self):
        result = align_transcript(["Первая реплика", "Вторая реплика"], [])

        assert result["missing"] == [0, 1]
        assert result["coverage"] == pytest.approx(0.0)

    def test_a_role_with_no_lines_asks_nothing(self):
        result = align_transcript([], _spoken(("что-то лишнее", 2.0)))

        assert result["lines"] == [] and result["missing"] == []


class TestWhatTheOrderBuysUs:
    def test_the_same_line_twice_matches_two_different_places(self):
        """«Да.» встречается дважды; без порядка обе нашлись бы в одном месте."""
        expected = ["Да", "Я подумаю над этим как следует", "Да"]
        result = align_transcript(expected, _spoken(("Да", 1.0), ("Я подумаю над этим как следует", 4.0), ("Да", 1.0)))

        first, _middle, last = result["lines"]
        assert first["matched"] and last["matched"]
        assert last["start"] > first["end"]

    def test_chatter_between_lines_is_stepped_over(self):
        """Диктор кашлянул, сказал «так, ещё раз» — реплики от этого не теряются."""
        expected = ["Я не пойду туда", "Там холодно"]
        result = align_transcript(
            expected,
            _spoken(("Я не пойду туда", 3.0), ("так ещё раз попробую", 2.0), ("Там холодно", 2.0)),
        )

        assert [line["matched"] for line in result["lines"]] == [True, True]


class TestWhereTheLineIsDrawn:
    def test_a_line_read_quite_differently_is_not_claimed_as_read(self):
        result = align_transcript(
            ["Пусть Полумрак рассудит нас обоих по справедливости"],
            _spoken(("совершенно посторонний текст ни о чём", 4.0)),
        )

        assert result["lines"][0]["matched"] is False

    def test_the_score_is_reported_so_a_human_can_judge(self):
        result = align_transcript(["Я не пойду туда"], _spoken(("Я не пойду туда", 3.0)))

        assert result["lines"][0]["score"] == pytest.approx(1.0, abs=0.01)


class TestAuthorsElongatedVowels:
    """Автор растягивает гласные дефисом для выразительности («попро-о-обую»),
    диктор читает обычное слово — растяжку нужно схлопнуть до сверки, иначе
    `_WORD` режет слово по дефисам на обрубки, ни один из которых не совпадёт
    с расшифровкой. Ровно это уронило главу 30 (Сенннуно, Глеб Лапин) до 33%."""

    def test_a_stretched_vowel_collapses_to_the_plain_word(self):
        assert normalize_words("Я попро-о-обую!") == ["я", "попробую"]

    def test_several_stretches_in_one_line_all_collapse(self):
        assert normalize_words("не обессу-у-удьте! Я верну-у-усь") == ["не", "обессудьте", "я", "вернусь"]

    def test_double_vowels_without_a_hyphen_are_left_alone(self):
        """«зоолог», «поощрять» — не растяжка, а обычные слова с двумя гласными подряд."""
        assert normalize_words("зоолог поощрять") == ["зоолог", "поощрять"]

    def test_a_hyphen_between_different_letters_still_splits_the_word(self):
        """«кто-то», «по-моему» — дефис не между одинаковыми гласными, это не растяжка,
        `_WORD` как и раньше режет по нему на два слова."""
        assert normalize_words("кто-то по-моему") == ["кто", "то", "по", "моему"]

    def test_doubled_words_that_share_a_vowel_at_the_seam_are_not_stretch(self):
        """«еле-еле», «иди-иди» — это два повторённых слова, а не растяжка внутри
        одного: первое кончается и второе начинается той же гласной («е-е», «и-и»),
        но обратной ссылкой их от настоящей растяжки не отличить (согласная перед
        якорной гласной есть в обоих случаях) — поэтому граница проведена по числу
        повторов дефиса: у растяжки автора их два и больше, у стыка двух слов — один."""
        assert normalize_words("еле-еле иди-иди") == ["еле", "еле", "иди", "иди"]

    def test_a_single_repeat_keeps_the_old_behaviour(self):
        """Характеризационный тест: одиночный повтор («ну-у») и до, и после правки
        не считается растяжкой (нужно два и больше), режется по дефису как раньше."""
        assert normalize_words("ну-у") == ["ну", "у"]

    def test_a_three_letter_stretch_and_a_yo_stretch_collapse_too(self):
        assert normalize_words("Ка-а-а-ак") == ["как"]
        assert normalize_words("бо-о-ог") == ["бог"]

    def test_a_scripted_stretched_line_is_found_in_the_plain_transcript(self):
        """Интеграционный случай из главы 30: сценарий растягивает гласные,
        диктор читает обычным текстом — реплика должна найтись, и найтись точно,
        а не просто перевалить порог за счёт нечёткого посимвольного сравнения
        (без схлопывания растяжки скор был 0.833 — терпимо, но не так, как должно
        быть у слова, произнесённого слово в слово)."""
        result = align_transcript(
            ["- Я попро-о-обую!"],
            _spoken(("Я попробую", 2.0)),
        )

        assert result["lines"][0]["matched"] is True
        assert result["lines"][0]["score"] == pytest.approx(1.0, abs=0.01)


class TestWhenTheLineWasReadSeveralTimes:
    """Диктор читает реплику по нескольку раз подряд, разными интонациями, и оставляет
    все подходы в файле. Это норма ремесла: выбор лучшего — работа монтажёра.

    Для сверки подходы — один факт («сказал»), для монтажа — несколько кусков звука.
    Поэтому реплика остаётся одной строкой, но несёт список подходов.
    """

    def test_three_readings_in_a_row_become_three_takes(self):
        result = align_transcript(
            ["Лиана захватит и оплетёт ваши окна"],
            _spoken(("Лиана захватит и оплетёт ваши окна", 2.6),
                    ("Лиана захватит и оплетёт ваши окна", 2.7),
                    ("Лиана захватит и оплетёт ваши окна", 2.7)),
        )

        assert len(result["lines"][0]["takes"]) == 3

    def test_a_line_read_once_has_one_take(self):
        result = align_transcript(
            ["Я не пойду туда"],
            _spoken(("Я не пойду туда", 3.0)),
        )

        assert len(result["lines"][0]["takes"]) == 1

    def test_takes_are_in_the_order_they_were_spoken(self):
        result = align_transcript(
            ["Я не пойду туда"],
            _spoken(("Я не пойду туда", 3.0), ("Я не пойду туда", 3.0)),
        )

        first, second = result["lines"][0]["takes"]
        assert second["start"] > first["start"]

    def test_the_line_keeps_the_first_take_as_its_own_place(self):
        """`start`/`end` строки читают и сегодняшний сборщик сессии, и отчёты."""
        result = align_transcript(
            ["Я не пойду туда"],
            _spoken(("Я не пойду туда", 3.0), ("Я не пойду туда", 3.0)),
        )

        line = result["lines"][0]
        assert line["start"] == line["takes"][0]["start"]
        assert line["end"] == line["takes"][0]["end"]

    def test_a_line_that_was_not_read_has_no_takes(self):
        result = align_transcript(
            ["Пусть Полумрак рассудит нас обоих по справедливости"],
            _spoken(("совсем другие слова про другое", 3.0)),
        )

        assert result["lines"][0]["matched"] is False
        assert result["lines"][0]["takes"] == []

    def test_the_same_words_far_away_are_not_a_take_of_this_line(self):
        """Иначе реплика, повторённая героем через полглавы, слиплась бы с этой."""
        far = " ".join(["совершенно посторонний текст про другое"] * 6)  # 30 слов > окна
        result = align_transcript(
            ["Я не пойду туда"],
            _spoken(("Я не пойду туда", 3.0), (far, 20.0), ("Я не пойду туда", 3.0)),
        )

        assert len(result["lines"][0]["takes"]) == 1

    def test_the_next_line_is_not_looked_for_among_takes_already_taken(self):
        """Курсор уходит за последний подход. Пока он уходил за первый, подходы 2 и 3
        оставались впереди него и сбивали следующие реплики — это и был механизм
        каскада ложных пропусков."""
        result = align_transcript(
            ["Первая реплика героя", "Вторая реплика героя"],
            _spoken(("Первая реплика героя", 3.0), ("Первая реплика героя", 3.0),
                    ("Вторая реплика героя", 3.0)),
        )

        second = result["lines"][1]
        assert second["matched"] is True
        assert second["takes"][0]["start"] > result["lines"][0]["takes"][-1]["start"]

    def test_a_line_repeated_verbatim_in_the_script_keeps_its_own_place(self):
        """В сценарии есть подряд идущие реплики, совпадающие дословно: «Кто?» → «Кто?»,
        «Я знаю,» → «Я знаю,» — замер по «Крыльям полумрака» даёт десять таких пар.
        Жадный сбор подходов забрал бы у второй её место и объявил бы её пропущенной."""
        result = align_transcript(
            ["Я знаю", "Я знаю"],
            _spoken(("Я знаю", 1.0), ("Я знаю", 1.0)),
        )

        assert [line["matched"] for line in result["lines"]] == [True, True]
        assert len(result["lines"][0]["takes"]) == 1

    def test_a_similar_phrase_nearby_is_not_a_second_reading(self):
        """Подход — повторное чтение тех же слов, а не «похожее рядом». Замер по бою:
        соседние куски речи давали 0.647 и 0.714 — выше порога сверки, и жадный сбор
        глотал их, уводя курсор за собой, а дальше сыпалась вся глава."""
        result = align_transcript(
            ["Мой брат мухи не обидит"],
            _spoken(("Мой брат мухи не обидит", 2.0), ("Мой сват духи не обидел", 2.0)),
        )

        assert len(result["lines"][0]["takes"]) == 1

    def test_a_two_word_line_keeps_one_take_however_often_the_words_recur(self):
        """«Что?..» звучит в главе десятки раз, и повторное чтение от следующего «что»
        не отличить. На бою такая реплика набрала восемь «подходов», и следующая за ней
        была объявлена непроизнесённой."""
        result = align_transcript(
            ["Что", "Мой брат мухи не обидит"],
            _spoken(("Что", 0.5), ("Что", 0.5), ("Что", 0.5),
                    ("Мой брат мухи не обидит", 2.0)),
        )

        assert len(result["lines"][0]["takes"]) == 1
        assert result["lines"][1]["matched"] is True


class TestWhenTheReadingDriftsFarAhead:
    """Реплика бывает дальше окна поиска — например, зацикливание вписало между
    ними сотню лишних слов. Курсор пропуском не двигается (иначе пропущенная
    реплика съела бы поток), поэтому догнать без отдельного шага он не может, и
    один сбой превращается в каскад ложных пропусков до конца главы.
    """

    def test_a_line_beyond_the_window_is_still_found(self):
        junk = " ".join(["лишнее слово из расшифровки"] * 30)  # 120 слов, окно — 80
        expected = ["Я не пойду туда", "Наше королевское величество изволит отдыхать от дел"]
        result = align_transcript(
            expected,
            _spoken(("Я не пойду туда", 3.0), (junk, 60.0),
                    ("Наше королевское величество изволит отдыхать от дел", 5.0)),
        )

        assert [line["matched"] for line in result["lines"]] == [True, True]

    def test_catching_up_does_not_invent_a_line_that_was_never_read(self):
        """Дальний поиск спрашивает строже ближнего: иначе он найдёт «похоже» где угодно."""
        junk = " ".join(["лишнее слово из расшифровки"] * 30)
        result = align_transcript(
            ["Пусть Полумрак рассудит нас обоих по справедливости"],
            _spoken(("совсем другие слова про другое", 3.0), (junk, 60.0)),
        )

        assert result["lines"][0]["matched"] is False


class TestNumbersSpelledOut:
    """Распознавание пишет числа цифрами, сценарий — словами: «А мне 67 тысяч.» против
    «А мне шестьдесят семь тысяч.» Прод, 2026-09-14, гл.19 (роль «Мхивригодаб»): скор
    0.585 < MATCH_THRESHOLD 0.62 — диктору ушло письмо «пропущена реплика» про роль,
    записанную целиком. `_WORD` берёт цифры как обычные слова, а «22-й» режет дефисом
    на обрубки «22» и «й» — ни то, ни другое не совпадает со словом сценария."""

    def test_cardinal_digits_match_the_scripted_words(self):
        assert normalize_words("А мне 67 тысяч.") == normalize_words("А мне шестьдесят семь тысяч.")

    def test_ordinal_with_a_hyphen_suffix_matches_the_scripted_word(self):
        """«у принца 22-й» — гл.28, роль «Зеленщик»: распознавание пишет порядковое
        цифрой с дефисным окончанием, сценарий — словом целиком."""
        assert normalize_words("у принца 22-й") == normalize_words("у принца двадцать второй")

    def test_round_ordinals_with_a_hyphen_suffix(self):
        assert normalize_words("20-й") == ["двадцатый"]
        assert normalize_words("17-й") == ["семнадцатый"]

    def test_a_cardinal_digit_without_a_hyphen_suffix_stays_cardinal(self):
        """«23 уровень» — количественное, не порядковое: дефисного окончания нет."""
        assert normalize_words("23 уровень") == ["двадцать", "три", "уровень"]

    def test_thousands_written_solid_or_with_a_separator_agree(self):
        assert normalize_words("67000") == ["шестьдесят", "семь", "тысяч"]
        assert normalize_words("67 000") == ["шестьдесят", "семь", "тысяч"]

    def test_two_thousand_uses_the_feminine_count_word(self):
        assert normalize_words("2000") == ["две", "тысячи"]

    def test_a_single_digit(self):
        assert normalize_words("1") == ["один"]

    def test_the_incident_line_now_matches_with_a_comfortable_score(self):
        """Тот самый случай из прода: реплика больше не уходит в «пропущена»."""
        result = align_transcript(
            ["А мне шестьдесят семь тысяч."],
            [{"text": "А мне 67 тысяч.", "start": 0, "end": 2}],
        )

        assert result["lines"][0]["matched"] is True
        assert result["lines"][0]["score"] >= 0.9

    def test_a_digit_word_splits_its_time_evenly_between_the_words_it_becomes(self):
        """Одно слово «67» в потоке превращается в два слова сверки — «шестьдесят» и
        «семь»; им нужно делить отведённое слову время пополам, а не получать одно и
        то же начало и конец, как если бы оба произносились одновременно."""
        stream = word_stream([{"text": "67", "start": 0.0, "end": 4.0,
                               "words": [{"word": "67", "start": 1.0, "end": 2.0}]}])

        assert stream == [("шестьдесят", 1.0, 1.5), ("семь", 1.5, 2.0)]

    def test_a_missing_end_does_not_make_times_run_backwards(self):
        """Ревью: если `end` отметки не пришёл или пришёл нулём, а `start` — нет,
        деление на количество слов даёт отрицательный шаг, и времена слов идут в
        обратном порядке. У сегмента без времени должно получиться время без длины
        (start у всех), а не start > end."""
        stream = word_stream([{"text": "67", "start": 2.0, "end": 2.0,
                               "words": [{"word": "67", "start": 2.0, "end": 0.0}]}])

        assert stream == [("шестьдесят", 2.0, 2.0), ("семь", 2.0, 2.0)]


class TestNumberWords:
    """`number_words` — количественное числительное в именительном падеже мужского
    рода, своей маленькой реализацией без новых зависимостей."""

    def test_zero(self):
        assert number_words(0) == ["ноль"]

    def test_small_numbers(self):
        assert number_words(1) == ["один"]
        assert number_words(67) == ["шестьдесят", "семь"]

    def test_thousands(self):
        assert number_words(1000) == ["тысяча"]
        assert number_words(2000) == ["две", "тысячи"]
        assert number_words(5000) == ["пять", "тысяч"]
        assert number_words(21000) == ["двадцать", "одна", "тысяча"]
        assert number_words(67000) == ["шестьдесят", "семь", "тысяч"]

    def test_millions(self):
        assert number_words(1000000) == ["один", "миллион"]
        assert number_words(2000000) == ["два", "миллиона"]
        assert number_words(5000000) == ["пять", "миллионов"]


class TestOrdinalWords:
    """`ordinal_words` — порядковое числительное, именительный падеж мужского рода."""

    def test_named_examples_from_the_incident(self):
        assert ordinal_words(22) == ["двадцать", "второй"]
        assert ordinal_words(20) == ["двадцатый"]
        assert ordinal_words(17) == ["семнадцатый"]
        assert ordinal_words(100) == ["сотый"]
        assert ordinal_words(3) == ["третий"]
        assert ordinal_words(40) == ["сороковой"]
        assert ordinal_words(90) == ["девяностый"]
        assert ordinal_words(200) == ["двухсотый"]
        assert ordinal_words(1000) == ["тысячный"]


class TestHugeDigitStringsDoNotCrash:
    """Ревью после первой правки: телефон или id в расшифровке — цепочка цифр за
    пределами того, что `number_words` умеет разложить по разрядам (в нём только
    единицы/тысячи/миллионы). `_three_digits` индексирует таблицы по цифре 0..9, а
    сотни миллионов уже дают индекс за пределами таблицы — `IndexError`, и одно число
    в услышанном тексте валит сверку всей роли (и переигровку архива вместе с ней).
    Свыше нескольких тысяч цифр `int()` сам откажется — `_spell_numbers` не должен
    даже пытаться туда дойти."""

    def test_a_phone_number_does_not_crash(self):
        assert normalize_words("89171234567") == ["89171234567"]

    def test_a_twelve_digit_number_does_not_crash(self):
        assert normalize_words("100000000000") == ["100000000000"]

    def test_align_transcript_survives_a_phone_number_in_the_heard_text(self):
        result = align_transcript(
            ["Позвони мне, номер 89171234567"],
            [{"text": "Позвони мне номер 89171234567", "start": 0, "end": 3}],
        )

        assert isinstance(result, dict)


class TestOrdinalTablesDoNotHideBehindYo:
    """Ревью: в таблицах порядковых осталась «ё» («четвёртый», «трёхсотый»,
    «четырёхсотый»), а `normalize_words` меняет ё→е ДО перевода чисел в слова — значит
    сценарий, где автор написал «четвёртый» словом, нормализуется в «четвертый», а то
    же число, распознанное цифрой («4-й»), нормализовалось бы в «четвёртый» и никогда
    бы с ним не совпало. Таблицы должны сразу давать уже нормализованную форму."""

    def test_fourth(self):
        assert normalize_words("4-й") == normalize_words("четвёртый")

    def test_fourteenth_is_unaffected(self):
        assert normalize_words("14-й") == normalize_words("четырнадцатый")

    def test_three_hundredth(self):
        assert normalize_words("300-й") == normalize_words("трёхсотый")


class TestOrdinalSuffixDoesNotGlueToTheNextWord:
    """Ревью: дефисное окончание порядкового резалось без границы слова и без запрета
    на пробелы вокруг дефиса.

    «5-метровый» — окончание «м» из старого списка альтернатив совпадало с первой
    буквой «метровый» и склеивало число со всем словом целиком: «пятыйетровый».
    «5-ми», «2-мя», «3-его» — короткая альтернатива («м», «м», «е») совпадала раньше
    более длинной («ми», «мя», «его») из-за порядка перечисления, а не длины, и
    остаток окончания прилипал к результату отдельной буквой.
    «глава 5 - я пошел» — пробел вокруг дефиса раньше допускался, и дефис глотал
    следующее слово «я» как псевдоокончание «-я», теряя местоимение целиком.
    """

    def test_an_unrecognised_hyphen_suffix_falls_back_to_a_cardinal_and_keeps_the_word(self):
        assert normalize_words("5-метровый") == ["пять", "метровый"]

    def test_the_ми_ending_does_not_leave_a_stray_letter(self):
        assert normalize_words("5-ми") == ["пятый"]

    def test_the_мя_ending_does_not_leave_a_stray_letter(self):
        assert normalize_words("2-мя") == ["второй"]

    def test_the_его_ending_is_taken_whole_not_just_its_first_letter(self):
        assert normalize_words("3-его") == ["третий"]

    def test_a_space_around_the_hyphen_is_not_an_ordinal_suffix(self):
        """Дефис с пробелами вокруг — не окончание, а обычное тире; «я» остаётся словом."""
        assert normalize_words("глава 5 - я пошел") == ["глава", "пять", "я", "пошел"]


class TestGroupedThousandsOfAnyLength:
    """Ревью: разделитель тысяч склеивал только ОДНУ пару групп по три цифры, а не
    произвольное их число: «1 000 000» после склейки становился «1000» + « 000»,
    и они читались раздельно как «тысяча ноль». Узкий неразрывный пробел (U+202F,
    его вставляют некоторые распознавания) вообще не входил в список разделителей."""

    def test_a_million_with_two_separator_groups(self):
        assert normalize_words("1 000 000") == number_words(1_000_000)

    def test_two_million_with_two_separator_groups(self):
        assert normalize_words("2 000 000") == number_words(2_000_000)

    def test_a_thousand_with_a_narrow_no_break_space(self):
        assert normalize_words("1 000") == number_words(1000)


class TestOrdinalThousandsDoNotGainAnExtraOne:
    """Ревью: `ordinal_words` для тысяч всегда добавляло счётное слово («одна тысяча»),
    хотя `number_words` для того же случая уже знает, что «1000» — это «тысяча», а не
    «одна тысяча». Год «1941-м» нормализовался в «одна тысяча девятьсот сорок первый»
    и не совпадал со сценарием, где год написан без «одна»."""

    def test_a_year_in_the_thousands_has_no_leading_one(self):
        result = normalize_words("В 1941-м году")

        assert result == ["в", "тысяча", "девятьсот", "сорок", "первый", "году"]

    def test_a_bare_thousand_and_first_has_no_leading_one(self):
        assert ordinal_words(1001) == ["тысяча", "первый"]
