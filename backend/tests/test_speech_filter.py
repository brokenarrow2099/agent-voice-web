from voice_app.speech_filter import SpeakableStream


def collect(chunks: list[str], max_chars: int = 80) -> list[str]:
    stream = SpeakableStream(max_chars=max_chars)
    emitted: list[str] = []
    for chunk in chunks:
        emitted.extend(stream.feed(chunk))
    emitted.extend(stream.flush())
    return emitted


def test_fenced_code_split_across_deltas_is_not_spoken():
    stream = SpeakableStream(max_chars=80)
    assert stream.feed("先解释。``") == ["先解释。"]
    assert stream.feed("`py\nprint('x')\n``") == []
    assert stream.feed("`之后继续。") == ["之后继续。"]
    assert stream.flush() == []


def test_prose_around_multiple_code_blocks_is_preserved():
    spoken = collect(["开头。```sh\necho hi\n```中间。", "```\nx=1\n```结尾"])
    assert spoken == ["开头。", "中间。", "结尾"]


def test_markdown_is_normalized_without_deleting_words():
    spoken = collect(
        [
            "# 标题\n\n- 第一项\n- **第二项**，查看[说明](https://example.com/path)。\n",
            "文件是 `hello.py`。原始地址 https://example.com/a?q=1 不播。",
        ]
    )
    joined = " ".join(spoken)
    assert "标题" in joined
    assert "第一项" in joined
    assert "第二项" in joined
    assert "说明" in joined
    assert "hello.py" in joined
    assert "https://" not in joined
    assert "example.com" not in joined


def test_semantic_symbols_are_spoken_as_natural_chinese():
    assert collect(["✅ **部署完成**。❌ 发布失败。⚠️ 请检查。准备 → 重试。"]) == [
        "已完成 部署完成。",
        "失败 发布失败。",
        "注意 请检查。",
        "准备 接下来 重试。",
    ]


def test_decorative_symbols_and_unknown_emoji_are_not_sent_to_tts():
    assert collect(["• 性能很好🚀，继续优化🧪——不要停:::。"]) == [
        "性能很好，继续优化，不要停。"
    ]


def test_symbol_normalization_is_stable_across_stream_deltas():
    whole = collect(["状态⚠️：检查完成✅。"])
    split = collect(["状态⚠", "️：检查完成✅", "。"])
    assert split == whole == ["状态 注意：检查完成 已完成。"]


def test_markdown_table_is_not_spoken():
    spoken = collect(["| 名称 | 状态 |\n| --- | --- |\n| 语音 | 正常 |\n"])
    assert spoken == []


def test_prose_around_markdown_table_is_preserved():
    spoken = collect(["说明。\n| 名称 | 状态 |\n| --- | --- |\n| 语音 | 正常 |\n继续。"])
    assert spoken == ["说明。", "继续。"]


def test_chinese_and_english_sentence_punctuation_emit_in_order():
    stream = SpeakableStream(max_chars=80)
    assert stream.feed("第一句。第二句！Really?") == ["第一句。", "第二句！", "Really?"]
    assert stream.feed(" Last one. 尾巴") == ["Last one."]
    assert stream.flush() == ["尾巴"]


def test_hard_length_never_drops_text():
    text = "这是一段没有标点但是必须完整朗读的很长正文内容"
    spoken = collect([text[:8], text[8:17], text[17:]], max_chars=10)
    assert "".join(spoken) == text
    assert all(len(part) <= 10 for part in spoken)


def test_unclosed_code_fence_is_dropped_at_final_flush():
    stream = SpeakableStream()
    assert stream.feed("可读。```python\nsecret = 1") == ["可读。"]
    assert stream.flush() == []


def test_split_fence_marker_at_end_is_preserved_if_not_completed():
    stream = SpeakableStream()
    assert stream.feed("正文后有两个反引号``") == []
    assert stream.flush() == ["正文后有两个反引号"]
